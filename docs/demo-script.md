# Demo script

A 3-minute live demo and pitch for three people, a 60-second version, what to do when each thing fails, and the ten questions to expect.

Read "Things the code does that will surprise you on stage" before the first rehearsal. Each item there has cost us a rehearsal or would have.

## Roles

| Role | Does | Needs |
|---|---|---|
| **P — Patient** | Sits in the chair, does not speak or move hands during the demo. Calibrated in advance. | Steady head, the lamp on their face. |
| **N — Narrator** | Speaks the whole pitch. Stands beside the patient, never between the patient and the objects or the lamp. | This script memorised. |
| **O — Operator** | Holds the nurse station (laptop or phone), taps "On my way", swaps the bottle for the cup, warms the sensor, kills the app. | A terminal with the supervisor's output visible. |

`[FILL IN: who takes which role. The patient should be whoever calibrates most reliably, decided by trying all three.]`

## Setup, before any judge arrives

Layout: screen and face camera 50–70 cm in front of P at eye level. Water bottle far left, snack packet far right, blanket low on one side, PAIN card high on the other. Objects at least 25° apart as P sees them. Lamp in front of P, nothing bright behind. Scene camera facing the objects.

1. Start the app under the supervisor, so the kill step works:
   `.venv/bin/python -m readtheroom.supervisor -- --camera 0` `[FILL IN: the Pi command, once it exists]`
2. Open the patient screen on the screen in front of P, and the nurse station on O's device. **Click or tap once on each page.** Browsers keep sound off until a page has been touched; without this there is no chime and no nurse beep.
3. On the nurse station press "Show camera view", check it says the face size and brightness are good, then hide it.
4. "Start calibration". It walks through 7 targets twice, about 40 seconds. Read the repeat-check line. If any target is flagged NOT RELIABLE, move it further from its neighbours and calibrate again.
5. **Copy `data/calibration.json` to `data/calibration.good.json`.** A judge who tries the device overwrites P's calibration.
6. Start the scene sender on the laptop facing the objects: `.venv/bin/python -m readtheroom.scene_sender --url http://DEVICE:8000 --camera 0`. Wait until the nurse station's "Room scan" section lists the water spot as "water bottle". If it says "not found yet", move the bottle or the camera until it is found.
7. Do one full pass (bottle → YES → "On my way"). Check there are no open requests left on the nurse station.
8. Have the cup within O's reach, and the terminal showing the supervisor's line `app started (pid …)`.

## The 3-minute demo

Times are targets. The device sets its own pace in places; N keeps talking through those.

| Time | N says | P and O do | What the device shows |
|---|---|---|---|
| **0:00–0:25** | "Imagine you're awake in intensive care with a breathing tube. You can't speak. Your hands are too weak for the call button. You're thirsty. In a study of twenty-six hundred ventilated patients, more than half were awake enough to communicate and had no good way to do it. This is [P]. For the next three minutes they can't speak or move their hands." | P sits still, looking at the screen centre. | Patient screen: "Look at what you need". |
| **0:25–0:50** | "They want water. So they do what anyone does: they look at it." *(pause while the bar fills)* "The device asks one question." *(chime)* "Yes is on the left, no is on the right. They look, and hold." | P looks at the bottle and holds about 2 s. When the question appears, P waits a beat, then looks at YES and holds until the bar fills. | Bar fills under "water bottle" → chime → "Are you thirsty? Do you need water?" → YES fills → "Sent to your nurse". |
| **0:50–1:10** | "At the nurse station: Bed 4, patient is thirsty and wants water. Not just 'bed 4 is calling'. The nurse answers…" *(O taps)* "…and the patient is told someone is coming. If nobody had answered in a minute, it would turn red and keep alarming." | O holds up the nurse station, then taps "On my way". | Nurse: new request card and a beep. Patient screen: "Your nurse is on the way (is thirsty and wants water)". |
| **1:10–1:40** | "Nothing gets sent just because you looked at something. It needs a held yes. And the room isn't fixed. A second camera watches the objects, with an object detector running on the device. Watch what happens when a visitor swaps the bottle for a cup." *(keep talking for about ten seconds: "It waits for two photos in a row before it believes the change, so one bad detection can't rewrite the questions.")* "Now they look at the same spot." | O replaces the bottle with the cup **in the same place**, then watches "Room scan" on the nurse station and nods to P when the spot reads "cup (was water bottle)". P then looks at the cup. P answers **NO** this time. | Room scan updates. Patient screen: bar under "cup" → "Are you thirsty? Do you want a drink?" → NO fills → back to idle. |
| **1:40–2:05** | "It also knows about the room. There's a temperature sensor here." *(O warms it)* "The room has gone above twenty-six degrees, so it asks by itself." | O warms the sensor (cup a hand around it, or breathe on it) until the nurse station's temperature chip passes 26 °C. P keeps looking at the screen centre, then answers YES. O does **not** acknowledge this request. | "The room is 27°C. Are you too warm?" → YES → nurse: "Bed 4: patient feels too warm (room is 27°C)". |
| **2:05–2:35** | "That request is still waiting. A device like this must not lose it. So I'm going to crash it." *(O kills the app)* "The screens notice within three seconds. A watchdog restarts it…" *(wait)* "…and the request is back, marked overdue, because it has now waited through a crash." | O runs `kill <pid>` using the pid from the supervisor's last "app started" line. Then holds up the nurse station. | Both screens: "Device offline" / "Lost connection to the device". Then they reconnect. Nurse: the too-warm request, red, "OVERDUE", three beeps. |
| **2:35–3:00** | "This is a prototype. It's been tested on us, not on patients, and the objects have to be spaced well apart. `[FILL IN one true sentence about the hardware, e.g. 'Everything you saw ran on this Raspberry Pi under QNX.']` Dedicated eye-tracking speech devices do far more, and cost thousands. This is the first layer: water, pain, too warm, with a webcam. People who can't speak still look at what they need. We made the room listen." | O taps "On my way" on the overdue request. P finally speaks: "Thank you." | Request turns green. |

