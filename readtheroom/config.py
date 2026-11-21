from __future__ import annotations

from dataclasses import dataclass

# Control zones live on the patient screen; every other zone is a real object.
REST, YES, NO = "rest", "yes", "no"
CONTROL_ZONES = (REST, YES, NO)


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

    escalate_after_s: float = 60.0
    face_ok_s: float = 2.0
    face_lost_notice_s: float = 30.0

    warm_above_c: float = 26.0
    cold_below_c: float = 19.0
    climate_reask_s: float = 900.0

    # Calibration
    settle_s: float = 1.5
    record_s: float = 2.0

    # Perception
    blink_threshold: float = 0.75
    ema_alpha: float = 0.4
    loop_hz: float = 20.0
    frame_stale_s: float = 1.0
    min_fps: float = 5.0           # below this, holds are read unreliably
