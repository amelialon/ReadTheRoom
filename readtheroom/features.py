from __future__ import annotations

import math

import numpy as np

# Eye-direction scores from MediaPipe's face model (0..1 each).
LOOK = ["eyeLookInLeft", "eyeLookOutLeft", "eyeLookUpLeft", "eyeLookDownLeft",
        "eyeLookInRight", "eyeLookOutRight", "eyeLookUpRight", "eyeLookDownRight"]
# Landmark indices: (iris centre, eye corner a, eye corner b)
EYES = [(468, 33, 133), (473, 362, 263)]
NOSE_TIP = 1
N_FEATURES = len(LOOK) + 2 * len(EYES) + 3 + 2


def iris_offset(iris, a, b):
    """Iris position relative to the eye corners: (along the eye, across it)."""
    ax, ay = a
    ex, ey = b[0] - ax, b[1] - ay
    width2 = ex * ex + ey * ey
    if width2 < 1e-12:
        return 0.5, 0.0
    ix, iy = iris[0] - ax, iris[1] - ay
    return (ix * ex + iy * ey) / width2, (ix * ey - iy * ex) / width2


def head_angles(matrix) -> tuple:
    """Yaw, pitch, roll (radians) from a 4x4 face transform. Convention only
    needs to be consistent, because zones are learned from calibration."""
    r = np.asarray(matrix, dtype=float)[:3, :3]
    yaw = math.atan2(r[0, 2], r[2, 2])
    pitch = math.asin(max(-1.0, min(1.0, -r[1, 2])))
    roll = math.atan2(r[1, 0], r[1, 1])
    return yaw, pitch, roll


class Ema:
    """Exponential smoothing of the feature vector; restarts after a gap."""

    def __init__(self, alpha: float):
        self.alpha = alpha
        self._value = None

    def __call__(self, x):
        if x is None:
            self._value = None
            return None
        if self._value is None:
            self._value = np.array(x, dtype=float)
        else:
            self._value = self.alpha * np.asarray(x, dtype=float) + (1 - self.alpha) * self._value
        return self._value.copy()


class FaceFeatureExtractor:
    """Frame -> gaze feature vector using MediaPipe Face Landmarker.

    Returns (face_present, features). Features are None when there is no
    face, and also when a face is present but the eyes are closed.
    """

    def __init__(self, model_path: str, blink_threshold: float = 0.75):
        import mediapipe as mp  # imported here so --simulate runs without it
        from mediapipe.tasks import python as mp_python
        from mediapipe.tasks.python import vision

        self._mp = mp
        self._blink_threshold = blink_threshold
        self._last_ts = -1
        # For the setup preview: where the face and eyes were in the last
        # frame, as fractions of the picture. None when no face was found.
        self.last_box = None
        self.last_eyes = None
        options = vision.FaceLandmarkerOptions(
            base_options=mp_python.BaseOptions(model_asset_path=model_path),
            running_mode=vision.RunningMode.VIDEO,
            num_faces=1,
            output_face_blendshapes=True,
            output_facial_transformation_matrixes=True,
        )
        self._landmarker = vision.FaceLandmarker.create_from_options(options)

    def __call__(self, frame_bgr, timestamp_ms: int):
        mp = self._mp
        ts = max(int(timestamp_ms), self._last_ts + 1)  # must strictly increase
        self._last_ts = ts
        rgb = np.ascontiguousarray(frame_bgr[:, :, ::-1])
        result = self._landmarker.detect_for_video(
            mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb), ts)
        if not result.face_landmarks:
            self.last_box = self.last_eyes = None
            return False, None
        lm = result.face_landmarks[0]
        xs, ys = [p.x for p in lm], [p.y for p in lm]
        self.last_box = (min(xs), min(ys), max(xs), max(ys))
        self.last_eyes = ([(lm[i].x, lm[i].y) for i, _, _ in EYES]
                          if len(lm) > max(i for i, _, _ in EYES) else None)
        if not result.face_blendshapes or not result.facial_transformation_matrixes:
            return True, None
        scores = {c.category_name: c.score for c in result.face_blendshapes[0]}
        blink = (scores.get("eyeBlinkLeft", 0.0) + scores.get("eyeBlinkRight", 0.0)) / 2
        if blink > self._blink_threshold:
            return True, None
        if len(lm) <= max(i for eye in EYES for i in eye):
            return True, None  # model without iris landmarks
        feat = [scores.get(name, 0.0) for name in LOOK]
        for iris, a, b in EYES:
            feat.extend(iris_offset((lm[iris].x, lm[iris].y), (lm[a].x, lm[a].y),
                                    (lm[b].x, lm[b].y)))
        feat.extend(head_angles(result.facial_transformation_matrixes[0]))
        feat.extend((lm[NOSE_TIP].x, lm[NOSE_TIP].y))
        return True, np.asarray(feat, dtype=float)
