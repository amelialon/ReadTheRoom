import json
import os

import pytest

from readtheroom.alerts import ESCALATED, PENDING, AlertStore
from readtheroom.calibration import control_targets
from readtheroom.config import NO, REST, YES, Config
from readtheroom.prompts import MenuItem, Zone, load_menu
from readtheroom.session import IDLE, MENU, PROMPT, SENT, Session

DT = 1 / 15
ZONES_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "config", "zones.json")


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def feed(session, clock, label, seconds, face=True, temp=None):
    for _ in range(round(seconds / DT)):
        clock.t += DT
        session.update(clock.t, label, face, temp)


def feed_until(session, clock, label, state, limit=30.0):
    """Hold a label until the state is reached; returns how long it took."""
    start = clock.t
    while session.state != state:
        assert clock.t - start < limit, f"never reached {state}"
        clock.t += DT
        session.update(clock.t, label, True)
    return clock.t - start


MENU_ITEMS = [
    MenuItem("Pain", "is in PAIN", urgent=True),
    MenuItem("Move me", "wants to be moved"),
    MenuItem("Toilet", "needs the toilet"),
    MenuItem("Something else", "needs something else"),
]


@pytest.fixture
def rig():
    clock = Clock()
    cfg = Config()
    zones = {"water": Zone("water", "water glass", "Thirsty?", "is thirsty")}
    alerts = AlertStore(cfg.escalate_after_s, mono=clock, wall=clock)
    return Session(cfg, zones, alerts, MENU_ITEMS), clock, alerts


def open_menu(session, clock):
    feed(session, clock, REST, 1.0)
    feed_until(session, clock, NO, MENU)


def step(session, clock, label):
    """One deliberate answer inside the menu: wait out the pause, then hold."""
    feed(session, clock, REST, session.cfg.refractory_s + 0.1)
    feed(session, clock, label, session.cfg.confirm_window_s + 0.2)


# --- opening ---------------------------------------------------------------

def test_long_hold_on_no_opens_the_menu(rig):
    session, clock, alerts = rig
    feed(session, clock, REST, 1.0)
    took = feed_until(session, clock, NO, MENU)
    assert session.cfg.menu_open_s <= took < session.cfg.menu_open_s + 0.3
    assert session.snapshot(clock.t)["menu"] == {"index": 0, "count": 4, "label": "Pain"}
    assert alerts.list() == []


def test_a_hold_as_long_as_an_answer_does_not_open_it(rig):
    session, clock, alerts = rig
    feed(session, clock, REST, 1.0)
    feed(session, clock, NO, session.cfg.confirm_window_s * 2)
    assert session.state == IDLE
    feed(session, clock, REST, 1.0)
    feed(session, clock, NO, session.cfg.confirm_window_s * 2)
    assert session.state == IDLE  # two shorter looks do not add up


def test_looking_away_clears_the_opening_progress(rig):
    session, clock, _ = rig
    feed(session, clock, REST, 1.0)
    feed(session, clock, NO, 2.0)
    assert session.snapshot(clock.t)["more_progress"] > 0.5
    feed(session, clock, REST, 0.8)
    assert session.snapshot(clock.t)["more_progress"] == 0.0
    took = feed_until(session, clock, NO, MENU)
    assert took >= session.cfg.menu_open_s


def test_blinks_do_not_break_the_opening_hold(rig):
    session, clock, _ = rig
    feed(session, clock, REST, 1.0)
    for i in range(round(4.0 / DT)):
        clock.t += DT
        session.update(clock.t, None if i % 15 < 2 else NO, True)
    assert session.state == MENU


def test_holding_yes_or_centre_never_opens_it(rig):
    session, clock, alerts = rig
    for label in (YES, REST, None, "elsewhere"):
        feed(session, clock, label, 20.0)
        assert session.state == IDLE
    assert alerts.list() == []


def test_flickering_gaze_does_not_open_it(rig):
    session, clock, alerts = rig
    feed(session, clock, REST, 1.0)
    for i in range(300):
        clock.t += DT
        session.update(clock.t, NO if i % 3 else REST, True)
    assert session.state == IDLE


