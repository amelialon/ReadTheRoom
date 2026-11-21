"""The offline tuner for quick-mode directions (readtheroom/tune_directions.py)."""
import json

import numpy as np
import pytest

from readtheroom import tune_directions as tune
from readtheroom.directions import CENTRE, DOWN, LEFT, RIGHT, UP, DirectionReader


def feat(eye_h=0.0, eye_v=0.0, iris_h=0.5, iris_v=0.0, yaw=0.0, pitch=0.0):
    """A feature vector with the given direction signals (see directions.signals)."""
    f = np.zeros(17)
    f[1] = f[4] = max(eye_h, 0.0)      # left eye out + right eye in  = eyes to the left
    f[0] = f[5] = max(-eye_h, 0.0)
    f[2] = f[6] = max(eye_v, 0.0)
    f[3] = f[7] = max(-eye_v, 0.0)
    f[8] = f[10] = iris_h
    f[9] = f[11] = iris_v
    f[12], f[13] = yaw, pitch
    return f


def frames(rng, n=20, noise=0.01, **kw):
    """n jittered copies of one look."""
    out = []
    for _ in range(n):
        jitter = {k: v + noise * rng.standard_normal() for k, v in kw.items()}
        for k in ("eye_h", "eye_v", "iris_h", "iris_v", "yaw", "pitch"):
            jitter.setdefault(k, (0.5 if k == "iris_h" else 0.0) + noise * rng.standard_normal())
        out.append(feat(**jitter))
    return out


def eyes_only_session(rng, iris_sign=-1.0, **extra):
    """The eyes-only steps of check_directions. The iris moves the opposite
    way to eye_h (iris_sign) so the tuner has a sign to find."""
    def look(eye_h=0.0, eye_v=0.0, **kw):
        return frames(rng, eye_h=eye_h, eye_v=eye_v, iris_h=0.5 + iris_sign * 0.3 * eye_h,
                      iris_v=0.2 * eye_v, pitch=-0.05 * eye_v, **kw)
    return [
        ("centre", CENTRE, look()),
        ("far left", LEFT, look(eye_h=0.4)),
        ("centre again", CENTRE, look()),
        ("far right", RIGHT, look(eye_h=-0.4)),
        ("up", UP, look(eye_v=0.3)),
        ("down", DOWN, look(eye_v=-0.3)),
        ("left edge", LEFT, look(eye_h=0.25)),
        ("right edge", RIGHT, look(eye_h=-0.25)),
        ("centre last", CENTRE, look()),
    ]


def head_session(rng, yaw_sign=-1.0):
    """Eyes-only steps plus head turns and natural looks where the head
    carries most of the signal and the eyes stay near the centre."""
    steps = eyes_only_session(rng)
    steps += [
        ("head left", None, frames(rng, yaw=yaw_sign * 0.4)),
        ("head right", None, frames(rng, yaw=-yaw_sign * 0.4)),
        ("natural centre", CENTRE, frames(rng)),
        ("natural left", LEFT, frames(rng, eye_h=0.08, yaw=yaw_sign * 0.25)),
        ("natural centre 2", CENTRE, frames(rng)),
        ("natural right", RIGHT, frames(rng, eye_h=-0.08, yaw=-yaw_sign * 0.25)),
        ("natural centre 3", CENTRE, frames(rng)),
    ]
    return steps


# --- loading -----------------------------------------------------------------

def test_expected_labels_follow_the_reader():
    assert tune.expected_label("down", DOWN) == CENTRE          # the reader never says down
    assert tune.expected_label("head left", None) == LEFT        # scored from the name
    assert tune.expected_label("head right", None) == RIGHT
    assert tune.expected_label("left edge", LEFT) == LEFT
    assert tune.expected_label("centre", CENTRE) == CENTRE
    assert tune.expected_label("something else", None) is None


def test_old_recording_without_features_still_loads():
    old = {"centre": [0.0, 0.0], "thresholds": [0.18, 0.15], "steps": [
        {"name": "centre", "expected": "centre", "frames": 2, "seen": 2,
         "signals": {"eye_h": [0.0, 0.01], "eye_v": [0.0, 0.0], "head_yaw": [0.0, 0.0], "head_pitch": [0.0, 0.0]}},
        {"name": "far left", "expected": "left", "frames": 2, "seen": 2,
         "signals": {"eye_h": [0.4, 0.41], "eye_v": [0.0, 0.0], "head_yaw": [0.0, 0.0], "head_pitch": [0.0, 0.0]}},
        {"name": "down", "expected": "down", "frames": 1, "seen": 1,
         "signals": {"eye_h": [0.0], "eye_v": [-0.3], "head_yaw": [0.0], "head_pitch": [0.0]}},
    ]}
    rec = tune.recording_from_dict(old, "old.json")
    assert not rec.has_iris
    assert [s.expected for s in rec.steps] == [CENTRE, LEFT, CENTRE]
    assert rec.steps[1].table.shape == (2, 6)
    signs = tune.detect_signs([rec])
    assert signs["iris_h"][0] is None and signs["head_yaw"][0] is None
    best = tune.search([rec], signs)
    assert (best.iris_weight, best.head_weight) == (0.0, 0.0)   # nothing to choose them from
    assert tune.score([rec], best)["accuracy"] == 1.0


# --- signs -------------------------------------------------------------------