### If only the laptop version works

Everything above runs on a laptop except the sensor step: on a laptop there is no temperature sensor, and the typed temperature box exists only in simulation mode. Options, in order:

1. Skip the temperature step and use the time on the crash step and the closing. Say: "With the sensor attached it also asks 'are you too warm' when the room heats up."
2. Show it in a second, simulated copy and say so. Start it in advance on another port with its own data folder: `python3 -m readtheroom --simulate --port 8001 --data-dir data-sim`. On its nurse page, set the temperature to 27 and press "Screen centre". Say: "This copy is in simulation mode; I'm typing the temperature in."

In either case the last line of the pitch must say it ran on a laptop.

## The 60-second version

For a judge who says "I've got a minute." One beat only: the core loop. O has the nurse station ready.

| Time | N says | P and O do |
|---|---|---|
| 0:00–0:12 | "More than half of ventilated ICU patients are awake but can't speak or press a call button. [P] can't speak or move right now, and wants water." | P looks at the screen centre. |
| 0:12–0:35 | "So they look at it. The device asks. They answer yes with their eyes." | P: bottle, then YES. |
| 0:35–0:48 | "The nurse sees exactly what's wanted, answers, and the patient is told. Unanswered requests escalate after a minute and survive a crash." | O shows the request, taps "On my way". |
| 0:48–1:00 | "It also changes its questions when the objects or the room temperature change. Prototype, tested only on us. `[FILL IN: 'Runs on a Raspberry Pi under QNX' if true.]` Want to see it crash and recover?" | — |

The closing question earns the extra minute if they have one.

## Things the code does that will surprise you on stage

