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


def test_repeat_check_exposes_targets_the_patient_did_not_look_at():
    # First look: four distinct places. Second look: "water" was really the
    # screen centre both times it mattered, so it must score badly.
    rng = np.random.default_rng(3)
    clf = ZoneClassifier()
    clf.fit(*blobs(rng, CENTRES))
    X2, y2 = blobs(rng, {**CENTRES, "water": [3, -3, 0.5]})
    score = clf.score(X2, y2)
    assert score["water"] < 0.2 and score["yes"] > 0.9 and score["no"] > 0.9


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
        assert t < 20
    assert cal.error is None and not cal.active
    assert set(cal.y) == {"rest", "water"}
    assert set(cal.passes) == {0, 1} and len(cal.passes) == len(cal.y)
    assert [cal.y[i] for i, p in enumerate(cal.passes) if p == 1][0] == "rest"
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


# --- detector --------------------------------------------------------------

def test_detector_decodes_boxes_back_to_photo_coordinates(tmp_path):
    cv2 = pytest.importorskip("cv2")
    pytest.importorskip("onnxruntime")
    import json
    from readtheroom.detector import SceneDetector

    det = SceneDetector.__new__(SceneDetector)
    det._cv2, det.words, det.size = cv2, ["bottle", "snack"], (640, 640)
    det.min_score, det.overlap, det._input = 0.25, 0.5, "images"

    class FakeSession:
        def run(self, _, feeds):
            assert feeds["images"].shape == (1, 3, 640, 640)
            out = np.zeros((1, 6, 8400), dtype=np.float32)
            # A 1280x720 photo is scaled by 0.5 and padded 140 px top and bottom.
            out[0, :, 0] = [320, 320, 100, 50, 0.9, 0.1]    # bottle
            out[0, :, 1] = [322, 321, 100, 50, 0.6, 0.1]    # same bottle, overlapping
            out[0, :, 2] = [100, 200, 40, 40, 0.05, 0.7]    # snack
            out[0, :, 3] = [500, 500, 40, 40, 0.2, 0.1]     # below the score cut-off
            return [out]

    det._session = FakeSession()
    found = det.detect(np.zeros((720, 1280, 3), np.uint8))
    assert [f["label"] for f in found] == ["bottle", "snack"]
    assert found[0]["box"] == [540.0, 310.0, 740.0, 410.0]
    assert found[1]["box"] == [160.0, 80.0, 240.0, 160.0]


def test_detector_runs_a_4_by_3_model_on_a_4_by_3_photo_without_padding():
    # The C270's whole 640x480 view fills a 480x640 model, so boxes map 1:1.
    cv2 = pytest.importorskip("cv2")
    from readtheroom.detector import SceneDetector

    det = SceneDetector.__new__(SceneDetector)
    det._cv2, det.words, det.size = cv2, ["bottle"], (480, 640)
    det.min_score, det.overlap, det._input = 0.25, 0.5, "images"

    class FakeSession:
        def run(self, _, feeds):
            assert feeds["images"].shape == (1, 3, 480, 640)
            out = np.zeros((1, 5, 6300), dtype=np.float32)
            out[0, :, 0] = [600, 50, 40, 20, 0.9]  # near the frame's top-right corner
            return [out]

    det._session = FakeSession()
    found = det.detect(np.zeros((480, 640, 3), np.uint8))
    assert found[0]["box"] == [580.0, 40.0, 620.0, 60.0]


def test_opencv_fallback_gives_the_same_output_as_onnxruntime():
    # The Pi runs the model through OpenCV; it must agree with ONNX Runtime.
    import os
    cv2 = pytest.importorskip("cv2")
    ort = pytest.importorskip("onnxruntime")
    from readtheroom.detector import _OpenCVSession

    model = os.path.join(os.path.dirname(__file__), "..", "models", "scene.onnx")
    if not os.path.exists(model):
        pytest.skip("models/scene.onnx not exported")
    session = ort.InferenceSession(model, providers=["CPUExecutionProvider"])
    blob = np.random.default_rng(0).random(session.get_inputs()[0].shape, dtype=np.float32)
    expected = session.run(None, {session.get_inputs()[0].name: blob})[0]
    got = _OpenCVSession(cv2, model).run(None, {"images": blob})[0]
    assert got.shape == expected.shape
    assert np.abs(got - expected).max() < 0.05


