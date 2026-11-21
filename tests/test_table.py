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

    # Teach the three looks, one request each.
    for target, where in ((REST, SCREEN), ("t:water-bottle", BOTTLE), ("t:snack-packet", SNACK)):
        assert app.request_teach(target)
        run(look(where), app.cfg.teach_settle_s + app.cfg.teach_record_s + 0.3)
        assert target in ray.points, target
        assert app.calibrator.error is None
    assert app.hub._state["calibrated"] is True
    assert app.self_check["t:water-bottle"] == 1.0
    assert os.path.exists(app._ray_path)

    # A look at the bottle, with the head turned toward it and shifted in the bed.
    run(look(SCREEN), 1.0)
    run(look(BOTTLE, pos=[10, 3, -55], yaw=0.3), 2.0)
    assert app.session.state == "prompt" and "thirsty" in app.session.prompt["question"]
    run(look(BOTTLE, pos=[10, 3, -55], yaw=0.3), 3.0)      # still looking: not an answer
    assert app.alerts.list() == []
    run(look(SCREEN), 1.2)                                   # back to read the question
    run(look([14, -8, -5]), 1.6)                             # left of the screen: YES
    assert app.session.state == "sent"
    assert app.alerts.list()[0]["message"] == "Bed 4: patient is thirsty and wants water"

    # The snack, answered NO from the right of the screen.
    run(look(SCREEN), app.cfg.sent_display_s + 1.5)
    run(look(SNACK), 2.0)
    assert app.session.state == "prompt" and "snack" in app.session.prompt["question"]
    run(look(SCREEN), 1.2)
    run(look([-14, -8, -5]), 1.6)
    assert app.session.state == "idle" and len(app.alerts.list()) == 1

    snap = app.hub._state["table"]
    assert snap["ready"] and set(snap["learned"]) == {REST, "t:water-bottle", "t:snack-packet"}
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
    for label, seconds in ((REST, 1.0), ("t:water-bottle", 2.0)):
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