def test_menu_is_off_without_items():
    clock = Clock()
    cfg = Config()
    alerts = AlertStore(cfg.escalate_after_s, mono=clock, wall=clock)
    session = Session(cfg, {"water": Zone("water", "w", "q", "m")}, alerts)
    feed(session, clock, REST, 1.0)
    feed(session, clock, NO, 20.0)
    assert session.state == IDLE
    assert session.snapshot(clock.t)["more"] is False


def test_opening_shows_progress_only_once_gaze_has_been_elsewhere(rig):
    session, clock, _ = rig
    feed(session, clock, NO, 1.0)  # device starts with the patient looking at NO
    assert session.snapshot(clock.t)["more_progress"] == 0.0
    feed(session, clock, REST, 1.0)
    feed(session, clock, NO, 1.5)
    snap = session.snapshot(clock.t)
    assert snap["more"] is True and 0.3 < snap["more_progress"] < 1.0
    assert snap["leader"] is None  # the object bar is not used for this


# --- choosing --------------------------------------------------------------

def test_choosing_the_first_item_sends_an_urgent_alert(rig):
    session, clock, alerts = rig
    open_menu(session, clock)
    step(session, clock, YES)
    assert session.state == SENT
    (alert,) = alerts.list()
    assert alert["message"] == "Bed 4: patient is in PAIN"
    assert alert["urgent"] is True and alert["state"] == ESCALATED
    feed(session, clock, REST, session.cfg.sent_display_s + 0.2)
    assert session.state == IDLE


def test_no_moves_to_the_next_item_and_yes_chooses_it(rig):
    session, clock, alerts = rig
    open_menu(session, clock)
    step(session, clock, NO)
    assert session.snapshot(clock.t)["menu"]["label"] == "Move me"
    step(session, clock, NO)
    assert session.snapshot(clock.t)["menu"] == {"index": 2, "count": 4, "label": "Toilet"}
    assert alerts.list() == []
    step(session, clock, YES)
    (alert,) = alerts.list()
    assert alert["message"] == "Bed 4: patient needs the toilet"
    assert alert["urgent"] is False and alert["state"] == PENDING


def test_no_on_the_last_item_closes_without_sending(rig):
    session, clock, alerts = rig
    open_menu(session, clock)
    for _ in range(3):
        step(session, clock, NO)
        assert session.state == MENU
    step(session, clock, NO)
    assert session.state == IDLE
    assert session.snapshot(clock.t)["menu"] is None
    assert alerts.list() == []


def test_menu_closes_when_nothing_is_chosen(rig):
    session, clock, alerts = rig
    open_menu(session, clock)
    feed(session, clock, REST, session.cfg.menu_step_timeout_s - 0.5)
    assert session.state == MENU
    feed(session, clock, REST, 1.0)
    assert session.state == IDLE
    assert alerts.list() == []


def test_each_item_gets_its_own_time_limit(rig):
    session, clock, _ = rig
    open_menu(session, clock)
    feed(session, clock, REST, session.cfg.menu_step_timeout_s - 3.0)
    feed(session, clock, NO, session.cfg.confirm_window_s + 0.2)
    assert session.snapshot(clock.t)["menu"]["index"] == 1
    feed(session, clock, REST, session.cfg.menu_step_timeout_s - 1.0)
    assert session.state == MENU


# --- no false alerts -------------------------------------------------------

def test_input_right_after_each_menu_screen_is_ignored(rig):
    session, clock, alerts = rig
    open_menu(session, clock)
    feed(session, clock, YES, session.cfg.refractory_s - 0.1)
    assert session.snapshot(clock.t)["progress"] == 0.0
    feed(session, clock, REST, 3.0)
    assert session.state == MENU and alerts.list() == []

    feed(session, clock, REST, session.cfg.refractory_s + 0.1)
    while session.snapshot(clock.t)["menu"]["index"] == 0:  # NO, up to the moment it moves on
        feed(session, clock, NO, DT)
    feed(session, clock, YES, session.cfg.refractory_s - 0.1)
    assert session.snapshot(clock.t)["progress"] == 0.0
    feed(session, clock, REST, 3.0)
    assert session.snapshot(clock.t)["menu"]["index"] == 1 and alerts.list() == []


