import numpy as np
import pytest

from readtheroom.alerts import ACKED, ESCALATED, PENDING, AlertStore
from readtheroom.calibration import Calibrator
from readtheroom.config import NO, REST, YES, Config
from readtheroom.dwell import DwellDetector
from readtheroom.features import Ema, head_angles, iris_offset
from readtheroom.prompts import Zone, climate_offer, load_zones, resolve
from readtheroom.sensor import _crc8, decode_sht3x
from readtheroom.session import IDLE, PROMPT, SENT, Session
from readtheroom.zones import ZoneClassifier

DT = 1 / 15


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def feed(session, clock, label, seconds, face=True, temp=None):
    for _ in range(round(seconds / DT)):
        clock.t += DT
        session.update(clock.t, label, face, temp)


@pytest.fixture
def rig():
    clock = Clock()
    cfg = Config()
    zones = {
        "water": Zone("water", "water glass", "Thirsty?", "is thirsty"),
        "pain": Zone("pain", "PAIN card", "In pain?", "is in PAIN", urgent=True),
        "blanket": Zone("blanket", "blanket", "Blanket?", "wants the blanket", climate=True),
    }
    alerts = AlertStore(cfg.escalate_after_s, mono=clock, wall=clock)
    return Session(cfg, zones, alerts), clock, alerts


# --- dwell -----------------------------------------------------------------

def test_dwell_needs_a_full_window():
    d = DwellDetector(1.5, 0.8)
    t = 0.0
    while t < 1.4:
        d.add(t, "a")
        assert d.winner(t) is None
        t += DT
    d.add(1.6, "a")
    assert d.winner(1.6) == "a"


def test_dwell_ignores_flicker_and_mixed_gaze():
    d = DwellDetector(1.5, 0.8)
    for i in range(60):
        t = i * DT
        d.add(t, "a" if i % 2 else "b")
        assert d.winner(t) is None


def test_dwell_tolerates_a_few_dropped_frames():
    d = DwellDetector(1.5, 0.8)
    for i in range(40):
        d.add(i * DT, None if i % 10 == 0 else "a")
    assert d.winner(39 * DT) == "a"


def test_dwell_does_not_win_on_stale_samples():
    d = DwellDetector(1.5, 0.8)
    for i in range(30):
        d.add(i * DT, "a")
    assert d.winner(30 * DT + 5.0) is None


def test_dwell_restarts_after_a_pause_in_samples():
    d = DwellDetector(1.5, 0.8)
    for i in range(30):
        d.add(i * DT, None)
    t = 30 * DT + 20.0  # long stall, then a few frames on a target
    for i in range(6):
        d.add(t + i * DT, "a")
        assert d.winner(t + i * DT) is None
    for i in range(6, 26):
        d.add(t + i * DT, "a")
    assert d.winner(t + 25 * DT) == "a"


def test_dwell_works_at_low_frame_rates():
    d = DwellDetector(1.2, 0.8)
    for i in range(6):
        d.add(i * 0.3, "yes")  # about 3 frames per second
    assert d.winner(1.5) == "yes"


# --- session ---------------------------------------------------------------

def test_glance_then_yes_sends_alert(rig):
    s, clock, alerts = rig
    feed(s, clock, REST, 1.0)
    feed(s, clock, "water", 1.7)
    assert s.state == PROMPT and s.prompt["question"] == "Thirsty?"
    feed(s, clock, YES, 2.5)
    assert s.state == SENT
    (a,) = alerts.list()
    assert a["message"] == "Bed 4: patient is thirsty" and a["state"] == PENDING
    feed(s, clock, REST, 4.5)
    assert s.state == IDLE


def test_yes_during_refractory_is_ignored(rig):
    s, clock, alerts = rig
    feed(s, clock, "water", 1.7)
    assert s.state == PROMPT
    feed(s, clock, YES, 0.9)
    assert s.state == PROMPT and alerts.list() == []


def test_no_backs_off_the_zone(rig):
    s, clock, alerts = rig
    feed(s, clock, "water", 1.7)
    feed(s, clock, NO, 2.5)
    assert s.state == IDLE and alerts.list() == []
    feed(s, clock, "water", 5.0)
    assert s.state == IDLE  # still on back-off
    feed(s, clock, "pain", 1.7)
    assert s.state == PROMPT  # other zones still work
    feed(s, clock, NO, 2.5)
    clock.t += s.cfg.backoff_s
    feed(s, clock, "water", 1.7)
    assert s.state == PROMPT


def test_prompt_times_out_without_sending(rig):
    s, clock, alerts = rig
    feed(s, clock, "water", 1.7)
    feed(s, clock, REST, 10.5)
    assert s.state == IDLE and alerts.list() == []


def test_looking_at_the_object_never_confirms(rig):
    s, clock, alerts = rig
    feed(s, clock, "water", 9.0)
    assert s.state == PROMPT and alerts.list() == []


def test_urgent_zone_escalates_immediately(rig):
    s, clock, alerts = rig
    feed(s, clock, "pain", 1.7)
    feed(s, clock, YES, 2.5)
    assert alerts.list()[0]["state"] == ESCALATED


