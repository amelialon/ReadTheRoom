from __future__ import annotations

import json
import os
import sys
import threading
import time

PENDING, ESCALATED, ACKED = "pending", "escalated", "acked"
MAX_HISTORY = 200  # answered alerts kept in memory and on disk


class AlertStore:
    """Confirmed patient requests. Thread-safe, saved to disk on every change.

    An alert that nobody acknowledges within `escalate_after_s` escalates.
    Urgent alerts start escalated. Open alerts found on disk at start-up are
    escalated, because they have already waited through a restart.
    """

    def __init__(self, escalate_after_s: float, path: str | None = None,
                 mono=time.monotonic, wall=time.time):
        self._escalate_after_s = escalate_after_s
        self._path = path
        self._mono = mono
        self._wall = wall
        self._lock = threading.Lock()
        self._alerts: list = []
        self._deadline: dict = {}  # id -> monotonic time at which to escalate
        self._next_id = 1
        self._save_failed = False
        self._load()

    def _load(self) -> None:
        if not self._path or not os.path.exists(self._path):
            return
        try:
            with open(self._path, encoding="utf-8") as f:
                loaded = json.load(f)
        except (OSError, ValueError):
            return
        if not isinstance(loaded, list) or not all(self._valid(a) for a in loaded):
            print(f"Ignoring unreadable alert history at {self._path}", file=sys.stderr)
            return
        self._alerts = loaded
        for alert in self._alerts:
            if alert["state"] == PENDING:
                alert["state"] = ESCALATED
        if self._alerts:
            self._next_id = max(a["id"] for a in self._alerts) + 1

    @staticmethod
    def _valid(alert) -> bool:
        return (isinstance(alert, dict)
                and isinstance(alert.get("id"), int)
                and isinstance(alert.get("message"), str)
                and alert.get("state") in (PENDING, ESCALATED, ACKED)
                and isinstance(alert.get("created_at"), (int, float))
                and isinstance(alert.get("acked_at"), (int, float, type(None)))
                and isinstance(alert.get("urgent"), bool))

    def _save(self) -> None:
        # Memory is the source of truth. A full or read-only disk must never
        # stop a request from reaching the nurse.
        if not self._path:
            return
        tmp = self._path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._alerts, f)
            os.replace(tmp, self._path)
        except OSError as exc:
            if not self._save_failed:
                print(f"Cannot save alerts to {self._path}: {exc}", file=sys.stderr)
            self._save_failed = True
        else:
            self._save_failed = False

    def create(self, message: str, urgent: bool = False) -> dict:
        with self._lock:
            alert = {
                "id": self._next_id,
                "message": message,
                "urgent": urgent,
                "state": ESCALATED if urgent else PENDING,
                "created_at": self._wall(),
                "acked_at": None,
            }
            self._next_id += 1
            self._alerts.append(alert)
            answered = [a for a in self._alerts if a["state"] == ACKED]
            if len(answered) > MAX_HISTORY:
                drop = {a["id"] for a in answered[:-MAX_HISTORY]}
                self._alerts = [a for a in self._alerts if a["id"] not in drop]
            if not urgent:
                self._deadline[alert["id"]] = self._mono() + self._escalate_after_s
            self._save()
            return dict(alert)

    def ack(self, alert_id: int) -> bool:
        with self._lock:
            for alert in self._alerts:
                if alert["id"] == alert_id and alert["state"] != ACKED:
                    alert["state"] = ACKED
                    alert["acked_at"] = self._wall()
                    self._deadline.pop(alert_id, None)
                    self._save()
                    return True
            return False

    def tick(self) -> None:
        """Escalate pending alerts whose deadline has passed."""
        with self._lock:
            now = self._mono()
            due = [i for i, t in self._deadline.items() if now >= t]
            if not due:
                return
            for alert in self._alerts:
                if alert["id"] in due and alert["state"] == PENDING:
                    alert["state"] = ESCALATED
            for i in due:
                del self._deadline[i]
            self._save()

    def clear_answered(self) -> int:
        """Drop answered alerts from the history. Open ones are never removed."""
        with self._lock:
            before = len(self._alerts)
            self._alerts = [a for a in self._alerts if a["state"] != ACKED]
            self._save()
            return before - len(self._alerts)

    def list(self, history: int = 30) -> list:
        """Newest first: every unanswered alert, plus recent answered ones."""
        with self._lock:
            answered = [a["id"] for a in self._alerts if a["state"] == ACKED]
            hidden = set(answered[:-history]) if len(answered) > history else set()
            return [dict(a) for a in reversed(self._alerts) if a["id"] not in hidden]
