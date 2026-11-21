# Read the Room

A bedside device for patients who cannot speak or use their hands. The patient looks at a real object, the device asks about the need ("Are you thirsty?"), the patient answers YES or NO with their eyes, and a specific message reaches the nurse station. See [PLAN.md](PLAN.md) for the design and build order.

## Try it without a camera

Needs only Python 3 and NumPy.

```
python3 -m readtheroom --simulate
```

Open `http://localhost:8000/` (patient screen) and `http://localhost:8000/nurse` (nurse station) in two windows. The nurse page has buttons that stand in for where the patient is looking: press "water bottle", wait for the question, press "YES".

## Run with a webcam (laptop)

One-time setup:

```
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
curl -L -o models/face_landmarker.task https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task
```

Check the camera and face model first. It prints the frame rate, brightness and how often the eyes were readable:

```
.venv/bin/python -m readtheroom.check_camera
```

On macOS the first run asks for camera permission for your terminal (or VS Code). Allow it in System Settings > Privacy & Security > Camera, then restart the terminal.

Then run the app:

```
.venv/bin/python -m readtheroom --camera 0
```

Press "Start calibration" on the nurse page and follow the instructions on the patient screen. Calibration is saved in `data/` and reused on the next start.

## Object detection (the scene camera)

A second camera that faces the objects lets the device notice when the object at a spot changes: swap the water bottle for a cup and the question changes with it. A YOLO-World model with a fixed word list does the recognising.

One-time export on a laptop (needs PyTorch, so it has its own environment). The result is one file that needs only ONNX Runtime:

```
python3 -m venv .venv-yolo
.venv-yolo/bin/pip install ultralytics onnx onnxruntime onnxslim
.venv-yolo/bin/python tools/export_yoloworld.py
```

That writes `models/scene.onnx` and `models/scene.json`. The app turns object detection on when it finds that file. Try the detector on a photo first:

```
.venv/bin/python -m readtheroom.check_scene photo.jpg
```

Then, on the laptop whose camera faces the objects, send a photo to the device every few seconds:

```
.venv/bin/python -m readtheroom.scene_sender --url http://DEVICE:8000 --camera 0
```

How it behaves:

- Each zone in `config/zones.json` names the object expected at its spot (`detect`). The first photo that shows it fixes where that spot is.
- If a different object from the `needs` table sits there in two photos in a row, the zone asks about that object instead.
- If the detector just misses the object, nothing changes.
- The nurse page shows what the last photo contained and what each spot currently holds.

The words in `zones.json` must be in the exported model's word list; the app says so at start-up if they are not. YOLO-World cannot be exported to TFLite, so on the Pi it has to run on QNX's `onnxruntime` package.

## Keeping it running

```
.venv/bin/python -m readtheroom.supervisor -- --camera 0
```

The supervisor starts the app and restarts it if it exits or its main loop stops for more than 5 seconds. Open requests are saved to disk, so they are still on the nurse page after a restart, marked overdue.

## Run on the QNX Raspberry Pi

Not yet tested on the device. Expected steps:

```
sudo apk add mediapipe python3-pip
python3 -m readtheroom --frame-file /tmp/frame.jpg
```

QNX does not expose the webcam through OpenCV, so `--frame-file` reads frames that a separate capture program writes (write to a temporary file, then rename). That capture program is not written yet. For the temperature sensor (SHT3x on I2C bus 1) the app must run as root.

## Layout

| File | Purpose |
|---|---|
| `config/zones.json` | The objects, their questions and the message the nurse receives |
| `readtheroom/features.py` | Camera frame → eye and head measurements (MediaPipe) |
| `readtheroom/zones.py`, `calibration.py` | Learns which measurements mean which object |
| `readtheroom/dwell.py`, `session.py` | Glance → question → YES/NO → alert, with back-off |
| `readtheroom/alerts.py` | Requests, acknowledgement, escalation after 60 s, saved to disk |
| `readtheroom/detector.py`, `scene.py`, `scene_sender.py` | Object detection on scene photos and tracking what is at each spot |
| `readtheroom/supervisor.py` | Restarts the app if it stops |
| `readtheroom/server.py`, `static/` | Patient screen and nurse station |
| `readtheroom/config.py` | All timings and thresholds |

## Tests

```
python3 -m pytest
```

## Known limits

- The pages have no login: anyone on the same network can acknowledge requests or start calibration.
- Vision code (`features.py`, `camera.py`) and the sensor driver have not been run against real hardware yet.