def test_unknown_and_control_labels_never_prompt(rig):
    s, clock, _ = rig
    for label in (None, REST, YES, NO, "not-a-zone"):
        feed(s, clock, label, 3.0)
        assert s.state == IDLE


def test_climate_zone_adapts_to_room_temperature(rig):
    s, clock, alerts = rig
    feed(s, clock, "blanket", 1.7, temp=17.0)
    assert s.prompt["question"] == "Are you cold?"
    feed(s, clock, NO, 2.5, temp=17.0)
    feed(s, clock, REST, 5.0, temp=17.0)
    assert s.state == IDLE  # a NO is not followed by the same question unprompted


def test_room_offers_unprompted_but_does_not_nag(rig):
    s, clock, alerts = rig
    feed(s, clock, REST, 0.5, temp=22.0)
    assert s.state == IDLE
    feed(s, clock, REST, 0.5, temp=28.0)
    assert s.state == PROMPT and "too warm" in s.prompt["question"]
    feed(s, clock, YES, 2.5, temp=28.0)
    assert "too warm" in alerts.list()[0]["message"]
    feed(s, clock, REST, 10.0, temp=28.0)
    assert s.state == IDLE  # does not re-ask straight away
    clock.t += s.cfg.climate_reask_s
    feed(s, clock, REST, 0.5, temp=28.0)
    assert s.state == PROMPT


def test_room_does_not_ask_an_empty_bed(rig):
    s, clock, _ = rig
    feed(s, clock, None, 3.0, face=False, temp=28.0)
    assert s.state == IDLE


def test_face_lost_timer(rig):
    s, clock, _ = rig
    feed(s, clock, REST, 1.0)
    assert s.face_lost_s(clock.t) < 0.1
    feed(s, clock, None, 31.0, face=False)
    assert s.face_lost_s(clock.t) > 30


# --- alerts ----------------------------------------------------------------

def test_alert_escalates_then_acks(tmp_path):
    clock = Clock()
    path = str(tmp_path / "alerts.json")
    store = AlertStore(60, path, mono=clock, wall=clock)
    a = store.create("Bed 4: patient is thirsty")
    clock.t += 59
    store.tick()
    assert store.list()[0]["state"] == PENDING
    clock.t += 2
    store.tick()
    assert store.list()[0]["state"] == ESCALATED
    assert store.ack(a["id"]) and not store.ack(a["id"]) and not store.ack(999)
    assert store.list()[0]["state"] == ACKED


def test_acked_alert_does_not_escalate():
    clock = Clock()
    store = AlertStore(60, mono=clock, wall=clock)
    a = store.create("x")
    store.ack(a["id"])
    clock.t += 120
    store.tick()
    assert store.list()[0]["state"] == ACKED


def test_open_alerts_survive_restart_and_escalate(tmp_path):
    clock = Clock()
    path = str(tmp_path / "alerts.json")
    first = AlertStore(60, path, mono=clock, wall=clock)
    first.create("one")
    done = first.create("two")
    first.ack(done["id"])
    second = AlertStore(60, path, mono=clock, wall=clock)
    states = {a["message"]: a["state"] for a in second.list()}
    assert states == {"one": ESCALATED, "two": ACKED}
    assert second.create("three")["id"] == 3


def test_corrupt_alert_file_does_not_crash(tmp_path):
    path = tmp_path / "alerts.json"
    path.write_text("{not json")
    store = AlertStore(60, str(path))
    assert store.list() == []
    store.create("x")


@pytest.mark.parametrize("content", ["{}", "null", "[1]", '[{"id": 1}]'])
def test_wrong_shaped_alert_file_does_not_crash(tmp_path, content):
    path = tmp_path / "alerts.json"
    path.write_text(content)
    store = AlertStore(60, str(path))
    assert store.list() == []
    assert store.create("x")["id"] == 1


def test_unwritable_disk_still_delivers_one_alert(tmp_path):
    clock = Clock()
    store = AlertStore(60, str(tmp_path / "missing-dir" / "alerts.json"), mono=clock, wall=clock)
    a = store.create("x")
    assert [x["id"] for x in store.list()] == [a["id"]]
    clock.t += 61
    store.tick()
    assert store.list()[0]["state"] == ESCALATED
    assert store.ack(a["id"])


def test_unanswered_alert_stays_listed_behind_many_newer_ones():
    clock = Clock()
    store = AlertStore(60, mono=clock, wall=clock)
    first = store.create("still waiting")
    for i in range(300):
        store.ack(store.create(f"later {i}")["id"])
    listed = store.list()
    assert first["id"] in [a["id"] for a in listed]
    assert len(listed) == 31


# --- classifier ------------------------------------------------------------

def blobs(rng, centres, n=30, noise=0.05):
    X, y = [], []
    for label, centre in centres.items():
        X.append(rng.normal(centre, noise, size=(n, len(centre))))
        y += [label] * n
    return np.vstack(X), y