1. **A NO or a timeout silences that object for 3 minutes.** If P fails to answer within 10 seconds, or answers NO, looking at that object does nothing for 180 seconds. In the script, the NO is used on the cup on purpose, and that spot is not used again. If it happens by accident: kill the app (the back-off is not saved, so the restart clears it), or swap the object at that spot (a changed object lifts the back-off).
2. **The unprompted "too warm" question is asked at most once every 15 minutes.** Rehearsing the temperature step uses it up. Restart the app before each judge so it is fresh. Looking at the blanket in a warm room still asks "Are you too warm?" at any time (unless the blanket is on back-off), so that is the backup for this step.
3. **The unprompted question appears only when P's face is visible and P is not dwelling on an object.** P must look at the screen centre during the warming.
4. **YES is ignored for the first second after a question appears.** P should wait a beat before looking left. Rushing does not make it faster.
5. **The swap needs two scene photos in a row, sent every 4 seconds.** Expect 8–12 seconds after the cup is placed. Use `--every 2` on the scene sender to halve it. The cup must overlap where the bottle stood; a cup placed beside the spot is ignored.
6. **After a restart the device forgets what it learned about the spots** and relearns from the next photo, looking for the *original* object. If the cup is still there, the water spot shows "not found yet" and goes back to asking the water-bottle question. This does not break the script (the spot is not used after the crash), but for the next judge put the bottle back and press "Learn spots again".
7. **Open requests come back as OVERDUE after a restart**, immediately, not after 60 seconds. That is intended; say so.
8. **Restart time.** In simulation mode on a laptop the app was back about 1 second after being killed. With the camera and the face model it takes longer. `[FILL IN: time it on the demo hardware, and make N's line long enough to cover it.]`
9. **Sound needs one tap on each page** after every page reload.
10. **Anyone on the same network can open the nurse station** and acknowledge requests. Do not put the address on a slide.

## When something fails live

| Failure | How you notice | What to do | What N says |
|---|---|---|---|
| **Bad lighting** (overhead glare, window behind, dim hall) | Before the demo: camera view says "too dark" or "Eyes not readable". During: bar under an object never fills; "Cannot see your face". | Turn P so the brightest light is in front. Lamp on, 50 cm from the face, slightly off to one side. Glasses off if they glare. Recalibrate; it takes 40 seconds. | "Webcams need light on the face. The next version uses infrared so it works in the dark." |
| **Calibration drift** (P shifted, screen bumped, table moved) | Wrong object's label appears under the bar, or nothing registers. | First: P sits back exactly as calibrated. If still wrong, recalibrate on the spot and narrate it as a feature. Between judges: restore `calibration.good.json` and restart the app only if nothing physical moved. | "It learns each person's eyes in about forty seconds. Here's what that looks like." |
| **One object will not trigger** | Bar fills partly and drops. | Use a different object. PAIN is usually the easiest if it is placed high; the request arrives already marked URGENT, which is worth showing anyway. | "Pain is treated as urgent from the first second." |
| **Wi-Fi blocks devices from talking to each other** (common on event networks) | Nurse station on the phone shows "Connecting…" or will not load. | Plan A: phone hotspot, with the device and the nurse laptop both on it; test before judging. Plan B: both pages in two windows on the device itself (`localhost`), nurse window turned towards the judge. The scene sender also needs the network; on plan B run it on the same machine with a second camera, or skip the swap. | Nothing. Do not explain networks to a judge. |
| **Scene swap does not register** | Room scan still says "water bottle" after 15 seconds, or says "nothing it knows". | Move on. Do not wait. Afterwards check the cup overlaps the old spot and the model was exported (`models/scene.onnx` exists). | "The detector didn't catch it that time; it's built to change nothing when it's unsure, which is the safe direction." |
| **Sensor will not warm past 26 °C, or reads nothing** | Temperature chip stuck, or "No temperature". | Skip to the crash step. Never type a temperature into the simulation page and present it as the sensor. | "The sensor step needs a warmer hand than mine. In a warm room it asks 'are you too warm' on its own." |
| **App does not come back after the kill** | Screens stay on "Device offline" for more than ~15 seconds. | O starts it by hand in the terminal. Open requests are still loaded from disk, so the point survives. | "The request was written to disk the moment it was confirmed, so even a manual restart brings it back." |
| **Everything is broken** | — | Run simulation mode on any laptop: `python3 -m readtheroom --simulate`. O presses the gaze buttons on the nurse page while P acts it out, and N says it is simulated. Or play the backup video. | "The camera isn't cooperating with this lighting, so I'm standing in for the eye tracking with these buttons. Everything after the gaze is the real system." |
| **A judge wants to try it** | — | Yes, if there are two spare minutes. Seat them, show the camera view so they can see their face is found, run calibration (40 s), let them ask for water. Afterwards restore `calibration.good.json` and restart the app (kill it; the supervisor reloads the file). If there is no time, offer the YES/NO step only: they will be reading with P's calibration, so say it may not work. | "It calibrates to each person. Look at each thing it names and hold still." If it fails for them: "It's been tuned on three faces. That's exactly the testing it needs next." |

Record a backup video of one clean full pass before judging opens. `[FILL IN: where the video is.]`

## Ten hard questions

