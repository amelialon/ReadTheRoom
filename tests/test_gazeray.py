"""The gaze ray must find the same object after the head turns or moves.

Frames are made from a small model of a person: an object at a known point
in the room, a head at some position facing some way, and eyes that turn
within the head exactly as far as needed to look at the object. Head turns
and shifts therefore change every number in the frame, and the test is that
the learned point still wins.
"""
import math

import numpy as np
import pytest

from readtheroom.features import HEAD_R, HEAD_T, N_FEATURES
from readtheroom.gazeray import GazeRay, eye_angles


def rotation_y(yaw):
    c, s = math.cos(yaw), math.sin(yaw)
    return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])


def rotation_x(pitch):
    c, s = math.cos(pitch), math.sin(pitch)
    return np.array([[1, 0, 0], [0, c, -s], [0, s, c]])


def frame(head_pos, head_yaw, head_pitch, target, reader: GazeRay, noise=0.0, rng=None):
    """A feature vector for a head at head_pos facing (head_yaw, head_pitch),
    with the eyes turned within the head to look at target."""
    rot = rotation_y(head_yaw) @ rotation_x(head_pitch)
    to = np.asarray(target, dtype=float) - np.asarray(head_pos, dtype=float)
    local = rot.T @ (to / np.linalg.norm(to))        # the line to the object, in face axes
    yaw = math.atan2(local[0], local[2])
    pitch = math.asin(local[1])
    # Invert eye_angles: split each angle evenly between the two estimates.
    f = np.zeros(N_FEATURES)
    score_h, score_v = yaw / reader.eye_gain, pitch / reader.eye_gain
    # keep the score estimate inside its clamp by putting the rest in the iris
    score_h = max(-reader.eye_clamp, min(reader.eye_clamp, score_h * reader.eye_gain)) / reader.eye_gain
    score_v = max(-reader.eye_clamp, min(reader.eye_clamp, score_v * reader.eye_gain)) / reader.eye_gain
    iris_h = (2 * yaw - reader.eye_gain * score_h) / reader.iris_gain
    iris_v = (2 * pitch - reader.eye_gain * score_v) / reader.iris_gain
    f[1] = f[4] = max(0.0, score_h); f[0] = f[5] = max(0.0, -score_h)   # out-left / in-right vs in-left / out-right
    f[2] = f[6] = max(0.0, score_v); f[3] = f[7] = max(0.0, -score_v)
    f[8] = f[10] = 0.5 + iris_h
    f[9] = f[11] = iris_v
    f[HEAD_T:HEAD_T + 3] = head_pos
    f[HEAD_R:HEAD_R + 9] = rot.reshape(-1)
    if noise and rng is not None:
        f[:12] += rng.normal(0, noise, 12)
    return f


@pytest.fixture
def reader():
    return GazeRay(distance_cm=70.0, accept_deg=9.0, margin_deg=2.0)


# Camera axes: x to the picture's right (the patient's left), y up, z toward
# the camera. The head sits 50 cm in front of the camera (z = -50); the table
# is beyond the screen, so objects on it are at the camera's depth or behind it.
BOTTLE = [8.0, 8.0, 20.0]        # a little to the patient's left, beyond the screen
SNACK = [-30.0, -5.0, 0.0]       # to the patient's right, beside the screen
SCREEN = [0.0, -8.0, -5.0]       # just below the camera


def test_eye_angles_round_trip(reader):
    f = frame([0, 0, -50], 0.0, 0.0, BOTTLE, reader)
    yaw, pitch = eye_angles(f, reader.eye_gain, reader.eye_clamp, reader.iris_gain)
    assert math.isclose(yaw, math.atan2(8, 70), abs_tol=1e-6)
    assert math.isclose(pitch, math.asin(8 / math.sqrt(8 * 8 + 8 * 8 + 70 * 70)), abs_tol=1e-6)


def test_learns_points_and_recognises_them(reader):
    home = [0, 0, -50]
    for name, target in (("bottle", BOTTLE), ("snack", SNACK), ("screen", SCREEN)):
        reader.learn(name, [frame(home, 0, 0, target, reader) for _ in range(10)],
                     distance_cm=None if name != "screen" else 45.0)
    assert reader.predict(frame(home, 0, 0, BOTTLE, reader)) == "bottle"
    assert reader.predict(frame(home, 0, 0, SNACK, reader)) == "snack"
    assert reader.predict(frame(home, 0, 0, SCREEN, reader)) == "screen"


def test_head_turn_does_not_fool_it(reader):
    home = [0, 0, -50]
    reader.learn("bottle", [frame(home, 0, 0, BOTTLE, reader)] * 5)
    reader.learn("snack", [frame(home, 0, 0, SNACK, reader)] * 5)
    # Turn the head 25 degrees toward the bottle and look at it with the eyes nearly straight.
    turned = frame(home, math.radians(25), 0, BOTTLE, reader)
    assert reader.predict(turned) == "bottle"
    # Head turned toward the bottle but eyes swung back to the snack.
    assert reader.predict(frame(home, math.radians(25), 0, SNACK, reader)) == "snack"


def test_moving_in_the_bed_keeps_working(reader):
    reader.learn("bottle", [frame([0, 0, -50], 0, 0, BOTTLE, reader)] * 5)
    reader.learn("snack", [frame([0, 0, -50], 0, 0, SNACK, reader)] * 5)
    for pos in ([12, 0, -50], [-12, 4, -58], [0, -6, -42]):
        assert reader.predict(frame(pos, 0, 0, BOTTLE, reader)) == "bottle", pos
        assert reader.predict(frame(pos, 0, 0, SNACK, reader)) == "snack", pos


def test_looking_elsewhere_is_nothing(reader):
    home = [0, 0, -50]
    reader.learn("bottle", [frame(home, 0, 0, BOTTLE, reader)] * 5)
    reader.learn("snack", [frame(home, 0, 0, SNACK, reader)] * 5)
    assert reader.predict(frame(home, 0, 0, [0, 60, 20], reader)) is None      # up at the ceiling
    assert reader.predict(frame(home, 0, 0, [-11, 1, 10], reader)) is None     # between the two


def test_noise_is_tolerated(reader):
    rng = np.random.default_rng(1)
    home = [0, 0, -50]
    reader.learn("bottle", [frame(home, 0, 0, BOTTLE, reader, 0.02, rng) for _ in range(20)])
    reader.learn("snack", [frame(home, 0, 0, SNACK, reader, 0.02, rng) for _ in range(20)])
    hits = sum(reader.predict(frame(home, 0, 0, BOTTLE, reader, 0.02, rng)) == "bottle" for _ in range(50))
    assert hits >= 45
    assert reader.spread["bottle"] < 5


def test_offset_from_screen_gives_yes_no(reader):
    home = [0, 0, -50]
    reader.learn("screen", [frame(home, 0, 0, SCREEN, reader)] * 5, distance_cm=45.0)
    left, _ = reader.offset_from("screen", frame(home, 0, 0, [15, -8, -5], reader))
    right, _ = reader.offset_from("screen", frame(home, 0, 0, [-15, -8, -5], reader))
    assert left > 10 and right < -10


def test_save_and_load(tmp_path, reader):
    reader.learn("bottle", [frame([0, 0, -50], 0, 0, BOTTLE, reader)] * 5)
    path = tmp_path / "ray.json"
    reader.save(str(path))
    other = GazeRay()
    other.load(str(path))
    assert other.points == reader.points


def test_short_or_unreadable_frames(reader):
    assert reader.ray(None) is None
    assert reader.ray(np.zeros(17)) is None
    with pytest.raises(ValueError):
        reader.learn("x", [None, None])