def test_sign_detection_reads_direction_of_each_term():
    rng = np.random.default_rng(1)
    rec = tune.recording_from_steps(head_session(rng, yaw_sign=-1.0))
    signs = tune.detect_signs([rec])
    assert signs["iris_h"][0] == -1      # iris offset falls when the eyes go left
    assert signs["head_yaw"][0] == -1    # "head left" has negative yaw, so -1 makes it positive
    assert signs["iris_v"][0] == 1       # up raises iris_v
    assert signs["head_pitch"][0] == -1  # up lowers pitch a little (but clearly)

    flipped = tune.recording_from_steps(head_session(np.random.default_rng(2), yaw_sign=1.0))
    assert tune.detect_signs([flipped])["head_yaw"][0] == 1

    no_head = tune.recording_from_steps(eyes_only_session(np.random.default_rng(3)))
    assert tune.detect_signs([no_head])["head_yaw"][0] is None


def test_sign_detection_refuses_noise():
    rng = np.random.default_rng(4)
    # The head does not move at all here: yaw and pitch are pure noise.
    steps = eyes_only_session(rng)
    steps += [("head left", None, frames(rng)), ("head right", None, frames(rng))]
    signs = tune.detect_signs([tune.recording_from_steps(steps)])
    assert signs["head_yaw"][0] is None
    assert "too weak" in signs["head_yaw"][1]


# --- the decision rule -------------------------------------------------------

@pytest.mark.parametrize("settings", [
    tune.Settings.current(),
    tune.Settings(0.16, 0.14, iris_weight=-2.0, head_weight=1.0, iris_v_weight=1.5, head_v_weight=-0.5),
])
def test_tuner_reads_exactly_like_the_reader(settings):
    rng = np.random.default_rng(5)
    # Random looks all over the range, so every branch of the rule is hit.
    steps = [("centre", CENTRE, frames(rng, n=15))]
    for k in range(12):
        steps.append((f"look {k}", [LEFT, RIGHT, UP, CENTRE][k % 4],
                      frames(rng, n=25, noise=0.2, eye_h=0.0, eye_v=0.0, iris_h=0.5, iris_v=0.0, yaw=0.0, pitch=0.0)))
    rec = tune.recording_from_steps(steps)

    reader = DirectionReader(**settings.reader_kwargs())
    reader.set_centre(steps[0][2])
    expected = [reader.read(f) for _, _, fs in steps[1:] for f in fs]

    deltas, _, _ = tune._frames([rec])
    got = [tune.LABELS[c] for c in tune.read_frames(deltas, settings)]
    assert got == expected
    assert len(set(got)) == 4   # the random looks really did cover all four answers


def test_score_is_per_step_and_skips_the_first_centre():
    rng = np.random.default_rng(6)
    rec = tune.recording_from_steps(eyes_only_session(rng))
    result = tune.score([rec], tune.Settings.current())
    assert [r["step"] for r in result["steps"]][0] == "far left"
    assert result["frames"] == 8 * 20
    assert result["accuracy"] == 1.0


# --- the search --------------------------------------------------------------

def test_head_turn_is_picked_up_when_it_carries_the_signal():
    rng = np.random.default_rng(7)
    rec = tune.recording_from_steps(head_session(rng, yaw_sign=-1.0))
    current = tune.score([rec], tune.Settings.current())
    by_step = {r["step"]: r["correct"] / r["frames"] for r in current["steps"]}
    assert by_step["head left"] == 0.0 and by_step["head right"] == 0.0   # eyes only: head turns read as centre

    best = tune.search([rec])
    assert best.head_weight < 0                     # sign from the data, size from the grid
    assert best.iris_weight == 0.0                  # the iris adds nothing here, so it stays out
    tuned = tune.score([rec], best)
    assert tuned["accuracy"] >= 0.98
    assert all(r["correct"] == r["frames"] for r in tuned["steps"] if r["step"].startswith("head"))


def test_search_keeps_the_current_settings_when_they_are_as_good():
    rng = np.random.default_rng(8)
    rec = tune.recording_from_steps(eyes_only_session(rng))
    best = tune.search([rec])
    assert (best.h_threshold, best.v_threshold) == (tune.Settings.current().h_threshold,
                                                    tune.Settings.current().v_threshold)
    assert best.complexity() == (0, 0.0)


def test_search_prefers_the_smallest_weight_that_works():
    # Two recordings, same person: the pooled choice must work on both.
    recs = [tune.recording_from_steps(head_session(np.random.default_rng(s)), path=f"r{s}") for s in (9, 10)]
    best = tune.search(recs)
    assert best.head_weight < 0
    assert abs(best.head_weight) <= 1.0
    for rec in recs:
        assert tune.score([rec], best)["accuracy"] >= 0.98


# --- the command -------------------------------------------------------------

def test_command_reports_and_recommends(tmp_path, capsys):
    rng = np.random.default_rng(11)
    paths = []
    for k in range(2):
        steps = head_session(rng)
        data = {"version": 2, "centre": [0, 0], "thresholds": [0.16, 0.14], "steps": [
            {"name": n, "expected": e, "frames": len(fs), "seen": len(fs),
             "features": [[float(x) for x in f] for f in fs]} for n, e, fs in steps]}
        path = tmp_path / f"directions-{k}.json"
        path.write_text(json.dumps(data))
        paths.append(str(path))
    assert tune.main(paths) == 0
    out = capsys.readouterr().out
    assert "Recommended Config values" in out
    assert "dir_head_weight: float = -" in out
    assert "head left" in out and "natural right" in out
    assert "others only" in out            # leave-one-recording-out table
    assert "Best found adds head turn" in out
