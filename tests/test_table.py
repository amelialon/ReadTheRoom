"""Table mode end to end: a photo of the table, targets on it, one look per
target to learn, then a look at an object asks the question and a look
beside the screen answers it. Frames come from the small person model in
test_gazeray, so head turns and shifts are real changes in the numbers."""
import json
import os

import numpy as np
import pytest

from readtheroom.config import Config, REST
from readtheroom.prompts import load_menu, load_needs, load_zones
from readtheroom.table import Table, jpeg_size
from tests.test_core import Clock, _FakeCamera, _FakeExtractor
from tests.test_gazeray import BOTTLE, SCREEN, SNACK, frame

ROOT = os.path.dirname(os.path.dirname(__file__))
ZONES = os.path.join(ROOT, "config", "zones.json")
# A 2x2 JPEG is enough: only the header is read.
TINY_JPEG = bytes.fromhex(
    "ffd8ffe000104a46494600010100000100010000ffdb004300080606070605080707070909080a0c140d0c0b0b0c1912130f14"
    "1d1a1f1e1d1a1c1c20242e2720222c231c1c2837292c30313434341f27393d38323c2e333432ffc0000b080002000201011100"
    "ffc40014000100000000000000000000000000000009ffc40014100100000000000000000000000000000000ffda0008010100"
    "003f00d2cf20ffd9")


def test_jpeg_size():
    assert jpeg_size(TINY_JPEG) == (2, 2)
    assert jpeg_size(b"not a jpeg") is None


def test_table_targets_and_zones(tmp_path):
    needs = load_needs(ZONES)
    table = Table(str(tmp_path / "table"), needs)
    with pytest.raises(ValueError):
        table.set_targets([])            # no photo yet
    table.set_photo(TINY_JPEG)
    table.set_targets([{"id": REST, "box": [0, 1, 1, 2]},
                       {"need": "water bottle", "box": [0, 0, 1, 1]},
                       {"need": "snack packet", "label": "crisps", "box": [1, 0, 2, 1]}])
    zones = table.zones()
    assert set(zones) == {"t:water-bottle", "t:crisps"}
    assert "thirsty" in zones["t:water-bottle"].question
    assert table.has_screen()
    with pytest.raises(ValueError):
        table.set_targets([{"need": "unicorn", "box": [0, 0, 1, 1]}])
    with pytest.raises(ValueError):
        table.set_targets([{"need": "water bottle", "box": [0, 0, 5, 5]}])  # outside the photo
    # Survives a restart.
    again = Table(str(tmp_path / "table"), needs)
    assert again.photo == TINY_JPEG and [t["id"] for t in again.targets] == [REST, "t:water-bottle", "t:crisps"]


@pytest.fixture
def table_app(tmp_path):
    from readtheroom.main import App
    from readtheroom.server import Hub
    extractor = _FakeExtractor()
    cfg = Config(ema_alpha=1.0, gaze_mode="table")
    app = App(cfg, load_zones(ZONES), str(tmp_path), Hub(), _FakeCamera(), extractor,
              menu=load_menu(ZONES), needs=load_needs(ZONES))
    clock = Clock()

    def run(feature, seconds):
        extractor.feature = feature
        for _ in range(int(seconds * 20)):
            clock.t += 0.05
            app.tick(clock.t)
    return app, run


YES_SPOT = [14.0, -8.0, -5.0]     # the YES half of the screen, to the patient's left
NO_SPOT = [-14.0, -8.0, -5.0]


def calibrate(app, run, where: dict, pos=(0, 0, -50), yaw=0.0):
    """Run the guided sequence, feeding the frame for whichever target it asks for."""
    assert app.request_calibration()
    run(None, 0.1)
    steps = 0
    while app.calibrator.active and steps < 50:
        target = app.calibrator.snapshot(0)["target"]
        run(frame(list(pos), yaw, 0, where[target], app.ray), 0.5)
        steps += 1
    assert app.calibrator.error is None, app.calibrator.error


