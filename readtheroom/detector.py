from __future__ import annotations

import json
import os

import numpy as np


class _OpenCVSession:
    """OpenCV's DNN module behind ONNX Runtime's run() call. QNX's
    python3-onnxruntime ships without its compiled module, so on the Pi this
    is what runs the model."""

    def __init__(self, cv2, model_path: str):
        # On the Pi 5, 2 cores run it nearly as fast as 4 and leave 2 for gaze.
        cv2.setNumThreads(2)
        self._net = cv2.dnn.readNetFromONNX(model_path)

    def run(self, _outputs, feeds):
        (blob,) = feeds.values()
        self._net.setInput(blob)
        return [self._net.forward()]


class SceneDetector:
    """Finds and names objects in a picture of the room.

    Runs a YOLO-World model exported by tools/export_yoloworld.py. The word
    list is baked into the model file, so this needs only ONNX Runtime (or
    OpenCV when ONNX Runtime will not import): no PyTorch and no text model.
    """

    def __init__(self, model_path: str, min_score: float = 0.25, overlap: float = 0.5):
        import cv2
        try:
            import onnxruntime as ort
        except ImportError:
            ort = None

        self._cv2 = cv2
        with open(os.path.splitext(model_path)[0] + ".json", encoding="utf-8") as f:
            meta = json.load(f)
        self.words: list = meta["words"]
        size = meta["size"]  # one number for a square model, else [height, width]
        self.size: tuple = (size, size) if isinstance(size, int) else tuple(size)
        self.min_score = min_score
        self.overlap = overlap
        if ort is not None:
            self._session = ort.InferenceSession(model_path, providers=["CPUExecutionProvider"])
            inp = self._session.get_inputs()[0]
            self._input = inp.name
            shape = list(inp.shape)[-2:]  # the sidecar can disagree with the file
            if all(isinstance(v, int) for v in shape):
                self.size = tuple(shape)
            self.backend = "onnxruntime"
        else:
            self._session = _OpenCVSession(cv2, model_path)
            self._input = "images"
            self.backend = "opencv"

    def _letterbox(self, frame_bgr):
        """Resize keeping the shape, pad to the model size. Returns (image, scale, pad_x, pad_y)."""
        cv2 = self._cv2
        h, w = frame_bgr.shape[:2]
        size_h, size_w = self.size
        scale = min(size_h / h, size_w / w)
        new_w, new_h = round(w * scale), round(h * scale)
        resized = cv2.resize(frame_bgr, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
        canvas = np.full((size_h, size_w, 3), 114, dtype=np.uint8)
        pad_x, pad_y = (size_w - new_w) // 2, (size_h - new_h) // 2
        canvas[pad_y:pad_y + new_h, pad_x:pad_x + new_w] = resized
        return canvas, scale, pad_x, pad_y

    def detect_jpeg(self, jpeg: bytes) -> list:
        frame = self._cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), self._cv2.IMREAD_COLOR)
        return [] if frame is None else self.detect(frame)

    def detect(self, frame_bgr) -> list:
        """[{'label', 'score', 'box': [x1, y1, x2, y2]}] in frame pixels, best first."""
        h, w = frame_bgr.shape[:2]
        image, scale, pad_x, pad_y = self._letterbox(frame_bgr)
        blob = image[:, :, ::-1].transpose(2, 0, 1)[None].astype(np.float32) / 255.0
        out = self._session.run(None, {self._input: np.ascontiguousarray(blob)})[0][0]
        out = out.T  # rows: cx, cy, w, h, then one score per word
        if out.shape[1] != 4 + len(self.words):
            raise ValueError("model output does not match its word list")
        scores = out[:, 4:]
        best = scores.argmax(axis=1)
        conf = scores[np.arange(len(best)), best]
        keep = conf >= self.min_score
        if not keep.any():
            return []
        boxes, conf, best = out[keep, :4], conf[keep], best[keep]
        x1 = (boxes[:, 0] - boxes[:, 2] / 2 - pad_x) / scale
        y1 = (boxes[:, 1] - boxes[:, 3] / 2 - pad_y) / scale
        x2 = (boxes[:, 0] + boxes[:, 2] / 2 - pad_x) / scale
        y2 = (boxes[:, 1] + boxes[:, 3] / 2 - pad_y) / scale
        xyxy = np.stack([x1.clip(0, w), y1.clip(0, h), x2.clip(0, w), y2.clip(0, h)], axis=1)
        results = []
        for cls in np.unique(best):  # suppress overlaps within each word
            idx = np.where(best == cls)[0]
            rects = [[float(b[0]), float(b[1]), float(b[2] - b[0]), float(b[3] - b[1])]
                     for b in xyxy[idx]]
            kept = self._cv2.dnn.NMSBoxes(rects, conf[idx].tolist(), self.min_score, self.overlap)
            for k in np.asarray(kept).reshape(-1):
                i = idx[int(k)]
                results.append({"label": self.words[int(cls)], "score": round(float(conf[i]), 3),
                                "box": [round(float(v), 1) for v in xyxy[i]]})
        results.sort(key=lambda r: -r["score"])
        return results
