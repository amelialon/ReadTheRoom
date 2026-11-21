"""Quick check that the camera and the face model work together.

    python -m readtheroom.check_camera            # webcam 0, four seconds
    python -m readtheroom.check_camera --image photo.jpg
    python -m readtheroom.check_camera --list     # which number is which camera
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time

import numpy as np

from .features import N_FEATURES, FaceFeatureExtractor


def list_cameras(limit: int = 4) -> None:
    """Camera numbers change when a camera is plugged in, and OpenCV gives no
    names. Ask each one for a 1920x1080 picture: the Logitech C270 cannot go
    above 1280x720, while a built-in laptop camera usually can."""
    import cv2

    found = 0
    for index in range(limit):
        # OpenCV prints errors for camera numbers that do not exist; hide them.
        saved = os.dup(2)
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, 2)
        try:
            cap = cv2.VideoCapture(index)
            opened = cap.isOpened()
        finally:
            os.dup2(saved, 2)
            os.close(saved)
            os.close(devnull)
        if not opened:
            continue
        found += 1
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
        ok, frame = cap.read()
        cap.release()
        if not ok:
            print(f"--camera {index}: opened but gave no picture")
            continue
        height, width = frame.shape[:2]
        guess = ("likely the Logitech C270" if width <= 1280
                 else "likely a built-in or other high-resolution camera")
        print(f"--camera {index}: up to {width}x{height}, {guess}")
    if not found:
        print("No camera could be opened. Check the cable and camera permission.")


def main() -> None:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    p = argparse.ArgumentParser(prog="readtheroom.check_camera")
    p.add_argument("--camera", type=int, default=0)
    p.add_argument("--image", help="check a still photo instead of the webcam")
    p.add_argument("--seconds", type=float, default=4.0)
    p.add_argument("--list", action="store_true",
                   help="show the largest picture each camera number offers")
    p.add_argument("--model", default=os.path.join(root, "models", "face_landmarker.task"))
    args = p.parse_args()

    if args.list:
        list_cameras()
        return
    extractor = FaceFeatureExtractor(args.model)
    if args.image:
        import cv2

        frame = cv2.imread(args.image)
        if frame is None:
            raise SystemExit(f"Cannot read image {args.image}")
        face, feature = extractor(frame, 1)
        print(f"Image {frame.shape[1]}x{frame.shape[0]}: face found: {face}, "
              f"gaze readable: {feature is not None}")
        if feature is not None:
            assert len(feature) == N_FEATURES
            print("Measurements:", np.round(feature, 3).tolist())
        return

    from .camera import OpenCVCamera

    if sys.platform == "darwin":
        listing = subprocess.run(["system_profiler", "SPCameraDataType"],
                                 capture_output=True, text=True).stdout
        names = [line.strip().rstrip(":") for line in listing.splitlines()
                 if line.startswith("    ") and not line.startswith("     ") and line.rstrip().endswith(":")]
        print("Cameras macOS can see:", ", ".join(names) or "none")
    try:
        camera = OpenCVCamera(args.camera)
    except RuntimeError as exc:
        raise SystemExit(f"{exc}. On macOS, allow camera access for this terminal in "
                         "System Settings > Privacy & Security > Camera, then restart it.")
    frames = faces = usable = 0
    times = []
    frame = None
    end = time.monotonic() + args.seconds
    while time.monotonic() < end:
        latest = camera.read()
        if latest is None:
            continue
        frame = latest
        frames += 1
        started = time.perf_counter()
        face, feature = extractor(frame, int(time.monotonic() * 1000))
        times.append(time.perf_counter() - started)
        faces += face
        usable += feature is not None
    if not frames:
        raise SystemExit("The camera opened but produced no frames.")
    print(f"Frame size {frame.shape[1]}x{frame.shape[0]}, average brightness "
          f"{frame.mean():.0f}/255 (below about 60 is too dark)")
    print(f"{frames} frames in {args.seconds:.0f} s ({frames / args.seconds:.1f} per second)")
    print(f"Face found in {faces}, gaze readable in {usable}")
    print(f"Face model time per frame: {1000 * float(np.median(times)):.0f} ms")


if __name__ == "__main__":
    main()
