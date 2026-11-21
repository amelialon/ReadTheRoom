"""Send photos of the room to the device so it can see what is at each spot.

Run this on the laptop whose camera faces the objects:

    python -m readtheroom.scene_sender --url http://DEVICE:8000 --camera 0
    python -m readtheroom.scene_sender --url http://DEVICE:8000 --photo room.jpg
"""
from __future__ import annotations

import argparse
import time
import urllib.error
import urllib.request


def send(url: str, jpeg: bytes) -> bool:
    request = urllib.request.Request(url.rstrip("/") + "/api/scene", data=jpeg,
                                     headers={"Content-Type": "image/jpeg"})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status == 200
    except (urllib.error.URLError, OSError) as exc:
        print(f"Could not reach the device: {exc}")
        return False


def main() -> None:
    p = argparse.ArgumentParser(prog="readtheroom.scene_sender")
    p.add_argument("--url", default="http://localhost:8000")
    p.add_argument("--camera", type=int, default=0)
    p.add_argument("--photo", help="send this one picture and stop")
    p.add_argument("--every", type=float, default=4.0, help="seconds between photos")
    args = p.parse_args()

    if args.photo:
        with open(args.photo, "rb") as f:
            print("Sent." if send(args.url, f.read()) else "Not sent.")
        return

    import cv2

    cap = cv2.VideoCapture(args.camera)
    if not cap.isOpened():
        raise SystemExit(f"Cannot open camera {args.camera}")
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
    print(f"Sending a photo to {args.url} every {args.every:.0f} s. Ctrl+C to stop.")
    try:
        while True:
            for _ in range(4):  # drop buffered frames so the photo is current
                cap.grab()
            ok, frame = cap.read()
            if ok:
                ok, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
                if ok:
                    send(args.url, jpeg.tobytes())
            time.sleep(args.every)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
