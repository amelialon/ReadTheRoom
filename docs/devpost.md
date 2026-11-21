# Devpost write-up

Paste the sections below into Devpost. Everything in `[FILL IN: ...]` depends on what happens at the event. Replace each one with what was measured or seen, or delete the sentence. Do not submit with a placeholder left in, and do not round a result up.

Sources for every statistic are in [pitch-facts.md](pitch-facts.md).

---

**Tagline (max ~140 characters):**

A bedside device for patients who can't speak or move their hands: look at the water, answer YES with your eyes, and a nurse is told.

---

## Inspiration

A patient on a ventilator is often awake, thirsty and unable to say so. They cannot speak past the tube, and many cannot press a call button. In one study of 2,671 ventilated ICU patients, more than half were awake and responsive enough that a communication tool could have served them (Happ et al., 2015). In another, more than a third of exchanges about pain between nurses and patients who could not speak did not get through (Happ et al., 2011). A Quebec study of 2,355 hospital charts found that patients with a communication problem had three times the odds of a preventable adverse event (Bartlett et al., CMAJ 2008).

Eye-tracking speech devices exist, and they are good, but they are expensive, need training, and are rarely at the bedside on the first night. We wanted the simplest thing that could work in between: no typing, no menus. The patient already looks at the water when they want water. The room is the interface.

## What it does

1. **Look.** Objects that stand for needs sit around the bed: a water bottle, a snack packet, a blanket, and a card that says PAIN. A camera watches the patient's face. When they hold their gaze on one object for about a second and a half, the device notices.
2. **Ask.** The screen plays a chime and asks one question: "Are you thirsty? Do you need water?"
3. **Confirm.** YES is on the left of the screen and NO is on the right. The patient looks at one and holds. A bar fills so they can see it working. Nothing is sent without a held YES, so just glancing at the bottle never pages anyone. After a NO, the device stops asking about that object for three minutes.
4. **Tell the nurse.** The nurse station shows "Bed 4: patient is thirsty and wants water" and beeps. The nurse taps "On my way", and the patient's screen says "Your nurse is on the way". If nobody answers within 60 seconds, the request turns red and is marked overdue. PAIN is marked urgent from the start.

Three things make it more than a gaze button:

- **It notices when the room changes.** A second camera looks at the objects, and an on-device object detector (YOLO-World) checks what is at each spot. Swap the water bottle for a cup and the question becomes "Are you thirsty? Do you want a drink?" without anyone reconfiguring anything.
- **It uses the room temperature.** With a temperature sensor attached, looking at the blanket in a warm room asks "Are you too warm?" instead of "Do you want your blanket adjusted?", and if the room leaves the comfort range the device asks on its own.
- **It is built to fail safely.** Requests are saved to disk the moment they are confirmed. A watchdog restarts the app if it crashes or hangs, and the open requests come back marked overdue. If the camera stalls or loses the patient's face, both screens say so instead of pretending to work.

**What works today:** `[FILL IN: one honest sentence, e.g. "The full flow runs on a laptop with a webcam, and on the Raspberry Pi under QNX we got X working." Before the event the true statement is: the full flow works on a laptop in simulation and with a laptop webcam; nothing has run on the Pi; the temperature sensor driver is untested on hardware.]`

## How we built it

- **Gaze without geometry.** MediaPipe Face Landmarker gives eye-direction scores, iris positions and head pose for each frame. We do not compute where the eyes point in 3D. During a short calibration the patient looks at each object and at YES, NO and the screen centre, twice. A k-nearest-neighbour classifier learns what their eyes and head look like for each target, and rejects anything far from what it saw, so looking elsewhere means nothing. The second look at each target is used to test what was learned from the first, and the nurse page shows that score per target and warns when two objects are too close together.
- **Never act on one frame.** A target has to win 80% of the frames over a full time window (1.5 s to open a question, 1.2 s to answer it), and answers are ignored for the first second after a question appears. Blinks are dropped. A gap in the camera feed restarts the hold.
- **Requests that survive.** Confirmed requests are written to disk with an atomic file replace. Unanswered ones escalate after 60 seconds. On restart, anything still open is loaded and shown as overdue.
- **Watchdog.** A separate supervisor process starts the app, polls a health endpoint, and restarts the app if it exits or its main loop stops publishing for 5 seconds.
- **Scene camera.** YOLO-World (the small v2 model) is exported once on a laptop to a single ONNX file with our word list built in, so the device needs only ONNX Runtime and OpenCV, with no PyTorch and no text model. Detection runs in its own thread so a slow scan never delays reading the eyes. A spot changes its question only after two photos in a row show the new object; a missed detection changes nothing.
- **Screens.** The patient screen and nurse station are two plain web pages served by the device with Python's standard library, updated over Server-Sent Events. If the device stops sending, both pages say so within three seconds.
- **Tests.** The dwell logic, the question flow, escalation, restart recovery, the classifier and the scene tracker are covered by automated tests that run without a camera.
- **On the Raspberry Pi 5 with QNX:** `[FILL IN: what actually ran on QNX — which camera route worked (C270 via QNX camera library, C capture program, borrowed camera, or laptop streaming frames), which QNX packages were used for MediaPipe and ONNX Runtime, whether the SHT3x sensor was read. If the laptop still did part of the work in the demo, say which part.]`
- **Gemini:** `[FILL IN only if integrated: "At setup time, Gemini turns a detected object plus the room context into the question wording. It is not in the real-time loop; the device works with the network unplugged." Otherwise delete this bullet and remove Gemini from "Built with".]`

