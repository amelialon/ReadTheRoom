from __future__ import annotations

import json
import os

import numpy as np


class SceneDetector:
    """Finds and names objects in a picture of the room.

    Runs a YOLO-World model exported by tools/export_yoloworld.py. The word
    list is baked into the model file, so this needs only ONNX Runtime and
    OpenCV: no PyTorch and no text model.
    """

    def __init__(self, model_path: str, min_score: float = 0.25, overlap: float = 0.5):
        import cv2
        import onnxruntime as ort

        self._cv2 = cv2
        with open(os.path.splitext(model_path)[0] + ".json", encoding="utf-8") as f:
            meta = json.load(f)
        self.words: list = meta["words"]
        self.size: int = meta["size"]
        self.min_score = min_score
        self.overlap = overlap
        self._session = ort.InferenceSession(model_path, providers=["CPUExecutionProvider"])
        self._input = self._session.get_inputs()[0].name

    def _letterbox(self, frame_bgr):
        """Resize keeping the shape, pad to a square. Returns (image, scale, pad_x, pad_y)."""
        cv2 = self._cv2
        h, w = frame_bgr.shape[:2]
        scale = min(self.size / h, self.size / w)
        new_w, new_h = round(w * scale), round(h * scale)
        resized = cv2.resize(frame_bgr, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
        canvas = np.full((self.size, self.size, 3), 114, dtype=np.uint8)
        pad_x, pad_y = (self.size - new_w) // 2, (self.size - new_h) // 2
        canvas[pad_y:pad_y + new_h, pad_x:pad_x + new_w] = resized
        return canvas, scale, pad_x, pad_y

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
