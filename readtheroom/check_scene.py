"""Run the object detector on a photo of the room and show what it finds.

    python -m readtheroom.check_scene photo.jpg
    python -m readtheroom.check_scene photo.jpg --min-score 0.1

Writes photo.detected.jpg next to the photo with the boxes drawn on it.
"""
from __future__ import annotations

import argparse
import os
import time

from .detector import SceneDetector


def main() -> None:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    p = argparse.ArgumentParser(prog="readtheroom.check_scene")
    p.add_argument("photo")
    p.add_argument("--model", default=os.path.join(root, "models", "scene.onnx"))
    p.add_argument("--min-score", type=float, default=0.25)
    args = p.parse_args()

    import cv2

    if not os.path.exists(args.model):
        raise SystemExit(f"No detector model at {args.model}. See README (object detection).")
    frame = cv2.imread(args.photo)
    if frame is None:
        raise SystemExit(f"Cannot read image {args.photo}")
    detector = SceneDetector(args.model, min_score=args.min_score)
    detector.detect(frame)  # first run includes start-up cost
    started = time.perf_counter()
    found = detector.detect(frame)
    print(f"Looking for: {', '.join(detector.words)}")
    print(f"{len(found)} found in {1000 * (time.perf_counter() - started):.0f} ms")
    for item in found:
        x1, y1, x2, y2 = (int(v) for v in item["box"])
        print(f"  {item['label']:<16} {item['score']:.2f}  at {item['box']}")
        cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 220, 255), 3)
        cv2.putText(frame, f"{item['label']} {item['score']:.2f}", (x1 + 4, max(24, y1 - 8)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 220, 255), 2)
    out = os.path.splitext(args.photo)[0] + ".detected.jpg"
    cv2.imwrite(out, frame)
    print(f"Marked-up copy: {out}")


if __name__ == "__main__":
    main()
