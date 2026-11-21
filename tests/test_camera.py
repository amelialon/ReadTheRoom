import os

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from readtheroom.camera import FileFrameSource  # noqa: E402


def _write(path, value, mtime_ns=None):
    """Replace the frame file the way the capture program does."""
    ok, jpeg = cv2.imencode(".jpg", np.full((8, 8, 3), value, np.uint8))
    assert ok
    tmp = str(path) + ".tmp"
    with open(tmp, "wb") as f:
        f.write(jpeg.tobytes())
    if mtime_ns is not None:
        os.utime(tmp, ns=(mtime_ns, mtime_ns))
    os.replace(tmp, path)


def test_frame_file_gives_each_frame_once(tmp_path):
    path = tmp_path / "frame.jpg"
    source = FileFrameSource(str(path))
    assert source.read() is None  # nothing written yet
    _write(path, 40)
    assert source.read().shape == (8, 8, 3)
    assert source.read() is None  # same frame is not handed out twice
    _write(path, 200)
    assert source.read().mean() > 150


def test_frame_file_sees_new_frames_when_times_are_whole_seconds(tmp_path):
    path = tmp_path / "frame.jpg"
    source = FileFrameSource(str(path))
    second = 1_700_000_000 * 1_000_000_000
    _write(path, 40, second)
    assert source.read() is not None
    assert source.read() is None
    # Different picture of the same size written within the same second.
    _write(path, 41, second)
    assert os.stat(path).st_mtime_ns == second
    assert source.read() is not None
    assert source.read() is None


def test_frame_file_ignores_a_broken_file(tmp_path):
    path = tmp_path / "frame.jpg"
    source = FileFrameSource(str(path))
    path.write_bytes(b"not a picture")
    assert source.read() is None
    _write(path, 90)
    assert source.read() is not None