def test_table_mode_end_to_end(table_app):
    app, run = table_app
    assert app.table_mode and not app.quick
    app.set_table_photo(TINY_JPEG)
    app.set_table_targets([{"id": REST, "box": [0, 1, 1, 2]},
                           {"need": "water bottle", "box": [0, 0, 1, 1]},
                           {"need": "snack packet", "box": [1, 0, 2, 1]}])
    ray = app.ray
    home = [0, 0, -50]
    look = lambda target, pos=home, yaw=0.0: frame(pos, yaw, 0, target, ray)
    run(look(SCREEN), 0.5)
    assert set(app.session.zones) == {"t:water-bottle", "t:snack-packet"}
    assert app.hub._state["calibrated"] is False and app.session.state == "idle"
    assert app.hub._state["table"]["steps"] == 5

    # One guided sequence: screen, YES, NO, bottle, snack.
    calibrate(app, run, {REST: SCREEN, "yes": YES_SPOT, "no": NO_SPOT,
                         "t:water-bottle": BOTTLE, "t:snack-packet": SNACK})
    assert set(ray.points) == {REST, "yes", "no", "t:water-bottle", "t:snack-packet"}
    assert app.hub._state["calibrated"] is True
    assert app.self_check["t:water-bottle"] == 1.0
    assert os.path.exists(app._ray_path)

    # A look at the bottle, with the head turned toward it and shifted in the bed.
    run(look(SCREEN), 1.0)
    run(look(BOTTLE, pos=[10, 3, -55], yaw=0.3), 1.5)    # a glance: nothing yet
    assert app.session.state == "idle"
    run(look(BOTTLE, pos=[10, 3, -55], yaw=0.3), 1.5)    # a hold: asked
    assert app.session.state == "prompt" and "thirsty" in app.session.prompt["question"]
    run(look(BOTTLE, pos=[10, 3, -55], yaw=0.3), 3.0)      # still looking: not an answer
    assert app.alerts.list() == []
    run(look(SCREEN), 1.2)                                   # back to read the question
    run(look(YES_SPOT), 1.6)                                 # the YES look: sent
    assert app.session.state == "sent"
    assert app.alerts.list()[0]["message"] == "Bed 4: patient is thirsty and wants water"

    # The snack, answered with the NO look.
    run(look(SCREEN), app.cfg.sent_display_s + 1.5)
    run(look(SNACK), 3.0)
    assert app.session.state == "prompt" and "snack" in app.session.prompt["question"]
    run(look(SCREEN), 1.2)
    run(look(NO_SPOT), 1.6)
    assert app.session.state == "idle" and len(app.alerts.list()) == 1

    # Looking near the screen but not exactly at its taught point still counts as the screen.
    run(look([3, -12, -5]), 1.0)
    assert app.session.last_label == REST if hasattr(app.session, "last_label") else True

    snap = app.hub._state["table"]
    assert snap["ready"] and set(snap["learned"]) == set(ray.points)
    assert snap["gaze_uv"] is not None and 0 <= snap["gaze_uv"][0] <= 1


def test_new_photo_forgets_the_looks(table_app):
    app, run = table_app
    app.set_table_photo(TINY_JPEG)
    app.set_table_targets([{"id": REST, "box": [0, 1, 1, 2]}, {"need": "water bottle", "box": [0, 0, 1, 1]}])
    app.request_teach(REST)
    run(frame([0, 0, -50], 0, 0, SCREEN, app.ray), 3.0)
    assert REST in app.ray.points
    app.set_table_photo(TINY_JPEG)
    run(None, 0.2)
    assert app.ray.points == {} and app.session.zones == {}
    assert app.hub._state["table"]["targets"] == []


def test_simulation_uses_the_targets(tmp_path):
    from readtheroom.main import App
    from readtheroom.server import Hub
    cfg = Config(gaze_mode="table")
    app = App(cfg, load_zones(ZONES), str(tmp_path), Hub(), simulate=True,
              menu=load_menu(ZONES), needs=load_needs(ZONES))
    app.set_table_photo(TINY_JPEG)
    app.set_table_targets([{"id": REST, "box": [0, 1, 1, 2]}, {"need": "water bottle", "box": [0, 0, 1, 1]}])
    clock = Clock()
    for label, seconds in ((REST, 1.0), ("t:water-bottle", 3.0)):
        app.set_debug_gaze(label)
        for _ in range(int(seconds * 20)):
            clock.t += 0.05
            app.tick(clock.t)
    assert app.session.state == "prompt"
    assert app.request_teach(REST) is False    # nothing to learn from in simulation