# --- scene tracking --------------------------------------------------------

NEEDS = {"cup": {"question": "Want a drink?", "message": "wants a drink"},
         "book": {"question": "Want to read?", "message": "wants their book"},
         "water bottle": {"question": "Thirsty?", "message": "is thirsty"}}
SPOT_A, SPOT_B = [100, 100, 200, 300], [600, 100, 700, 300]


def det(label, box, score=0.8):
    return {"label": label, "score": score, "box": box}


@pytest.fixture
def tracker():
    from readtheroom.scene import SceneTracker
    zones = {
        "water": Zone("water", "water bottle", "Thirsty?", "is thirsty", detect="water bottle"),
        "snack": Zone("snack", "snack packet", "Hungry?", "is hungry", detect="snack packet"),
        "pain": Zone("pain", "PAIN card", "In pain?", "is in PAIN", urgent=True),
    }
    return SceneTracker(zones, NEEDS)


def test_scene_swaps_label_only_after_repeat_sightings(tracker):
    tracker.update([det("water bottle", SPOT_A), det("snack packet", SPOT_B)])
    assert tracker.overrides() == {}
    tracker.update([det("cup", [105, 110, 205, 290]), det("snack packet", SPOT_B)])
    assert tracker.overrides() == {}  # one photo is not enough
    tracker.update([det("cup", [105, 110, 205, 290]), det("snack packet", SPOT_B)])
    swapped = tracker.overrides()
    assert set(swapped) == {"water"}
    assert swapped["water"].label == "cup" and swapped["water"].question == "Want a drink?"
    assert swapped["water"].id == "water"
    # The bottle comes back.
    tracker.update([det("water bottle", SPOT_A)])
    tracker.update([det("water bottle", SPOT_A)])
    assert tracker.overrides() == {}


def test_scene_keeps_label_when_detector_misses_or_flickers(tracker):
    tracker.update([det("water bottle", SPOT_A)])
    tracker.update([])                                  # missed entirely
    tracker.update([det("cup", SPOT_A)])                # one stray sighting
    tracker.update([det("water bottle", SPOT_A)])
    tracker.update([det("book", SPOT_A)])
    tracker.update([det("cup", SPOT_A)])                # two different strays
    assert tracker.overrides() == {}
    status = {s["zone"]: s for s in tracker.status()}
    assert status["water"]["located"] and not status["snack"]["located"]
    assert "pain" not in status  # no expected object, so never tracked


def test_scene_ignores_objects_elsewhere_and_unknown_words(tracker):
    tracker.update([det("water bottle", SPOT_A)])
    for _ in range(3):
        tracker.update([det("cup", SPOT_B), det("sign", SPOT_A),
                        det("book", [0, 0, 1280, 720])])  # far away, unknown, whole-frame
    assert tracker.overrides() == {}


def test_scene_prefers_current_object_when_both_are_seen(tracker):
    tracker.update([det("water bottle", SPOT_A)])
    for _ in range(3):
        tracker.update([det("cup", SPOT_A, 0.9), det("water bottle", SPOT_A, 0.3)])
    assert tracker.overrides() == {}


def test_scene_reset_forgets_spots(tracker):
    tracker.update([det("water bottle", SPOT_A)])
    tracker.reset()
    assert not any(s["located"] for s in tracker.status())


def test_session_asks_about_the_swapped_object(rig):
    s, clock, alerts = rig
    feed(s, clock, "water", 1.7)
    feed(s, clock, NO, 2.5)              # declined: water is on back-off
    cup = Zone("water", "cup", "Want a drink?", "wants a drink")
    s.set_overrides({"water": cup})      # a different object: ask again
    feed(s, clock, "water", 1.7)
    assert s.state == PROMPT and s.prompt["question"] == "Want a drink?"
    feed(s, clock, YES, 2.5)
    assert alerts.list()[0]["message"] == "Bed 4: patient wants a drink"
    s.set_overrides({})
    assert s.zone("water").label == "water glass"


