from __future__ import annotations

import os


class OpenCVCamera:
    """Webcam through OpenCV. Works on laptops; QNX says VideoCapture is not
    its camera path, so on the Pi expect to use FileFrameSource instead."""

    def __init__(self, index: int = 0, width: int = 1280, height: int = 720):
        import cv2

        self._cap = cv2.VideoCapture(index)
        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        if not self._cap.isOpened():
            raise RuntimeError(f"cannot open camera {index}")

    def read(self):
        ok, frame = self._cap.read()
        return frame if ok else None


class FileFrameSource:
    """Reads the newest JPEG written to a fixed path by a separate capture
    process (for example a small C program built on QNX's mjpeg_example).
    The writer must replace the file atomically: write a temp file, then rename.
    """

    def __init__(self, path: str):
        import cv2
        import numpy as np

        self._cv2, self._np = cv2, np
        self._path = path
        self._mtime = None

    def read(self):
        try:
            mtime = os.stat(self._path).st_mtime_ns
            if mtime == self._mtime:
                return None
            with open(self._path, "rb") as f:
                data = f.read()
        except OSError:
            return None
        frame = self._cv2.imdecode(self._np.frombuffer(data, self._np.uint8),
                                   self._cv2.IMREAD_COLOR)
        if frame is not None:
            self._mtime = mtime
        return frame
