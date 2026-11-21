from __future__ import annotations

from .alerts import AlertStore
from .config import NO, YES, Config
from .dwell import DwellDetector
from .prompts import climate_offer, resolve

IDLE, PROMPT, SENT, MENU = "idle", "prompt", "sent", "menu"
_AWAY = "_away"
CLIMATE_KEY = "_climate"


class Session:
    """Glance -> prompt -> confirm -> alert.

    Fed one gaze label per frame. Nothing reaches the caregiver without a
    held YES. A NO or a timeout puts that zone on back-off so the device
    does not keep asking about something the patient is merely looking at.

    Needs that have no object (pain, being moved, the toilet) are in a menu.

    How the menu opens, and why: while idle, the patient holds their gaze on
    the NO side for `menu_open_s`, clearly longer than an answer takes. This
    needs no extra calibration target; every added target makes calibration
    longer and makes the control targets harder to tell apart. The NO side is
    used rather than the YES side on purpose. Inside the menu YES chooses and
    NO moves on, so someone who is only resting their eyes on that side opens
    the menu, steps through it and closes it without sending anything. Had
    the YES side opened it, the same stare would go on to choose the first
    item, which is the urgent one. Two more guards: the hold only counts
    after the gaze has been somewhere else since the screen last changed
    (so the NO that answered a question cannot run on into the menu, and a
    menu that closed cannot reopen in a loop), and every menu step ignores
    input for `refractory_s` and then needs a fresh full hold.
    """

    def __init__(self, cfg: Config, zones: dict, alerts: AlertStore, menu=()):
        self.cfg = cfg
        self.zones = zones
        self.alerts = alerts
        self.glance = DwellDetector(cfg.glance_window_s, cfg.win_fraction)
        self.confirm = DwellDetector(cfg.confirm_window_s, cfg.win_fraction)
        self.menu = list(menu)  # prompts.MenuItem; empty switches the menu off
        self.menu_index = 0
        self._opener = DwellDetector(cfg.menu_open_s, cfg.win_fraction)
        self._rearm = DwellDetector(cfg.menu_rearm_s, cfg.win_fraction)
        self._menu_armed = False
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

    def set_zones(self, zones: dict) -> None:
        """Replace what can be looked at, e.g. when the scene camera sees the
        objects change. Back-offs on objects that are still there are kept."""
        self.zones = zones
        self._overrides = {}
        self._backoff_until = {k: v for k, v in self._backoff_until.items()
                               if k in zones or k.startswith("_")}

    def is_backed_off(self, zone_id: str, now: float) -> bool:
        return self._backed_off(zone_id, now)

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
        elif self.state == MENU:
            self._update_menu(now, label)

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
        if self._update_opener(now, label):
            return
        leader, _ = self.glance.progress(now)
        # Do not put a question up under someone who is opening the menu:
        # their held NO would answer it.
        opening = self._menu_armed and self._opener.progress(now)[0] == NO
        if face_present and leader is None and not opening and now >= self._climate_next:
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

    def _update_opener(self, now: float, label: str | None) -> bool:
        """Idle only. Returns True when the held NO has opened the menu."""
        if not self.menu:
            return False
        # Unreadable frames (None) count as nothing, so a blink while staring
        # at NO neither arms the hold nor breaks it.
        self._rearm.add(now, None if label in (None, NO) else _AWAY)
        if self._rearm.winner(now) == _AWAY:
            # Clearly looking somewhere else: a hold may begin from here, and
            # any earlier one is over, so separate looks never add up.
            self._menu_armed = True
            self._opener.reset()
            return False
        if not self._menu_armed:
            return False
        if label == NO:
            self._opener.add(now, NO)
        elif self._opener.progress(now)[0] == NO:
            self._opener.add(now, None)  # a gap in a hold that has begun
        else:
            self._opener.reset()  # the hold is timed from the first look at NO
        if self._opener.winner(now) != NO:
            return False
        self.menu_index = 0
        self._enter_menu_step(now)
        return True

    def _enter_menu_step(self, now: float) -> None:
        self.state, self.state_since = MENU, now
        self.confirm.reset()

    def _update_menu(self, now: float, label: str | None) -> None:
        elapsed = now - self.state_since
        if elapsed >= self.cfg.menu_step_timeout_s:
            self._to_idle(now)
            return
        if elapsed < self.cfg.refractory_s:
            return
        self.confirm.add(now, label if label in (YES, NO) else None)
        answer = self.confirm.winner(now)
        if answer == YES:
            # Choosing is the confirmation: the item was on screen through the
            # refractory period and a full hold.
            item = self.menu[self.menu_index]
            self.alerts.create(f"{self.cfg.bed}: patient {item.message}", item.urgent)
            self.state, self.state_since = SENT, now
        elif answer == NO:
            if self.menu_index + 1 >= len(self.menu):
                self._to_idle(now)
            else:
                self.menu_index += 1
                self._enter_menu_step(now)

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
        self._opener.reset()
        self._rearm.reset()
        self._menu_armed = False

    def _backed_off(self, key: str, now: float) -> bool:
        return now < self._backoff_until.get(key, 0.0)

    def face_lost_s(self, now: float) -> float:
        since = self._last_face if self._last_face is not None else self._first_seen
        return 0.0 if since is None else max(0.0, now - since)

    def snapshot(self, now: float) -> dict:
        more_progress = 0.0
        if self.state in (PROMPT, MENU):
            leader, progress = self.confirm.progress(now)
        elif self.state == IDLE:
            leader, progress = self.glance.progress(now)
            if leader and self._backed_off(leader, now):
                leader, progress = None, 0.0
            if self._menu_armed:
                more_progress = self._opener.progress(now)[1]
        else:
            leader, progress = None, 0.0
        return {
            "state": self.state,
            "question": self.prompt["question"] if self.prompt else None,
            # Which zone the open question is about (None for room or menu prompts).
            "zone": self.prompt["key"] if self.prompt and self.prompt["key"] in self.zones else None,
            "gaze": self.last_label,
            "leader": leader,
            "progress": round(progress, 3),
            # Opening the menu: whether it exists, and how far the hold has got.
            "more": bool(self.menu),
            "more_progress": round(more_progress, 3),
            "menu": None if self.state != MENU else {
                "index": self.menu_index,
                "count": len(self.menu),
                "label": self.menu[self.menu_index].label,
            },
        }