def test_unchanged_overrides_do_not_lift_back_off(rig):
    s, clock, _ = rig
    cup = Zone("water", "cup", "Want a drink?", "wants a drink")
    s.set_overrides({"water": cup})
    feed(s, clock, "water", 1.7)
    feed(s, clock, NO, 2.5)
    s.set_overrides({"water": cup})      # same again, e.g. the next photo
    feed(s, clock, "water", 3.0)
    assert s.state == IDLE


def test_needs_table_loads_and_validates(tmp_path):
    import json
    from readtheroom.prompts import load_needs
    root = __import__("os").path.dirname(__import__("os").path.dirname(__file__))
    path = __import__("os").path.join(root, "config", "zones.json")
    zones, needs = load_zones(path), load_needs(path)
    for zone in zones.values():
        assert zone.detect is None or zone.detect in needs or zone.id == "blanket"
    bad = tmp_path / "b.json"
    bad.write_text(json.dumps({"zones": [], "needs": {"cup": {"question": "q"}}}))
    with pytest.raises(ValueError):
        load_needs(str(bad))


# --- direction reading (experimental) ---------------------------------------

def _look(out_l=0.0, in_l=0.0, up=0.0, down=0.0):
    """A feature vector for eyes turned toward the patient's left (out_l) or
    right (in_l), up or down."""
    f = np.zeros(17)
    f[1], f[4] = out_l, out_l      # left eye out + right eye in  = looking left
    f[0], f[5] = in_l, in_l        # left eye in  + right eye out = looking right
    f[2], f[6] = up, up
    f[3], f[7] = down, down
    return f


def test_direction_reader_needs_only_a_centre_look():
    from readtheroom.directions import DirectionReader
    reader = DirectionReader()
    assert reader.read(_look()) is None           # no centre learned yet
    reader.set_centre([_look(out_l=0.1, up=0.05)] * 10)   # this person's "centre" is off-zero
    assert reader.read(_look(out_l=0.1, up=0.05)) == "centre"
    assert reader.read(_look(out_l=0.5, up=0.05)) == "left"
    assert reader.read(_look(in_l=0.4, up=0.05)) == "right"
    assert reader.read(_look(out_l=0.1, up=0.5)) == "up"
    assert reader.read(_look(out_l=0.1, down=0.4)) == "centre"   # down is not used
    assert reader.read(_look(out_l=0.6, up=0.5)) == "left"       # sideways is decided first
    assert reader.read(None) is None
    with pytest.raises(ValueError):
        reader.set_centre([])


def test_direction_reader_follows_slow_drift_only():
    from readtheroom.directions import DirectionReader
    reader = DirectionReader(adapt=0.05)
    reader.set_centre([_look()] * 5)
    for _ in range(200):                          # posture shifts a little
        reader.adapt(_look(out_l=0.1))
    assert reader.centre[0] == pytest.approx(0.1, abs=0.01)
    assert reader.read(_look(out_l=0.1)) == "centre"
    assert reader.read(_look(out_l=0.4)) == "left"