def test_a_brief_look_at_yes_in_the_menu_sends_nothing(rig):
    session, clock, alerts = rig
    open_menu(session, clock)
    feed(session, clock, REST, session.cfg.refractory_s + 0.1)
    for _ in range(3):
        feed(session, clock, YES, session.cfg.confirm_window_s * 0.6)
        feed(session, clock, REST, 0.8)
    assert alerts.list() == []


def test_staring_at_no_forever_never_sends_anything(rig):
    """Someone resting their eyes on the NO side opens the menu, steps through
    it and closes it. It must not send, and must not open again and again."""
    session, clock, alerts = rig
    feed(session, clock, REST, 1.0)
    opened = 0
    was = IDLE
    for _ in range(round(300 / DT)):
        clock.t += DT
        session.update(clock.t, NO, True)
        if session.state == MENU and was == IDLE:
            opened += 1
        assert session.state in (IDLE, MENU)
        was = session.state
    assert alerts.list() == []
    assert opened == 1
    assert session.state == IDLE


def test_staring_at_yes_forever_never_sends_anything(rig):
    session, clock, alerts = rig
    feed(session, clock, REST, 1.0)
    feed(session, clock, YES, 300.0)
    assert session.state == IDLE and alerts.list() == []


def test_blinks_while_staring_at_no_do_not_reopen_the_menu(rig):
    session, clock, alerts = rig
    open_menu(session, clock)
    feed(session, clock, REST, session.cfg.menu_step_timeout_s + 0.5)  # closes
    assert session.state == IDLE
    feed(session, clock, NO, 2.0)
    # Never looked away since it closed, apart from frames with no reading.
    for i in range(round(60 / DT)):
        clock.t += DT
        session.update(clock.t, None if i % 20 < 3 else NO, True)
        assert session.state == IDLE
    assert alerts.list() == []


def test_the_no_that_answers_a_question_does_not_run_on_into_the_menu(rig):
    session, clock, alerts = rig
    feed_until(session, clock, "water", PROMPT)
    feed(session, clock, REST, session.cfg.refractory_s + 0.1)
    feed_until(session, clock, NO, IDLE)
    feed(session, clock, NO, 30.0)  # still looking at NO afterwards
    assert session.state == IDLE
    assert alerts.list() == []


def test_the_yes_that_answers_a_question_does_not_choose_from_the_menu(rig):
    session, clock, alerts = rig
    feed_until(session, clock, "water", PROMPT)
    feed(session, clock, REST, session.cfg.refractory_s + 0.1)
    feed_until(session, clock, YES, SENT)
    feed(session, clock, YES, 30.0)
    assert session.state == IDLE
    assert len(alerts.list()) == 1  # only the water request


def test_menu_can_be_opened_again_after_looking_away(rig):
    session, clock, _ = rig
    open_menu(session, clock)
    feed(session, clock, REST, session.cfg.menu_step_timeout_s + 1.5)
    assert session.state == IDLE
    feed_until(session, clock, NO, MENU)
    assert session.snapshot(clock.t)["menu"]["index"] == 0  # starts from the top


def test_face_hidden_in_the_menu_sends_nothing_and_closes(rig):
    session, clock, alerts = rig
    open_menu(session, clock)
    feed(session, clock, None, session.cfg.menu_step_timeout_s + 0.5, face=False)
    assert session.state == IDLE and alerts.list() == []


def test_a_stalled_camera_does_not_complete_a_choice(rig):
    session, clock, alerts = rig
    open_menu(session, clock)
    feed(session, clock, REST, session.cfg.refractory_s + 0.1)
    feed(session, clock, YES, 0.6)
    clock.t += 2.0  # no frames at all
    feed(session, clock, YES, 0.6)
    assert alerts.list() == []


