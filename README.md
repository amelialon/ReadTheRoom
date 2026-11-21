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
| `readtheroom/server.py`, `static/` | Patient screen and nurse station |
| `readtheroom/config.py` | All timings and thresholds |

## Tests

```
python3 -m pytest
```

## Known limits

- The pages have no login: anyone on the same network can acknowledge requests or start calibration.
- Vision code (`features.py`, `camera.py`) and the sensor driver have not been run against real hardware yet.
