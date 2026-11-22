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
                 "remote control", "phone", "pillow", "tissue box", "pill bottle", "fan", "pen"]


def unshare_matmul_constants(path: str) -> None:
    """Give each MatMul its own copy of a constant it shares with others.
    OpenCV 4.x (QNX's package) cannot load the shared word-feature table."""
    import onnx

    model = onnx.load(path)
    graph = model.graph
    consts = {i.name: i for i in graph.initializer}
    seen: dict = {}
    for node in graph.node:
        if node.op_type != "MatMul":
            continue
        for k, name in enumerate(node.input):
            if name not in consts:
                continue
            seen[name] = seen.get(name, 0) + 1
            if seen[name] > 1:
                copy = onnx.TensorProto()
                copy.CopyFrom(consts[name])
                copy.name = f"{name}_copy{seen[name]}"
                graph.initializer.append(copy)
                node.input[k] = copy.name
    onnx.save(model, path)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--weights", default="yolov8s-worldv2.pt")
    p.add_argument("--words", nargs="+", default=DEFAULT_WORDS)
    # Height and width; 480 640 fits the C270's 4:3 frame with no padding.
    p.add_argument("--size", type=int, nargs="+", default=[480, 640])
    p.add_argument("--out", default=os.path.join(ROOT, "models", "scene.onnx"))
    args = p.parse_args()

    from ultralytics import YOLO

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    weights = os.path.join(os.path.dirname(args.out), args.weights)
    model = YOLO(weights if os.path.exists(weights) else args.weights)
    model.set_classes(args.words)
    size = args.size[0] if len(args.size) == 1 else args.size
    exported = model.export(format="onnx", imgsz=size, simplify=True)
    shutil.move(exported, args.out)
    unshare_matmul_constants(args.out)
    with open(os.path.splitext(args.out)[0] + ".json", "w", encoding="utf-8") as f:
        json.dump({"words": args.words, "size": size}, f, indent=1)
    print(f"Wrote {args.out} ({os.path.getsize(args.out) / 1e6:.1f} MB) "
          f"with {len(args.words)} words")


if __name__ == "__main__":
    main()