CENTRES = {"rest": [0, 0, 0.5], "yes": [-1, 0, 0.5], "no": [1, 0, 0.5], "water": [-2, 1, 0.5]}


def test_classifier_separates_zones_and_rejects_elsewhere():
    rng = np.random.default_rng(0)
    clf = ZoneClassifier()
    clf.fit(*blobs(rng, CENTRES))
    for label, centre in CENTRES.items():
        assert clf.predict(rng.normal(centre, 0.05)) == label
    assert clf.predict([5.0, -4.0, 0.5]) is None
    assert clf.predict([np.nan, 0, 0]) is None
    assert clf.predict([0, 0]) is None
    assert clf.predict(None) is None
    assert min(clf.self_check().values()) > 0.9


def test_classifier_self_check_flags_overlapping_zones():
    rng = np.random.default_rng(1)
    clf = ZoneClassifier()
    clf.fit(*blobs(rng, {"a": [0, 0], "b": [0.01, 0], "c": [3, 3]}))
    check = clf.self_check()
    assert check["c"] > 0.9 and check["a"] < 0.8


def test_classifier_round_trips_through_disk(tmp_path):
    rng = np.random.default_rng(2)
    clf = ZoneClassifier()
    clf.fit(*blobs(rng, CENTRES))
    path = str(tmp_path / "cal.json")
    clf.save(path)
    loaded = ZoneClassifier()
    loaded.load(path)
    assert loaded.labels == set(CENTRES)
    for centre in CENTRES.values():
        assert loaded.predict(centre) == clf.predict(centre)


def test_classifier_rejects_bad_training_data():
    clf = ZoneClassifier()
    with pytest.raises(ValueError):
        clf.fit(np.zeros((10, 3)), ["a"] * 10)
    assert clf.predict([0, 0, 0]) is None


# --- calibration -----------------------------------------------------------

def test_calibrator_records_only_after_settling():
    cal = Calibrator([("rest", "centre"), ("water", "glass")], 1.5, 2.0)
    t = 0.0
    cal.start(t)
    done = False
    while not done:
        t += DT
        done = cal.update(t, np.array([t]))
        assert t < 10
    assert cal.error is None and not cal.active
    assert set(cal.y) == {"rest", "water"}
    first_rest = cal.X[0][0]
    assert first_rest >= 1.5


def test_calibrator_fails_when_face_is_missing():
    cal = Calibrator([("rest", "centre"), ("water", "glass")], 1.5, 2.0)
    t = 0.0
    cal.start(t)
    done = False
    while not done:
        t += DT
        done = cal.update(t, None)
    assert cal.error and "centre" in cal.error


# --- small pieces ----------------------------------------------------------

def test_iris_offset_and_head_angles():
    assert iris_offset((0.5, 0.0), (0, 0), (1, 0)) == pytest.approx((0.5, 0.0))
    along, across = iris_offset((0.25, 0.1), (0, 0), (1, 0))
    assert along == pytest.approx(0.25) and across == pytest.approx(-0.1)
    assert iris_offset((1, 1), (0, 0), (0, 0)) == (0.5, 0.0)
    assert head_angles(np.eye(4)) == pytest.approx((0, 0, 0))


def test_ema_restarts_after_gap():
    ema = Ema(0.5)
    assert ema([2.0])[0] == 2.0
    assert ema([4.0])[0] == 3.0
    assert ema(None) is None
    assert ema([10.0])[0] == 10.0


def test_sht3x_decoding():
    raw = [0x66, 0x66, 0, 0x80, 0x00, 0]
    raw[2], raw[5] = _crc8(raw[0:2]), _crc8(raw[3:5])
    t, h = decode_sht3x(raw)
    assert t == pytest.approx(25.0, abs=0.01) and h == pytest.approx(50.0, abs=0.01)
    assert _crc8([0xBE, 0xEF]) == 0x92  # check value from the sensor datasheet
    raw[2] ^= 1
    assert decode_sht3x(raw) is None


def test_zone_file_validation(tmp_path):
    import json
    good = tmp_path / "z.json"
    good.write_text(json.dumps({"zones": [{"id": "w", "label": "l", "question": "q", "message": "m"}]}))
    assert load_zones(str(good))["w"].urgent is False
    bad = tmp_path / "b.json"
    bad.write_text(json.dumps({"zones": [{"id": "yes", "label": "l", "question": "q", "message": "m"}]}))
    with pytest.raises(ValueError):
        load_zones(str(bad))


def test_climate_text():
    cfg = Config()
    zone = Zone("b", "blanket", "Blanket?", "wants blanket", climate=True)
    assert resolve(zone, 22.0, cfg) == ("Blanket?", "wants blanket")
    assert resolve(zone, None, cfg) == ("Blanket?", "wants blanket")
    assert resolve(zone, 30.0, cfg)[0] == "Are you too warm?"
    assert climate_offer(22.0, cfg) is None
    assert climate_offer(30.2, cfg)[0] == "The room is 30°C. Are you too warm?"
