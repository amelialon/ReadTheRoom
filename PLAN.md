# Read the Room — execution plan

A bedside device for patients who cannot speak or use their hands. The patient looks at a real object, the device proposes the need ("Are you thirsty? Do you need water?"), the patient confirms with their eyes, and a specific message reaches a caregiver.

Target prizes: Best Use of QNX, Sun Life Health, PCBWay Hardware, Gemini API, overall. Optional cheap extras: GoDaddy domain, Tiger Data.

## 1. What we are building

### Physical setup
- Screen in front of the patient, webcam mounted on top of it, 50–70 cm from the face, at or just below eye level. The webcam cannot resolve eyes beyond about 1 m.
- 4–6 "zones" spread widely around the patient, at least 25° apart from their viewpoint (roughly 30 cm apart at 1 m, 55 cm at 2 m). Example: water glass far left, TV remote/TV far right, window or fan up-left, blanket down-right.
- A lamp lighting the patient's face from the front. Never a window behind them.
- Temperature/humidity sensor on the Pi (SHT3x or BME280 over I2C; avoid DHT22, its timing is unreliable from Python).

### Interaction flow
1. **Setup (caregiver, once, ~2 min).** For each zone the caregiver takes a phone photo of the object. Gemini returns a label and a suggested need ("water glass → thirsty?"); the caregiver accepts or edits it. Typed labels work offline.
2. **Calibration (patient, 60–90 s).** Screen says "look at the WATER GLASS"; we record 2 s per zone, plus "look at the screen" as the neutral zone. A k-NN classifier maps gaze features to zones. A validation pass shows per-zone accuracy.
3. **Glance.** A zone must win 80% of frames over 1.5 s. Then a chime plays and the prompt appears.
4. **Confirm.** YES on the far left of the screen, NO on the far right, each needing a 1.2 s hold with a filling progress ring. Centre is a rest area. 10 s with no answer cancels. An up/down mode covers locked-in patients, who usually keep only vertical eye movement.
5. **Send.** "Bed 4: thirsty, wants water" goes to the caregiver dashboard. The caregiver taps "on my way", and the patient's screen shows that acknowledgement.

### Needs with no object
Pain, repositioning, toilet and breathing trouble have nothing to look at, and they are the most important clinically. Holding gaze on a "MORE" corner of the screen opens a four-item menu (Pain / Move me / Toilet / Something else), selected the same way as yes/no. This is a stretch goal, but the pitch must mention it.

### Room context ("climate-based scanning")
- Sensor readings change which prompt is offered: looking at the window when the room is 27 °C gives "Are you too warm?"; at 18 °C gives "Are you cold?".
- If the room leaves a comfort range, the device proactively asks "The room is 27 °C. Are you too warm?" with the same yes/no.
- Time context: "no water request in 4 hours" raises thirst as the first guess.

## 2. Architecture

Everything safety-relevant runs on the Pi under QNX. No video leaves the device.

