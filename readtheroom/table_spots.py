"""Find the spots on a device's table photo from another machine, and send
them back. For devices where the object detector cannot run (QNX's OpenCV
rejects the model): the laptop fetches the photo, runs the detector, and
posts the screen and every object it knows a question for as targets.

    python -m readtheroom.table_spots --url http://<device>:8080
    python -m readtheroom.table_spots --url http://<device>:8080 --watch

--watch keeps going and repeats whenever the device takes a new photo.
Spots already on the device are left alone unless --replace is given.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
import urllib.error
import urllib.request

from .detector import SceneDetector
from .prompts import load_needs
from .table import Table


def fetch(url: str, timeout: float = 10.0):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read()


def post(url: str, body: dict, timeout: float = 10.0) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"{}")


def state(url: str) -> dict:
    """One snapshot from the device's event stream."""
    req = urllib.request.Request(url + "/events?role=nurse")
    with urllib.request.urlopen(req, timeout=10) as r:
        deadline = time.time() + 5
        while time.time() < deadline:
            line = r.readline()
            if line.startswith(b"data: "):
                return json.loads(line[6:])
    raise RuntimeError("the device sent no state")


def main(argv=None) -> None:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    p = argparse.ArgumentParser(prog="readtheroom.table_spots")
    p.add_argument("--url", required=True, help="the device, e.g. http://172.23.43.63:8080")
    p.add_argument("--model", default=os.path.join(root, "models", "scene.onnx"))
    p.add_argument("--zones", default=os.path.join(root, "config", "zones.json"))
    p.add_argument("--watch", action="store_true", help="keep going; redo after every new photo")
    p.add_argument("--replace", action="store_true", help="overwrite spots already on the device")
    args = p.parse_args(argv)

    detector = SceneDetector(args.model)
    needs = load_needs(args.zones)
    print(f"Detector: {detector.backend}, {len(detector.words)} words")
    done_photo = None
    while True:
        try:
            s = state(args.url)
            tb = s.get("table")
            if tb is None:
                sys.exit("the device is not in table mode")
            if tb["photo_id"] is None:
                print("No table photo on the device yet.")
            elif tb["photo_id"] != done_photo:
                if tb["targets"] and not args.replace:
                    print(f"Photo {tb['photo_id']} already has {len(tb['targets'])} spots; leaving them (use --replace).")
                else:
                    jpeg = fetch(args.url + "/table.jpg?id=" + str(tb["photo_id"]))
                    with tempfile.TemporaryDirectory() as tmp:
                        table = Table(os.path.join(tmp, "table"), needs)
                        suggested = table.set_photo(jpeg, detector)
                    found = ", ".join(f"{f['label']} {f['score']:.2f}" for f in table.found) or "nothing"
                    print(f"Photo {tb['photo_id']}: saw {found}")
                    if suggested:
                        post(args.url + "/api/table/targets", {"targets": suggested})
                        print("Sent spots: " + ", ".join(t["label"] for t in suggested))
                    else:
                        print("Nothing to send; mark the spots by hand on the caregiver page.")
                done_photo = tb["photo_id"]
        except (urllib.error.URLError, OSError, RuntimeError, ValueError) as exc:
            print(f"Could not reach the device: {exc}")
        if not args.watch:
            return
        time.sleep(3)


if __name__ == "__main__":
    main()
