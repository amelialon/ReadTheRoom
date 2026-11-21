from __future__ import annotations

import os


class OpenCVCamera:
    """Webcam through OpenCV. Works on laptops; QNX says VideoCapture is not
    its camera path, so on the Pi expect to use FileFrameSource instead.

    A background thread keeps only the newest frame. Without it, a camera
    that produces frames faster than the app takes them builds up a queue,
    and the device reacts to pictures that are more and more out of date.
    """

    def __init__(self, index: int = 0, width: int = 1280, height: int = 720):
        import threading

        import cv2

        self._cap = cv2.VideoCapture(index)
        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        self._cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)  # ignored by some drivers
        if not self._cap.isOpened():
            raise RuntimeError(f"cannot open camera {index}")
        self._lock = threading.Lock()
        self._frame = None
        self._fresh = False
        self.dropped = 0  # frames that arrived while the app was busy
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self) -> None:
        while True:
            ok, frame = self._cap.read()
            if not ok:
                continue
            with self._lock:
                if self._fresh:
                    self.dropped += 1
                self._frame, self._fresh = frame, True

    def read(self):
        """The newest frame, or None if nothing new arrived since the last call."""
        with self._lock:
            if not self._fresh:
                return None
            self._fresh = False
            return self._frame


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
