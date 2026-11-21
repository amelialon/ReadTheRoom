from __future__ import annotations

from .alerts import AlertStore
from .config import NO, YES, Config
from .dwell import DwellDetector
from .prompts import climate_offer, resolve

IDLE, PROMPT, SENT = "idle", "prompt", "sent"
CLIMATE_KEY = "_climate"


class Session:
    """Glance -> prompt -> confirm -> alert.

    Fed one gaze label per frame. Nothing reaches the caregiver without a
    held YES. A NO or a timeout puts that zone on back-off so the device
    does not keep asking about something the patient is merely looking at.
    """

    def __init__(self, cfg: Config, zones: dict, alerts: AlertStore):
        self.cfg = cfg
        self.zones = zones
        self.alerts = alerts
        self.glance = DwellDetector(cfg.glance_window_s, cfg.win_fraction)
        self.confirm = DwellDetector(cfg.confirm_window_s, cfg.win_fraction)
        self.state = IDLE
        self.state_since = 0.0
        self.prompt: dict | None = None
        self.last_label: str | None = None
        self._overrides: dict = {}  # zone id -> Zone, when the object there changed
        self._backoff_until: dict = {}
        self._climate_next = 0.0
        self._first_seen: float | None = None
        self._last_face: float | None = None

    def reset(self, now: float) -> None:
        self._to_idle(now)

    def zone(self, zone_id: str):
        return self._overrides.get(zone_id, self.zones[zone_id])

    def set_overrides(self, overrides: dict) -> None:
        """Called when the scene camera finds a different object at a spot.
        A changed spot is a new question, so any back-off on it is lifted."""
        for zone_id in set(overrides) | set(self._overrides):
            if overrides.get(zone_id) != self._overrides.get(zone_id):
                self._backoff_until.pop(zone_id, None)
        self._overrides = dict(overrides)

    def mark_face(self, now: float, face_present: bool) -> None:
        if self._first_seen is None:
            self._first_seen = now
        if face_present:
            self._last_face = now

    def update(self, now: float, label: str | None, face_present: bool,
               temp_c: float | None = None) -> None:
        self.mark_face(now, face_present)
        self.last_label = label

        if self.state == SENT:
            if now - self.state_since >= self.cfg.sent_display_s:
                self._to_idle(now)
        elif self.state == IDLE:
            self._update_idle(now, label, face_present, temp_c)
        elif self.state == PROMPT:
            self._update_prompt(now, label)

    def _update_idle(self, now: float, label: str | None, face_present: bool,
                     temp_c: float | None) -> None:
        self.glance.add(now, label if label in self.zones else None)
        zone_id = self.glance.winner(now)
        if zone_id and not self._backed_off(zone_id, now):
            zone = self.zone(zone_id)
            question, message = resolve(zone, temp_c, self.cfg)
            if question != zone.question:
                # Already asking about the room; do not also offer it unprompted.
                self._climate_next = now + self.cfg.climate_reask_s
            self._open_prompt(now, zone_id, question, message, zone.urgent)
            return
        leader, _ = self.glance.progress(now)
        if face_present and leader is None and now >= self._climate_next:
            offer = climate_offer(temp_c, self.cfg)
            if offer:
                self._climate_next = now + self.cfg.climate_reask_s
                self._open_prompt(now, CLIMATE_KEY, offer[0], offer[1], False)

    def _update_prompt(self, now: float, label: str | None) -> None:
        elapsed = now - self.state_since
        if elapsed >= self.cfg.prompt_timeout_s:
            self._decline(now)
            return
        if elapsed < self.cfg.refractory_s:
            return
        self.confirm.add(now, label if label in (YES, NO) else None)
        answer = self.confirm.winner(now)
        if answer == YES:
            self.alerts.create(f"{self.cfg.bed}: patient {self.prompt['message']}",
                               self.prompt["urgent"])
            self.state, self.state_since = SENT, now
        elif answer == NO:
            self._decline(now)

    def _open_prompt(self, now, key, question, message, urgent) -> None:
        self.prompt = {"key": key, "question": question, "message": message,
                       "urgent": urgent}
        self.state, self.state_since = PROMPT, now
        self.confirm.reset()

    def _decline(self, now: float) -> None:
        self._backoff_until[self.prompt["key"]] = now + self.cfg.backoff_s
        self._to_idle(now)

    def _to_idle(self, now: float) -> None:
        self.state, self.state_since = IDLE, now
        self.prompt = None
        self.glance.reset()
        self.confirm.reset()

    def _backed_off(self, key: str, now: float) -> bool:
        return now < self._backoff_until.get(key, 0.0)

    def face_lost_s(self, now: float) -> float:
        since = self._last_face if self._last_face is not None else self._first_seen
        return 0.0 if since is None else max(0.0, now - since)

    def snapshot(self, now: float) -> dict:
        if self.state == PROMPT:
            leader, progress = self.confirm.progress(now)
        elif self.state == IDLE:
            leader, progress = self.glance.progress(now)
            if leader and self._backed_off(leader, now):
                leader, progress = None, 0.0
        else:
            leader, progress = None, 0.0
        return {
            "state": self.state,
            "question": self.prompt["question"] if self.prompt else None,
            "gaze": self.last_label,
            "leader": leader,
            "progress": round(progress, 3),
        }