Answer in two or three sentences, then stop.

**1. How accurate is it?**
"We don't have a number we'd stand behind. After calibration the device tests itself on a second look at each target and shows the score, and it warns when a target is unreliable. `[FILL IN: the repeat-check scores you actually saw at the event, with how many people and what lighting.]` That is a self-check on three healthy students, not an accuracy study."

**2. Real patients lie flat and have tubes and tape on their faces. Does it work then?**
"We don't know; we've only tested sitting upright with clear faces. The face model needs to see both eyes, so tape or a mask over the cheeks may or may not break it. The camera would have to be on an arm above the bed. It's the first thing we'd test with clinicians, and it could be where this fails."

**3. A camera pointed at a patient in bed. What about privacy?**
"Frames are processed on the device and not saved; what's stored is the list of requests and the calibration numbers. There is one exception we should be upfront about: during setup, staff can switch on a camera view on the nurse station to position the camera. It's off by default and turns itself off after three minutes. A real deployment would need consent, a physical lens cover, and a login on that page, which it doesn't have today."

**4. Why not a tablet with an eye tracker, or Tobii?**
"For someone who needs full language, they should have one; those do far more than this. They're costly, need training and an assessment, and often aren't at the bedside in the first days. This covers a few basic needs with no training, and it tells a nurse who isn't in the room. We see it as the layer before a full device, not instead of it."

**5. What about false alarms? Nurses already have alarm fatigue.**
"That's what most of the logic is for. Looking at an object only asks a question; a request needs a held YES on a different part of the screen, and YES is ignored for the first second so a passing glance can't confirm. After a NO it stops asking about that object for three minutes. And each request says what is wanted, so the nurse can prioritise. We haven't measured a false-alarm rate."

**6. What's the regulatory path?**
"We're students and haven't had regulatory advice. A device that patients rely on to call for help would very likely be regulated as a medical device in Canada and need clinical evidence, and connecting to a hospital's nurse call system brings its own standards. The realistic next step is far smaller: a supervised usability study with clinicians."

**7. What happens when it fails?**
"It's designed to fail loudly. If it can't see the patient, or the camera stalls, or it's running too slowly, both screens say so, and the nurse station shows a banner. If the app crashes, a watchdog restarts it and open requests come back marked overdue. What it can't do yet is alarm on its own if the nurse station is unreachable; the patient's screen just says the nurse station isn't connected. It must never be someone's only way to call for help."

**8. Why QNX and not Linux?**
`[FILL IN after the port, and say only what you did.]` Honest version today: "The job is to get a request from a patient to a nurse in bounded time and survive faults, which is what a real-time OS is for: fixed priorities so the alert path can't be starved by the vision code, and a supervised restart. Right now our watchdog is a Python supervisor process, and we have not yet split the pipeline into prioritised processes. That is the next piece of work on QNX."

**9. How is this different from Google's Look to Speak?**
"Look to Speak has you narrow down a list of phrases by looking left or right several times, and the phone speaks to someone beside you. Here the topic comes from what you look at in the room, so it's one yes/no, and the message goes to a nurse somewhere else, with acknowledgement and escalation. Our yes/no step is the same left/right idea, and we're happy to say so."

**10. Is the AI running on the device, and what is it?**
"Two models, both local: MediaPipe's face landmark model for the eyes and head, and a YOLO-World object detector exported with a fixed word list so it runs on ONNX Runtime alone. On top of the face model is a small nearest-neighbour classifier trained on that one patient during calibration. No language model is in the loop. `[FILL IN: 'Both ran on the Pi' / 'the detector ran on the Pi and the scene photos came from a laptop camera' / 'this demo ran on a laptop' — whichever is true. If Gemini was integrated: 'Gemini is used once at setup to word the questions, never during use.']`"

Two more that came up in our own review:

**What does it cost?** "We haven't totalled it. It's a Raspberry Pi, a webcam and a small sensor. `[FILL IN: the real total once someone adds up the parts.]`" Do not say "$100" or quote a price range for eye-gaze devices; see [pitch-facts.md](pitch-facts.md).

**What about pain and needing the toilet? There's no object for those.** "Right, and those matter most. Today there's a printed PAIN card to look at, and it's flagged urgent. The plan is a corner of the screen that opens a short menu: pain, move me, toilet, something else. That isn't built."
