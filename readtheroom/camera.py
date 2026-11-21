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
    process (qnx/capture.c on the Pi, or qnx/frame_relay.py as a last resort).
    The writer must replace the file atomically: write a temp file, then rename.

    A frame counts as new when the file's time, size or identity changes. Some
    filesystems only keep whole seconds (untested: QNX's may be one of them),
    which would hide all but one frame a second, so in that case the bytes
    themselves are compared.
    """

    def __init__(self, path: str):
        import cv2
        import numpy as np

        self._cv2, self._np = cv2, np
        self._path = path
        self._key = None
        self._data = None

    def read(self):
        try:
            st = os.stat(self._path)
            key = (st.st_mtime_ns, st.st_size, st.st_ino)
            coarse = st.st_mtime_ns % 1_000_000_000 == 0
            if key == self._key and not coarse:
                return None
            with open(self._path, "rb") as f:
                data = f.read()
        except OSError:
            return None
        if key == self._key and data == self._data:
            return None
        frame = self._cv2.imdecode(self._np.frombuffer(data, self._np.uint8),
                                   self._cv2.IMREAD_COLOR)
        if frame is not None:
            self._key, self._data = key, data
        return frame