def test_room_question_does_not_appear_under_a_held_no(rig):
    """A question that pops up while the patient holds NO for the menu would
    be answered by that same hold."""
    session, clock, alerts = rig
    feed(session, clock, REST, 0.6, temp=22.0)
    feed(session, clock, NO, 1.0, temp=22.0)
    while session.state == IDLE:
        clock.t += DT
        session.update(clock.t, NO, True, 30.0)  # room turns warm mid-hold
    assert session.state == MENU
    assert alerts.list() == []


def test_objects_still_work_with_the_menu_on(rig):
    session, clock, alerts = rig
    feed_until(session, clock, "water", PROMPT)
    feed(session, clock, REST, session.cfg.refractory_s + 0.1)
    feed_until(session, clock, YES, SENT)
    assert alerts.list()[0]["message"] == "Bed 4: patient is thirsty"


def test_reset_closes_the_menu(rig):
    session, clock, _ = rig
    open_menu(session, clock)
    session.reset(clock.t)
    assert session.state == IDLE and session.snapshot(clock.t)["menu"] is None


# --- configuration ---------------------------------------------------------

def test_shipped_menu_loads_with_pain_first_and_urgent():
    menu = load_menu(ZONES_FILE)
    assert [m.label for m in menu] == ["Pain", "Move me", "Toilet", "Something else"]
    assert [m.urgent for m in menu] == [True, False, False, False]
    assert all(m.message for m in menu)


def test_menu_file_validation(tmp_path):
    path = tmp_path / "zones.json"
    path.write_text(json.dumps({"zones": []}))
    assert load_menu(str(path)) == []
    path.write_text(json.dumps({"menu": [{"label": "Pain", "message": ""}]}))
    with pytest.raises(ValueError):
        load_menu(str(path))
    path.write_text(json.dumps({"menu": [{"label": "Pain", "message": "m", "urgent": "yes"}]}))
    with pytest.raises(ValueError):
        load_menu(str(path))
    path.write_text(json.dumps({"menu": [{"label": "Pain"}]}))
    with pytest.raises(TypeError):
        load_menu(str(path))


def test_opening_hold_is_clearly_longer_than_an_answer():
    cfg = Config()
    assert cfg.menu_open_s >= 2 * cfg.confirm_window_s


# --- up/down answers -------------------------------------------------------

def test_default_answers_are_left_and_right():
    assert Config().answer_mode == "horizontal"
    targets = dict(control_targets())
    assert targets[YES] == "Look at YES on the left"
    assert targets[NO] == "Look at NO on the right"


def test_vertical_mode_changes_only_the_instructions():
    targets = control_targets("vertical")
    assert [t[0] for t in targets] == [REST, YES, NO]
    assert dict(targets)[YES] == "Look at YES at the top"
    assert dict(targets)[NO] == "Look at NO at the bottom"
    with pytest.raises(ValueError):
        control_targets("diagonal")


def test_app_uses_the_answer_mode(tmp_path):
    from readtheroom.main import App
    from readtheroom.server import Hub

    zones = {"water": Zone("water", "water glass", "Thirsty?", "is thirsty")}
    hub = Hub()
    app = App(Config(answer_mode="vertical"), zones, str(tmp_path), hub,
              simulate=True, menu=MENU_ITEMS)
    instructions = [text for _, text in app.calibrator.targets]
    assert "Look at YES at the top" in instructions
    assert "Look at the water glass" in instructions
    assert app._calibration_path.endswith("calibration-vertical.json")
    app.tick(100.0)
    _, state = hub.wait(0, 0.1)
    assert state["status"]["answer_mode"] == "vertical"
    assert state["session"]["more"] is True

    flat = App(Config(gaze_mode="trained"), zones, str(tmp_path), Hub(), simulate=True)
    assert flat._calibration_path.endswith("calibration.json")
    assert "Look at YES on the left" in [text for _, text in flat.calibrator.targets]