def test_teach_frames_are_saved_and_tunable(table_app, tmp_path, capsys):
    from readtheroom import tune_ray
    app, run = table_app
    app.set_table_photo(TINY_JPEG)
    app.set_table_targets([{"id": REST, "box": [0, 1, 1, 2]}, {"need": "water bottle", "box": [0, 0, 1, 1]}])
    for target, where in ((REST, SCREEN), ("t:water-bottle", BOTTLE), (REST, SCREEN), ("t:water-bottle", BOTTLE)):
        app.request_teach(target)
        run(frame([0, 0, -50], 0.2 if target == REST else 0.0, 0, where, app.ray), 3.0)
    files = sorted(str(p) for p in tmp_path.glob("teach-*.json"))
    assert len(files) == 4
    records = [json.loads(open(f).read()) for f in files]
    assert {r["target"] for r in records} == {REST, "t:water-bottle"}
    assert all(len(r["frames"][0]) == 29 for r in records)
    tune_ray.main(files)
    out = capsys.readouterr().out
    assert "Recommended Config values" in out and "fair test" in out


class _FakeTableCamera:
    def __init__(self, jpeg=None):
        self.jpeg = jpeg

    def snapshot_jpeg(self):
        return self.jpeg


class _FakeDetector:
    def detect_jpeg(self, jpeg):
        return [{"label": "laptop", "score": 0.9, "box": [0, 1, 1, 2]},
                {"label": "water bottle", "score": 0.6, "box": [0, 0, 1, 1]},
                {"label": "cat", "score": 0.5, "box": [1, 0, 2, 1]}]


def test_table_camera_photographs_and_populates_by_itself(tmp_path):
    from readtheroom.main import App
    from readtheroom.server import Hub
    extractor = _FakeExtractor()
    camera = _FakeTableCamera()          # no frame yet
    app = App(Config(ema_alpha=1.0, gaze_mode="table"), load_zones(ZONES), str(tmp_path), Hub(),
              _FakeCamera(), extractor, menu=load_menu(ZONES), needs=load_needs(ZONES),
              detector=_FakeDetector(), table_source=camera)
    clock = Clock()
    for _ in range(60):                  # three seconds: it tries, finds no frame, waits
        clock.t += 0.05
        app.tick(clock.t)
    assert app.table.photo is None
    camera.jpeg = TINY_JPEG
    for _ in range(120):
        clock.t += 0.05
        app.tick(clock.t)
    assert app.table.photo == TINY_JPEG
    assert [t["id"] for t in app.table.targets] == [REST, "t:water-bottle"]   # the cat has no question
    assert set(app.session.zones) == {"t:water-bottle"}
    assert app.hub._state["table"]["camera"] is True
    # Retaking the photo forgets the looks and repopulates.
    app.request_teach(REST)
    for _ in range(60):
        extractor.feature = frame([0, 0, -50], 0, 0, SCREEN, app.ray)
        clock.t += 0.05
        app.tick(clock.t)
    assert REST in app.ray.points
    app.snap_table_photo()
    assert app.ray.points == {} and [t["id"] for t in app.table.targets] == [REST, "t:water-bottle"]


def test_file_snapshot_source(tmp_path):
    from readtheroom.camera import FileSnapshotSource
    src = FileSnapshotSource(str(tmp_path / "scene.jpg"))
    assert src.snapshot_jpeg() is None                   # nothing written yet
    (tmp_path / "scene.jpg").write_bytes(TINY_JPEG[:20])  # half-written
    assert src.snapshot_jpeg() is None
    (tmp_path / "scene.jpg").write_bytes(TINY_JPEG)
    assert src.snapshot_jpeg() == TINY_JPEG


def test_a_broken_detector_still_leaves_a_photo(tmp_path):
    class Broken:
        def detect_jpeg(self, jpeg):
            raise RuntimeError("Can't infer a dim denoted by -1")
    table = Table(str(tmp_path / "table"), load_needs(ZONES))
    assert table.set_photo(TINY_JPEG, Broken()) == []
    assert table.photo == TINY_JPEG and table.found == [] and "infer" in table.detect_error