def test_direction_reader_balances_a_weaker_side_from_one_look_each_way():
    from readtheroom.directions import DirectionReader
    reader = DirectionReader(h_threshold=0.14)
    reader.set_centre([_look()] * 5)
    reader.set_sides([_look(out_l=0.3)] * 5, [_look(in_l=0.5)] * 5)   # left glances read weaker
    assert reader.read(_look(out_l=0.1)) == "left"     # a small left glance now counts
    assert reader.read(_look(in_l=0.1)) == "centre"    # right is no easier than before
    reader.set_sides([_look(out_l=0.3)] * 5, [_look(in_l=0.04)] * 5)  # never looked right: even again
    assert reader.read(_look(out_l=0.1)) == "centre"
    reader.set_sides([_look(out_l=0.05)] * 5, [_look(in_l=0.5)] * 5)  # never looked left: even again
    assert reader.read(_look(out_l=0.1)) == "centre"
    reader.set_sides([_look(out_l=0.1)] * 5, [_look(in_l=2.0)] * 5)   # very lopsided: at most half
    assert reader.read(_look(out_l=0.06)) == "centre" and reader.read(_look(out_l=0.08)) == "left"
    reader.set_sides([_look(out_l=0.3)] * 5, [_look(in_l=0.5)] * 5)
    reader.set_centre([_look()] * 5)                  # a new centre (new person): balance forgotten
    assert reader.read(_look(out_l=0.1)) == "centre"


def test_zone_slots_are_validated(tmp_path):
    import json
    zone = {"label": "l", "question": "q", "message": "m"}
    path = tmp_path / "z.json"
    path.write_text(json.dumps({"zones": [dict(zone, id="a", slot="left"), dict(zone, id="b", slot="right")]}))
    assert {z.slot for z in load_zones(str(path)).values()} == {"left", "right"}
    path.write_text(json.dumps({"zones": [dict(zone, id="a", slot="left"), dict(zone, id="b", slot="left")]}))
    with pytest.raises(ValueError):
        load_zones(str(path))
    path.write_text(json.dumps({"zones": [dict(zone, id="a", slot="up")]}))
    with pytest.raises(ValueError):
        load_zones(str(path))


class _FakeExtractor:
    """Hands the app whatever feature the test sets, as if from the camera."""
    last_box = None
    last_eyes = None

    def __init__(self):
        self.feature = None

    def __call__(self, frame, ts):
        return self.feature is not None, self.feature


class _FakeCamera:
    def read(self):
        return np.zeros((4, 4, 3), np.uint8)


@pytest.fixture
def quick_app(tmp_path):
    import os
    from readtheroom.main import App
    from readtheroom.prompts import load_menu
    from readtheroom.server import Hub
    root = os.path.dirname(os.path.dirname(__file__))
    path = os.path.join(root, "config", "zones.json")
    extractor = _FakeExtractor()
    cfg = Config(ema_alpha=1.0)   # no smoothing, so each frame is what the test set
    app = App(cfg, load_zones(path), str(tmp_path), Hub(), _FakeCamera(), extractor,
              menu=load_menu(path))
    clock = Clock()

    def run(feature, seconds):
        extractor.feature = feature
        for _ in range(round(seconds / DT)):
            clock.t += DT
            app.tick(clock.t)
    return app, run


def test_quick_mode_end_to_end(quick_app):
    app, run = quick_app
    centre, left, right, up = _look(), _look(out_l=0.5), _look(in_l=0.5), _look(up=0.4)
    assert app.quick
    run(None, 1.0)                       # nobody there yet: nothing starts
    assert not app.calibrator.active and app.reader.centre is None
    run(centre, 3.0)                     # the greeting learns the centre by itself
    assert app.reader.centre is not None and not app.calibrator.active
    run(left, 2.0)                       # the object on the left
    assert app.session.state == PROMPT and "thirsty" in app.session.prompt["question"]
    run(left, 4.0)                       # still looking at it: must not count as YES
    assert app.session.state == PROMPT and app.alerts.list() == []
    run(centre, 1.2)                     # back to the screen to read the question
    run(left, 1.6)                       # now left means YES
    assert app.session.state == SENT
    assert app.alerts.list()[0]["message"] == "Bed 4: patient is thirsty and wants water"


def test_quick_mode_staff_calibration_balances_left_and_right(quick_app):
    app, run = quick_app
    centre, weak_left = _look(), _look(out_l=0.12)    # 0.12 is under the usual 0.14
    run(centre, 3.0)                                  # the greeting: centre only
    run(centre, 1.0); run(weak_left, 2.0)
    assert app.session.state == IDLE                  # too small a glance to count yet
    app.request_calibration()                         # staff press "Start calibration"
    run(centre, 2.6)
    assert app.calibrator.snapshot(0)["instruction"] == "Look to your left"
    run(_look(out_l=0.3), 2.5)
    run(_look(in_l=0.5), 2.5)                         # right glances read stronger
    assert not app.calibrator.active and app.calibrator.error is None
    run(centre, 1.0); run(weak_left, 2.0)
    assert app.session.state == PROMPT                # the same small left glance now asks


