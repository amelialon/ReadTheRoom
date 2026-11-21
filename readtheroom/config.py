from __future__ import annotations

from dataclasses import dataclass

# Control zones live on the patient screen; every other zone is a real object.
REST, YES, NO = "rest", "yes", "no"
CONTROL_ZONES = (REST, YES, NO)
ANSWER_MODES = ("horizontal", "vertical")


@dataclass
class Config:
    bed: str = "Bed 4"

    # Glance: an object zone must win this share of frames over this window.
    glance_window_s: float = 1.5
    confirm_window_s: float = 1.2
    win_fraction: float = 0.8

    refractory_s: float = 1.0      # ignore yes/no right after a prompt appears
    prompt_timeout_s: float = 10.0
    backoff_s: float = 180.0       # ignore a zone after NO or a timeout
    sent_display_s: float = 4.0

    # Menu of needs that have no object. It opens on a held NO while idle;
    # the hold must be clearly longer than an answer (confirm_window_s).
    menu_open_s: float = 3.5
    menu_rearm_s: float = 0.5        # gaze must be off NO this long before a hold counts
    menu_step_timeout_s: float = 8.0  # no answer to a menu item closes the menu

    # "horizontal": YES left, NO right. "vertical": YES top, NO bottom, for
    # patients who can only move their eyes up and down.
    answer_mode: str = "horizontal"

    escalate_after_s: float = 60.0
    face_ok_s: float = 2.0
    face_lost_notice_s: float = 30.0

    warm_above_c: float = 26.0
    cold_below_c: float = 19.0
    climate_reask_s: float = 900.0

    # Quick mode: directions from one look at the centre (see directions.py)
    gaze_mode: str = "quick"       # or "trained": the guided routine, objects anywhere
    dir_h_threshold: float = 0.14
    dir_v_threshold: float = 0.14
    dir_adapt: float = 0.005
    # How much the iris position and the head angle add to the eye scores on
    # each axis, sign included. 0 = eye scores only. Values come from
    # `python -m readtheroom.tune_directions data/directions-*.json`.
    dir_iris_weight: float = 0.0
    dir_head_weight: float = 1.0
    dir_iris_v_weight: float = 0.0
    dir_head_v_weight: float = 0.0
    greet_settle_s: float = 0.8
    greet_record_s: float = 1.7
    greet_retry_s: float = 5.0

    # Calibration
    settle_s: float = 1.2
    record_s: float = 1.5
    recheck_warn: float = 0.7      # repeat-check score below this needs attention

    # Perception
    blink_threshold: float = 0.75
    ema_alpha: float = 0.4
    loop_hz: float = 30.0          # the camera's rate; the loop never waits for more
    frame_stale_s: float = 1.0
    preview_s: float = 180.0       # camera view switches itself off after this
    min_fps: float = 5.0           # below this, holds are read unreliably