## Challenges we ran into

- **A webcam cannot tell which of two nearby objects you are looking at.** We stopped trying to estimate a precise gaze point and instead learn a few widely spaced targets per patient, with an explicit "none of these" answer. The price is that objects must be staged well apart.
- **Looking is not asking.** People look at things all the time. The first version would have paged a nurse every time the patient glanced at the bottle. The held YES, the ignore period right after the question appears, and the three-minute back-off after a NO all came from that.
- **Open-vocabulary detection on a small device.** YOLO-World normally needs PyTorch and a text encoder. Baking the word list into the exported model removed both, at the cost of re-exporting when the words change.
- **Lighting.** Glare on glasses and a bright window behind the patient both make the eyes unreadable. We added a camera view for positioning that says whether the face is too far, too close or too dark, and we bring our own lamp.
- `[FILL IN: the real problems from the weekend — QNX camera support for the C270, package availability, I2C access, frame rate on the Pi — and what you did about each.]`

## Accomplishments that we're proud of

- A person who has never seen the device can ask for water with their eyes after a calibration of under a minute. `[FILL IN: confirm with someone outside the team at the event, and give the calibration time you measured.]`
- The device says when it cannot do its job: no face in view, camera too slow, nurse station not connected, calibration not reliable for a particular object. In a medical setting a silent failure is the dangerous kind.
- Killing the app in the middle of a request loses nothing. The watchdog brings it back and the request is still on the nurse's screen, marked overdue.
- `[FILL IN: measured numbers, only if measured — frames per second on the Pi, time from holding YES to the request appearing on the nurse station, repeat-check scores per target.]`

## What we learned

- The hard part of an assistive device is not detecting intent; it is avoiding false alarms without making real requests slow. Most of our logic is about when not to act.
- Calibrating to a few targets per person is far more reliable on cheap hardware than trying to compute a general gaze direction.
- The needs that matter most clinically, pain and breathing trouble, have no object to look at. A printed PAIN card is a crude answer, and it is the part we would redesign first.
- `[FILL IN: what you learned about QNX — processes and priorities, the camera stack, what was different from Linux.]`

## What's next

- **Test with the people it is for.** We have tested only on ourselves, sitting upright. Real patients lie flat, have tubes and tape on their faces, and tire quickly. The next step is feedback from ICU nurses and speech-language pathologists, then a supervised usability study. This is not a medical device and has had no clinical validation.
- **Needs with no object:** a "more" corner on the screen that opens a short menu (pain, move me, toilet, something else), and an up/down answer mode for patients who keep only vertical eye movement.
- **Night use:** an infrared camera and infrared lighting, so it works in a dark room without a lamp in the patient's face.
- **Fit into the ward:** an output that plugs into the existing nurse call system instead of a separate web page, and a login on the nurse station. Today anyone on the same network can open it.
- **Purpose-built hardware:** one carrier board with the sensor, camera, lighting and speaker, in a wipe-clean enclosure that clamps to the bed rail.
- `[FILL IN if not done at the event: running the whole pipeline on the Pi under QNX; reading the temperature sensor on hardware; Gemini writing the question wording at setup.]`

## Built with

Matches the repository. Remove anything the final demo did not use.

- Python 3
- NumPy
- MediaPipe (Face Landmarker)
- OpenCV
- ONNX Runtime
- YOLO-World (Ultralytics `yolov8s-worldv2`, exported to ONNX)
- HTML, CSS and JavaScript with Server-Sent Events (no web framework)
- pytest
- Raspberry Pi 5 `[FILL IN: keep only if it ran on the Pi]`
- QNX `[FILL IN: keep only if it ran on QNX; give the version]`
- Logitech C270 webcam `[FILL IN: or the camera actually used]`
- Sensirion SHT3x temperature and humidity sensor (I2C) `[FILL IN: keep only if read on hardware]`
- Gemini API `[FILL IN: keep only if integrated]`

## Submission checklist

- `[FILL IN: team member names and Devpost accounts]`
- `[FILL IN: repository link, and make it public if the rules require]`
- `[FILL IN: demo video link. Record it before judging starts, with the lamp on, as a backup for a live failure.]`
- `[FILL IN: prize tracks selected on Devpost — see prize-notes.md for which are honest to enter]`
- `[FILL IN: photo of the physical setup for the gallery]`
- One sentence somewhere visible: "Prototype only. Not a medical device. Not tested with patients."