def test_quick_mode_no_and_menu(quick_app):
    app, run = quick_app
    centre, left, right, up = _look(), _look(out_l=0.5), _look(in_l=0.5), _look(up=0.4)
    run(centre, 3.0)
    run(right, 2.0)                      # the object on the right
    assert "snack" in app.session.prompt["question"]
    run(centre, 1.2)
    run(right, 1.6)                      # right now means NO
    assert app.session.state == IDLE and app.alerts.list() == []
    run(right, 4.0)                      # still looking right after answering: nothing
    assert app.session.state == IDLE
    run(centre, 1.0)
    run(up, app.cfg.menu_open_s + 0.6)   # hold up: the menu opens
    assert app.session.state == "menu" and app.session.menu[app.session.menu_index].label == "Pain"
    run(up, 3.0)                         # up means nothing inside the menu
    assert app.session.state == "menu" and app.alerts.list() == []
    run(centre, 1.2)
    run(left, 1.6)                       # YES: pain, which is asked once more
    assert app.session.state == PROMPT and "sure" in app.session.prompt["question"]
    assert app.alerts.list() == []
    run(centre, 1.2)
    run(left, 1.6)                       # YES again: now it is sent
    assert app.alerts.list()[0]["urgent"] is True and "PAIN" in app.alerts.list()[0]["message"]


# --- scene map: what lies to the left and right of the screen -----------------

SIZE = (1280, 720)
SCREEN = det("laptop", [540, 260, 740, 460], 0.9)   # centre x = 640


@pytest.fixture
def scene_map():
    from readtheroom.scene import SceneMap
    return SceneMap(NEEDS)


def see(scene_map, detections, times=None):
    """Show the same photo enough times for new objects to count."""
    for _ in range(times or scene_map.confirm):
        scene_map.update(detections, SIZE)


def test_scene_map_places_objects_by_where_they_are(scene_map):
    see(scene_map, [SCREEN, det("water bottle", [100, 300, 180, 460]),
                    det("cup", [1000, 320, 1080, 420]), det("sign", [50, 50, 90, 90])])
    sides = scene_map.sides()
    assert [i["label"] for i in sides["left"]] == ["water bottle"]
    assert [i["label"] for i in sides["right"]] == ["cup"]       # "sign" has no question: ignored
    # Move the bottle to the other side: the map follows, nothing is typed in.
    for _ in range(scene_map.hold + 1):
        scene_map.update([SCREEN, det("water bottle", [900, 300, 980, 460]),
                          det("cup", [1000, 320, 1080, 420])], SIZE)
    sides = scene_map.sides()
    assert sides["left"] == [] and {i["label"] for i in sides["right"]} == {"water bottle", "cup"}


def test_scene_map_orders_objects_that_share_a_side(scene_map):
    see(scene_map, [SCREEN, det("book", [100, 20, 200, 90]),          # high up on the left
                    det("water bottle", [150, 330, 210, 400])])         # level with the screen
    assert [i["label"] for i in scene_map.sides()["left"]] == ["water bottle", "book"]


def test_scene_map_survives_missed_detections_then_forgets(scene_map):
    see(scene_map, [SCREEN, det("cup", [1000, 320, 1080, 420])])
    for _ in range(scene_map.hold):
        scene_map.update([], SIZE)                    # missed a few times: still there
    assert [i["label"] for i in scene_map.sides()["right"]] == ["cup"]
    scene_map.update([], SIZE)                        # gone for good
    assert scene_map.sides()["right"] == []


