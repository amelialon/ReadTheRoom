"""The bedside device's line to the central API (backend/).

Every request the patient confirms is posted up with a UUID this device
makes and keeps, so a retry after a dropped connection or a restart sends
the same request again rather than a duplicate. Acknowledgements made on
the central dashboard come back down and the patient's screen shows them.
A heartbeat every few seconds tells the dashboard the device is alive, and
the room temperature goes up once a minute when there is a sensor.

All of it runs in one background thread that never raises and never blocks
the gaze loop; the device keeps working with no network at all.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone

from .alerts import ACKED, AlertStore


def iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat().replace("+00:00", "Z")


class CentralReporter(threading.Thread):
    def __init__(self, alerts: AlertStore, url: str, token: str, device_id: str, patient_id: str,
                 path: str | None = None, temperature=None, heartbeat_s: float = 5.0,
                 poll_s: float = 1.0, temp_every_s: float = 60.0, timeout_s: float = 6.0):
        super().__init__(name="central", daemon=True)
        self.alerts = alerts
        self.url = url.rstrip("/")
        self.token = token
        self.device_id = device_id
        self.patient_id = patient_id
        self.path = path                      # data/central.json: local alert -> uuid
        self.temperature = temperature        # callable -> degrees C or None
        self.heartbeat_s = heartbeat_s
        self.poll_s = poll_s
        self.temp_every_s = temp_every_s
        self.timeout_s = timeout_s
        self.ids: dict = {}                   # "localid:created" -> uuid
        self.sent: dict = {}                  # uuid -> last state the server accepted
        self.online = False
        self.last_error: str | None = None
        self._halt = threading.Event()
        self._next_heartbeat = 0.0
        self._next_temp = 0.0
        self._load()

    # --- the uuid map ------------------------------------------------------
    @staticmethod
    def key(alert: dict) -> str:
        # The local counter restarts if alerts.json is lost, so the time is
        # part of the key: the same number on another day is another request.
        return f"{alert['id']}:{int(alert['created_at'])}"

    def uuid_for(self, alert: dict) -> str:
        k = self.key(alert)
        if k not in self.ids:
            self.ids[k] = str(uuid.uuid4())
            self._save()
        return self.ids[k]

    def _load(self) -> None:
        if not self.path or not os.path.exists(self.path):
            return
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            self.ids = {str(k): str(v) for k, v in data.get("ids", {}).items()}
            self.sent = {str(k): str(v) for k, v in data.get("sent", {}).items()}
        except (OSError, ValueError, AttributeError):
            self.ids, self.sent = {}, {}

    def _save(self) -> None:
        if not self.path:
            return
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"ids": self.ids, "sent": self.sent}, f)
            os.replace(tmp, self.path)
        except OSError:
            pass

    # --- http ----------------------------------------------------------------
    def request(self, method: str, path: str, body: dict | None = None) -> tuple:
        """(status, json) ; status 0 means the network failed."""
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.url + path, data=data, method=method, headers={
            "Authorization": "Bearer " + self.token, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if raw else {})
        except urllib.error.HTTPError as exc:
            try:
                return exc.code, json.loads(exc.read() or b"{}")
            except ValueError:
                return exc.code, {}
        except (urllib.error.URLError, OSError, ValueError) as exc:
            self.last_error = str(exc)
            return 0, {}

    # --- one round -------------------------------------------------------------
    def sync(self, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        if now >= self._next_heartbeat:
            status, _ = self.request("POST", f"/api/devices/{self.device_id}/heartbeat")
            self.online = status == 200
            self._next_heartbeat = now + self.heartbeat_s
            if status == 0:
                return  # no network: nothing else will get through either
        self.push()
        self.pull()
        if self.temperature is not None and now >= self._next_temp:
            self._next_temp = now + self.temp_every_s
            temp = self.temperature()
            if temp is not None:
                self.request("POST", "/api/sensor-readings", {
                    "device_id": self.device_id, "temperature_c": float(temp),
                    "humidity_pct": None, "observed_at": iso(time.time())})

    def push(self) -> None:
        """Every alert whose state the server has not seen yet."""
        changed = False
        for alert in reversed(self.alerts.list(history=50)):  # oldest first
            rid = self.uuid_for(alert)
            if self.sent.get(rid) == alert["state"]:
                continue
            body = {"id": rid, "local_id": alert["id"], "patient_id": self.patient_id,
                    "device_id": self.device_id, "message": alert["message"],
                    "urgent": bool(alert["urgent"]), "state": alert["state"],
                    "created_at": iso(alert["created_at"]),
                    "acked_at": iso(alert["acked_at"]) if alert.get("acked_at") else None,
                    "category": None, "detected_object": None, "context": {}}
            status, data = self.request("POST", "/api/requests", body)
            if status in (200, 201):
                # A local ack reaches the server only if this is the first
                # post of that request; otherwise acking is the dashboard's
                # PATCH and nothing more can be done from this side.
                self.sent[rid] = alert["state"]
                changed = True
            elif status in (409, 422, 403, 404):
                print(f"Central API refused request {alert['id']} ({status}): {data}", file=sys.stderr)
                self.sent[rid] = alert["state"]  # do not hammer it with the same body
                changed = True
            else:
                break  # network or server trouble: try again next round
        if changed:
            self._save()

    def pull(self) -> None:
        """Acknowledgements made on the central dashboard."""
        status, data = self.request("GET", f"/api/requests?device_id={self.device_id}&state=acked&limit=200")
        if status != 200:
            return
        by_uuid = {v: k for k, v in self.ids.items()}
        open_local = {self.key(a): a for a in self.alerts.list() if a["state"] != ACKED}
        for r in data.get("requests", []):
            k = by_uuid.get(r.get("id"))
            if k in open_local:
                self.alerts.ack(open_local[k]["id"])
                self.sent[r["id"]] = ACKED
                self._save()

    # --- the thread ---------------------------------------------------------------
    def run(self) -> None:
        while not self._halt.is_set():
            try:
                self.sync()
            except Exception as exc:  # the device must keep going whatever happens here
                self.last_error = f"{type(exc).__name__}: {exc}"
            self._halt.wait(self.poll_s)

    def stop(self) -> None:
        self._halt.set()

    def snapshot(self) -> dict:
        return {"url": self.url, "online": self.online, "sent": len(self.sent), "error": self.last_error}
