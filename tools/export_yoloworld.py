"""Export YOLO-World with a fixed word list to ONNX (run on a laptop, not the Pi).

    .venv-yolo/bin/python tools/export_yoloworld.py

The word list is baked into the exported file, so the device only needs
ONNX Runtime to run it: no PyTorch, no text model. Re-run after changing
the words. Needs: pip install ultralytics onnx onnxruntime onnxslim
"""
from __future__ import annotations

import argparse
import json
import os
import shutil

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# "laptop" and "monitor" let the scene camera find the patient screen.
DEFAULT_WORDS = ["laptop", "monitor", "water bottle", "snack packet", "blanket", "sign", "cup", "book",
                 "remote control", "phone", "pillow", "tissue box", "pill bottle", "fan"]


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--weights", default="yolov8s-worldv2.pt")
    p.add_argument("--words", nargs="+", default=DEFAULT_WORDS)
    p.add_argument("--size", type=int, default=640)
    p.add_argument("--out", default=os.path.join(ROOT, "models", "scene.onnx"))
    args = p.parse_args()

    from ultralytics import YOLO

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    weights = os.path.join(os.path.dirname(args.out), args.weights)
    model = YOLO(weights if os.path.exists(weights) else args.weights)
    model.set_classes(args.words)
    exported = model.export(format="onnx", imgsz=args.size, simplify=True)
    shutil.move(exported, args.out)
    with open(os.path.splitext(args.out)[0] + ".json", "w", encoding="utf-8") as f:
        json.dump({"words": args.words, "size": args.size}, f, indent=1)
    print(f"Wrote {args.out} ({os.path.getsize(args.out) / 1e6:.1f} MB) "
          f"with {len(args.words)} words")


if __name__ == "__main__":
    main()