def test_scene_map_ignores_one_off_weak_and_huge_detections(scene_map):
    scene_map.update([SCREEN, det("cup", [100, 300, 180, 400])], SIZE)       # seen once only
    scene_map.update([SCREEN], SIZE)
    scene_map.update([SCREEN, det("cup", [100, 300, 180, 400])], SIZE)       # not in a row
    assert scene_map.sides()["left"] == []
    see(scene_map, [SCREEN, det("cup", [100, 300, 180, 400], 0.3)])          # a weak guess (a jacket?)
    assert scene_map.sides()["left"] == []
    see(scene_map, [SCREEN, det("blanket", [0, 0, 1200, 700], 0.9)])          # most of the picture
    assert scene_map.sides() == {"left": [], "right": []}


def test_scene_map_refuses_objects_in_line_with_the_screen(scene_map):
    scene_map.update([SCREEN, det("cup", [610, 100, 680, 170])], SIZE)   # right above the screen
    assert scene_map.sides() == {"left": [], "right": []}
    row = scene_map.status()[0]
    assert row["side"] is None and row["now"] == "cup"


def test_scene_map_without_a_visible_screen_uses_the_middle(scene_map):
    see(scene_map, [det("cup", [100, 300, 180, 400]), det("book", [1100, 300, 1200, 400])])
    sides = scene_map.sides()
    assert [i["label"] for i in sides["left"]] == ["cup"] and [i["label"] for i in sides["right"]] == ["book"]
    assert scene_map.screen is None


class _FakeScene:
    """Stands in for the scene worker: the test sets what lies each way."""
    def __init__(self):
        self.version, self._sides = 0, {"left": [], "right": []}

    def set(self, left=(), right=()):
        from readtheroom.scene import SceneMap
        make = lambda side, labels: [{"id": SceneMap.zone_id(l, side), "label": l, "box": [0, 0, 1, 1]}
                                     for l in labels]
        self._sides = {"left": make("left", left), "right": make("right", right)}
        self.version += 1

    def sides(self): return self._sides
    def overrides(self): return {}
    def snapshot(self): return {}


def test_gaze_plus_scene_asks_about_what_is_really_there(quick_app, tmp_path):
    app, run = quick_app
    centre, left, right = _look(), _look(out_l=0.5), _look(in_l=0.5)
    app.scene, app._needs = _FakeScene(), NEEDS
    app.scene.set(left=["cup", "book"], right=["water bottle"])
    run(centre, 3.0)
    run(left, 2.0)                            # two things are to the left: the nearer one first
    assert app.session.prompt["question"] == "Want a drink?"
    run(centre, 1.2); run(right, 1.6)         # NO
    assert app.session.state == IDLE
    run(centre, 1.0); run(left, 2.0)          # look left again: now it asks about the other one
    assert app.session.prompt["question"] == "Want to read?"
    run(centre, 1.2); run(left, 1.6)          # YES
    assert app.alerts.list()[0]["message"] == "Bed 4: patient wants their book"
    run(centre, 5.0)
    app.scene.set(left=["water bottle"], right=[])   # the room changed
    run(centre, 0.5); run(left, 2.0)
    assert app.session.prompt["question"] == "Thirsty?"
    run(centre, 1.2); run(right, 1.6)
    app.scene.set()                           # scene camera sees nothing it knows
    run(centre, 0.5); run(left, 2.0)          # falls back to the typed objects
    assert "thirsty" in app.session.prompt["question"].lower()


# --- mapping: gaze -> point in the scene photo --------------------------------

TARGETS = {"pen": [90, 520, 170, 640], "bottle": [700, 270, 830, 600],
           "phone": [1280, 490, 1460, 630], "screen": [750, 900, 750, 900]}


def _centre(box):
    return [(box[0] + box[2]) / 2, (box[1] + box[3]) / 2]


TARGETS6 = {**TARGETS, "yes": [430, 940, 430, 940], "no": [1080, 940, 1080, 940]}