| Part | Runs on | Stack |
|---|---|---|
| Camera capture | Pi / QNX | QNX Camera library. Python helper if it accepts the webcam, else a small C process derived from QNX's `mjpeg_example` |
| Face + eye features | Pi / QNX | QNX's `mediapipe` package (this is our "QNX open-source AI module"): Face Landmarker for iris position, head pose, blink |
| Zone classifier + dwell state machine | Pi / QNX | Python, NumPy; smoothing, k-NN, "none" rejection |
| Patient UI | Pi / QNX, HDMI | PySDL2 (shown working in QNX's MediaPipe codelab) |
| Sensor | Pi / QNX | `smbus` on I2C bus 1, run as root |
| Alert sender | Pi / QNX | HTTP POST with a retry queue; local alarm sound if the network is down |
| Caregiver dashboard | Laptop or Vultr | Small web app: live alerts, acknowledge button, device heartbeat, history |
| Gemini | Cloud, setup only | Zone labelling from photos; end-of-shift summary of requests |

Backup for the AI part if Face Landmarker does not run in QNX's MediaPipe build: MediaPipe face detection plus the MobileGaze MobileOne-S0 model (4.8 MB, MIT) on QNX's `onnxruntime` package.

### Why QNX matters here (the judges' main criterion)
Past QNX winners visibly used real-time features; cloud-AI wrappers on a Pi did not win. Build these in and show them:
- Separate processes with priorities: alert delivery and the state machine above perception, perception above UI.
- A watchdog that restarts the perception process if it stalls, and a heartbeat so the dashboard shows "device offline" within seconds.
- An on-screen latency readout (frame → prompt, confirm → alert delivered) with a stated budget.
- Alerts are never lost: queued, retried, with a local audible fallback.
- Gemini is outside the real-time loop. Pull the network cable in the demo and the device still works.

## 3. First hour: tests that decide the architecture

1. `cat /etc/os-release; apk update; apk search mediapipe tflite-runtime onnxruntime` — confirm the image is the Sept 2026 one with these packages.
2. Plug the C270 into a **blue** USB port. `usb -vvv`, then `camera_example3_viewfinder -u 5`. If that fails, switch the sensor config to `usb_camera_jpeg.conf` and try `mjpeg_example -l`.
3. `sudo apk add mediapipe python3-pip sdl2-compat`, run the codelab import check, then try Face Landmarker on a still photo.
4. Open a PySDL2 window on HDMI.
5. Read the sensor over `smbus`. Play a WAV. `curl` a laptop endpoint.

Camera decision ladder, in order:
1. C270 works through the Python helper (needs modifying; the helper only accepts NV12 frames and a webcam sends YUYV/MJPEG).
2. C270 works through a C capture process that hands frames to Python.
3. Borrow a Logitech C920 (supported) or Pi Camera Module 3 (best supported, codelab works as written) from QNX.
4. Last resort: laptop webcam streams JPEGs to the Pi; inference, UI, sensor and alerts stay on QNX. Disclose this to judges.

Ask the QNX mentors: which modules count as the "AI module"; is the C270 known to work; can we borrow a C920, Camera Module 3 or SHT3x; does Face Landmarker run in their MediaPipe build; how best to show real-time behaviour.

## 4. Build order

Develop the vision pipeline on a laptop in plain Python with MediaPipe and NumPy behind a `get_frame()` interface, so it moves to the Pi by swapping only the camera source.

| Phase | Work | Done when |
|---|---|---|
| 0 | First-hour tests above | Camera route chosen |
| 1 | Face features → smoothing → calibration → k-NN on laptop | 4 zones classified at >90% in validation |
| 2 | Dwell state machine + yes/no UI | A full glance → confirm cycle works on laptop |
| 3 | Port to Pi; alert sender + minimal dashboard | End-to-end on QNX hardware |
| 4 | Sensor context, Gemini zone setup, acknowledgement back to patient | Demo script runs start to finish |
| 5 | Priorities, watchdog, latency readout, offline test | Cable-pull demo works |
| 6 | Stretch: MORE menu, up/down mode, shift summary, Tiger Data history | — |
| Last 4 h | Freeze features. Rehearse at the judging table with our own lamp. Record a backup video | — |

Workstreams (adjust to team size): (A) QNX platform — camera, packages, processes, sensor; (B) gaze pipeline and calibration; (C) patient UI and state machine; (D) dashboard, Gemini, pitch and enclosure.

## 5. Demo and pitch

Demo (2–3 min): a teammate sits as the patient, already calibrated. They look at the glass → prompt → look left for YES → the judge's phone or the laptop shows "Bed 4: thirsty". Then warm the sensor with a hand → "Are you too warm?". Then unplug the network → device keeps working and queues the alert. If a judge wants to try, run the 15-second 4-zone quick calibration.

Numbers for the pitch (verify the source before putting each on a slide):
- Patients with communication problems had 3× the odds of a preventable adverse event (Bartlett, CMAJ 2008).
- 37.7% of pain communications with ventilated patients failed (Happ 2011).
- 70.8% of ICU patients rated thirst their most intense symptom.
- Over half of ventilated patients could communicate if given a tool (Happ 2015).
- Dedicated eye-gaze devices cost about US$7,000–15,000; ours is about $100 of hardware (our estimate).

Positioning: a low-cost first layer for basic needs that complements full eye-gaze communication devices, not a replacement. The closest prior work (Eid 2016, SceneTalk 2017) was lab prototypes with simulated recognition or on-screen selection.

For PCBWay: show the "next version" — a carrier board with the sensor, an IR LED ring and IR camera for night use, speaker, and a bed-rail clamp enclosure.

## 6. Known weaknesses to be ready for

- Webcam gaze is coarse; zones must be staged far apart. Two objects on one table cannot be separated.
- Calibration breaks if the camera or bed moves; offer one-key recalibration and a "low confidence" state.
- Glasses glare and dim light degrade it; fall back to head direction automatically, and bring a lamp.
- Real patients may be supine, have tubes or tape on the face, or have limited eye movement. No clinical validation.
- A camera in a patient room raises consent questions; our answer is that all video is processed on the device and none is stored or sent.