def _samples(true_map, noise=0.0, n=20, seed=0, targets=TARGETS):
    """Eye readings that a known straight-line map turns into each target's centre."""
    rng = np.random.default_rng(seed)
    inverse = np.linalg.inv(true_map[:2])
    out = {}
    for name, box in targets.items():
        eye = (np.array(_centre(box)) - true_map[2]) @ inverse
        out[name] = eye + rng.normal(0, noise, size=(n, 2))
    return out


TRUE_MAP = np.array([[-2000.0, 150.0], [100.0, -1500.0], [800.0, 700.0]])


def test_mapping_recovers_a_straight_line_map():
    from readtheroom import mapping
    samples = _samples(TRUE_MAP)
    m = mapping.fit(samples, {k: _centre(b) for k, b in TARGETS.items()})
    for name, box in TARGETS.items():
        assert np.allclose(mapping.predict(m, samples[name][0]), _centre(box), atol=1.0)


def test_mapping_uses_each_targets_median_so_a_blink_does_not_drag_the_fit():
    from readtheroom import mapping
    samples = _samples(TRUE_MAP)
    samples["phone"][:3] = [5.0, 5.0]                   # a few wild frames (a blink)
    m = mapping.fit(samples, {k: _centre(b) for k, b in TARGETS.items()})
    assert np.allclose(mapping.predict(m, samples["bottle"][5]), _centre(TARGETS["bottle"]), atol=1.0)


def test_mapping_homography_recovers_a_perspective_map():
    from readtheroom import mapping
    H = np.array([[-1800.0, 120.0, 800.0], [90.0, -1400.0, 700.0], [0.3, -0.2, 1.0]])
    rng = np.random.default_rng(1)
    eye = rng.uniform(-0.3, 0.3, size=(6, 2))
    proj = np.column_stack([eye, np.ones(6)]) @ H.T
    pix = proj[:, :2] / proj[:, 2:]
    samples = {i: eye[i:i + 1] for i in range(6)}
    points = {i: pix[i] for i in range(6)}
    m = mapping.fit(samples, points, kind="homography")
    assert np.allclose(mapping.predict(m, eye[2]), pix[2], atol=0.5)
    straight = mapping.fit(samples, points)              # a straight line cannot follow it
    assert not np.allclose(mapping.predict(straight, eye[2]), pix[2], atol=0.5)


def test_mapping_refuses_to_fit_from_too_few_targets():
    from readtheroom import mapping
    three = {k: TARGETS[k] for k in ("pen", "bottle", "phone")}
    samples = _samples(TRUE_MAP, targets=three)
    points = {k: _centre(b) for k, b in three.items()}
    mapping.fit(samples, points)                          # 3 is enough for a straight line
    with pytest.raises(ValueError):
        mapping.fit(samples, points, kind="homography")   # a perspective map needs 4
    with pytest.raises(ValueError):
        mapping.fit({"pen": samples["pen"], "bottle": samples["bottle"]}, points)
    with pytest.raises(ValueError):                      # nothing recorded: no verdict, not a pass
        mapping.leave_one_out({}, TARGETS)


def test_mapping_leave_one_out_flags_each_target_it_can_place():
    from readtheroom import mapping
    for kind in ("affine", "homography"):
        report = mapping.leave_one_out(_samples(TRUE_MAP, noise=0.002, targets=TARGETS6), TARGETS6, kind)
        assert set(report) == set(TARGETS6)
        assert all(r["nearest"] == name for name, r in report.items()), kind
        assert all(r["error_px"] < 30 for r in report.values()), kind


def test_mapping_picks_the_nearest_object_within_reach_only():
    from readtheroom import mapping
    radius = mapping.reach(TARGETS)
    assert 0 < radius < 300                              # half the closest gap
    assert mapping.nearest([800, 400], TARGETS, radius) == "bottle"   # inside its box
    assert mapping.nearest([1250, 560], TARGETS, radius) == "phone"   # just outside, close
    assert mapping.nearest([1100, 100], TARGETS, radius) is None      # empty table
    assert mapping.nearest([-50, 560], TARGETS, radius, size=(2000, 1333)) is None  # off the photo
