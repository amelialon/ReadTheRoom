# Bringing Read the Room up on the QNX Raspberry Pi

**Nothing in this guide has been run on the Pi.** It was written from QNX's documentation and sample source code, without the device. Each command carries one of three labels:

| Label | Meaning |
|---|---|
| **DOCS [n]** | VERIFIED FROM QNX DOCS: the command or fact is in QNX's own documentation or sample code, source number *n* in the list at the end. We still have not run it ourselves. |
| **LAPTOP** | We ran this, but only on the Mac. |
| **GUESS** | UNVERIFIED GUESS: reasonable, not found in any QNX source. Expect to adjust it. |

Work top to bottom. Do not skip the checks; each one takes a minute and tells you which branch to take.

## What to bring

- This repository, plus the two files git does not carry: `models/face_landmarker.task` (needed) and `models/scene.onnx` with `models/scene.json` (only for object detection).
- The C270, a lamp, an HDMI screen, USB keyboard, the SHT3x and four jumper wires.
- A laptop on the same network as the Pi.

## Step 1 — Log in (5 min)

| Do | Label |
|---|---|
| Screen on **HDMI 0** (the port next to the USB-C power port). Log in as `qnxuser`, password `qnxuser`. Root is `root` / `root`. | DOCS [2] |
| From the laptop: `ssh qnxuser@qnxpi.local` (Windows: `ssh -m hmac-sha2-256 qnxuser@qnxpi.local`) | DOCS [2] |
| If the name does not resolve, read the address off the Pi's welcome screen, or run `ifconfig` on the Pi | DOCS [2] |
| Root over SSH is off by default. Use `su` or `sudo` after logging in as `qnxuser`. | DOCS [2], `sudo` used throughout [10] [11] |

## Step 2 — Copy the project over (5 min)

On the laptop, in the repository folder:

```
git archive --format=tar.gz -o rtr.tar.gz HEAD
scp rtr.tar.gz models/face_landmarker.task qnxuser@qnxpi.local:~
```

On the Pi:

```
mkdir -p ~/readtheroom && cd ~/readtheroom
tar -xzf ~/rtr.tar.gz
mv ~/face_landmarker.task models/
```

| Fact | Label |
|---|---|
| `scp <file> qnxuser@<target-ip>:~/bin` form | DOCS [1] |
| `tar -xzf` exists on the image | DOCS [10] (the codelab uses it) |
| `git archive` on the laptop | LAPTOP (standard git; avoids copying `.venv`) |
| `git clone` directly on the Pi | GUESS that `git` is installed; QNX's sample READMEs do tell you to `git clone` on a quick start image [12] |

If the model file was forgotten: `curl -L -o models/face_landmarker.task https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task` (curl with HTTPS works on the image: DOCS [1]; the codelab downloads its model from the same host: DOCS [10]).

## Step 3 — First report, then packages (10 min)

```
cd ~/readtheroom
sh qnx/first_hour.sh --sudo 2>&1 | tee first_hour.txt
```

This only reads. It prints the OS release, which packages are installed or available, Python and library versions, the USB devices, the sensor service's config, the I2C buses and the network address. Keep `first_hour.txt`; it answers most later questions. (Script: LAPTOP only, for "does not crash".)

What you want to see in section 1: `TAGID=QNXOS_805` or later. The 8.0.4 image is too old for the camera set-up described here (DOCS [11]).

Then install. Either let the script do it (`sh qnx/first_hour.sh --install`, it shows the commands and asks), or by hand:

| Command | What it does | Label |
|---|---|---|
| `sudo apk update` | refresh the package list | DOCS [10] |
| `sudo apk add mediapipe python3-pip curl` | MediaPipe with its OpenCV and NumPy | DOCS [10] |
| `sudo apk add qnx-sf-base-dev libjpeg-turbo-dev` | headers to build the capture program | DOCS [12] (mjpeg_example README; it adds "restart the system" after updating these if your `sensor` is an old one) |
| `sudo apk add onnxruntime` | object detection, optional | GUESS: package name from PLAN.md; see "Object detection" below |
| `apk info -e <name>` / `apk search <name>` | is it installed / does it exist | DOCS [18] (both appear in QNX codelabs) |

Check the imports (DOCS [10], minus `sdl2` which we do not use):

```
python3 -c 'import cv2, mediapipe, numpy; print("OpenCV", cv2.__version__, "MediaPipe", mediapipe.__version__, "NumPy", numpy.__version__)'
```

The codelab's example output is `OpenCV 4.12.0 MediaPipe 0.10.26 NumPy 2.4.1`.

## Step 4 — The app without a camera (3 min)

```
python3 -m readtheroom --simulate
```

Open `http://<pi-address>:8000/nurse` and `http://<pi-address>:8000/` on the laptop. Press "water bottle", then "YES". This needs only Python and NumPy, so it separates "the app runs on QNX" from every camera question. (LAPTOP; on QNX it is a GUESS that nothing in the standard-library web server behaves differently.)

## Step 5 — The face model on a still photo (3 min)

```
curl -L -o /tmp/face.png https://storage.googleapis.com/mediapipe-assets/business-person.png
python3 -m readtheroom.check_camera --image /tmp/face.png
```

Expected, from the laptop with three MediaPipe versions (see "MediaPipe versions" below):

```
Image 958x1358: face found: True, gaze readable: True
Measurements: [0.055, 0.074, 0.288, 0.025, 0.046, 0.096, 0.26, 0.025, 0.423, 0.057, 0.547, 0.06, 0.115, 0.307, 0.096, 0.538, 0.254]
```

Small differences in the last digit are fine. If this step works, the "AI module" part of the QNX prize is in place: it is QNX's own `mediapipe` package doing the work.

Whether Face Landmarker (as opposed to the face *detector* the codelab uses) runs in QNX's MediaPipe build is **not confirmed by any QNX source** we found. If it fails, see the table at the end and ask a mentor straight away.

## Step 6 — The camera

### 6a. Does QNX see the camera at all? (5 min)

Plug the C270 into a **blue** USB port ("use one of the blue USB 3.0 ports to avoid receiving an error message", DOCS [1]).

| Command | Want to see | Label |
|---|---|---|
| `sudo usb -vvv` | a device with `Vendor : 0x046d (Logitech)` | DOCS [7] [11]; needs root [7] |
| `sudo pidin ar \| grep sensor` | one line like `sensor -U 521:521 -b external -r /data/share/sensor -c /etc/config/sensor/sensor_rpi5.conf`. **Copy this line into a note**; you need it to restart the service. | DOCS [11] |
| `ls -al /dev/sensor` | `camera1` … `camera5` | DOCS [11] |
| `camera_example3_viewfinder -u 5` | live picture on the HDMI screen. Stop with a key, or `slay -f camera_example3_viewfinder` from SSH. | DOCS [11] |

Why unit 5: in the Pi 5 image's config, `SENSOR_UNIT_5` is "USB camera (default)", with "USB camera MJPEG format" as a commented-out alternative (DOCS [1]). The same page's hardware table lists the C920 under ID 3, which contradicts its own config list; the codelab uses 5 for the USB camera (DOCS [11]). Try 5 first, then 3.

The C270 is **not** on QNX's list of supported cameras; the C920 is (DOCS [1]). The C270 is a standard UVC camera, and the sensor service's `usb_camera` type accepts UVC cameras (DOCS [17]), so it is a GUESS that it works.

Only one program can use a camera unit at a time (DOCS [10]: "stop any other application that has the selected camera unit open"). Stop the viewfinder before the next step.

### 6b. First choice: `qnx/capture` (10 min)

```
sh qnx/first_hour.sh --build        # or: clang qnx/capture.c -o qnx/capture -lcamapi -lturbojpeg
qnx/capture -l -u 5                 # formats, sizes, rates the unit offers
qnx/capture -u 5 -n 20 -v -o /tmp/frame.jpg
python3 -c 'import cv2; f = cv2.imread("/tmp/frame.jpg"); print(f.shape, f.mean())'
```

| Fact | Label |
|---|---|
| Build line `clang <file>.c -o <name> -lcamapi -lturbojpeg` on the Pi itself | DOCS [12] (same form as `clang mjpeg_example.c screen_handler.c -o mjpeg_example -lcamapi -lscreen -lturbojpeg`) |
| `qnx/capture` itself | **Never compiled on QNX.** LAPTOP: compiles without warnings against stand-in headers and, fed by a fake camera, turns YUYV, UYVY, NV12 and MJPEG frames into correct JPEGs. That tests our code, not QNX's library. |
| Output path `/tmp/frame.jpg` | GUESS that `/tmp` is a normal writable folder (the docs show `ls /tmp` working [3]). Do not use `/dev/shmem`: "this isn't a full filesystem" [3]. If `/tmp` fails, use `~/frame.jpg`. |

`capture` asks for MJPEG first and, if the unit refuses, carries on with whatever the unit delivers and compresses it itself. So with the default config you will most likely see `JPEG not available ... using the unit's own format` followed by `ycbycr (YUYV)`. That is fine if the rate is good enough: the app needs more than 5 frames a second and is happy at 10–15.

If the build fails: open `qnx/capture.c` and search for `UNSURE`. The most likely fix is deleting the `#include <sys/neutrino.h>` line or an enum name that differs in this SDK. `mjpeg_example.c` (clone it next to ours) is the reference that QNX says builds on the image.

If YUYV at 1280x720 is too slow (a USB 2 webcam usually cannot send uncompressed 720p quickly): try `qnx/capture -u 5 -w 640 -h 480`, or go to 6c.

### 6c. Second choice: switch the USB unit to MJPEG (10 min)

1. `sudo vim /etc/config/sensor/sensor_rpi5.conf`. In `SENSOR_UNIT_5`, comment out the active USB definition and uncomment the "USB camera MJPEG format" one. (DOCS [1]: "Comment out the active definition, uncomment the alternative definition, and save the changes." Make a copy of the file first.)
2. `mjpeg_example`'s own error message says MJPEG needs `create_window = false` and `memory_allocator_type = from_sensor` in the unit's definition (DOCS [12]); check that the alternative has them.
3. Restart the service **with the exact line you noted in 6a**:
   ```
   sudo slay sensor
   sudo sensor -U 521:521 -r /data/share/sensor -b external -c /etc/config/sensor/sensor_rpi5.conf
   ```
   (DOCS [1], the brightness section uses these two lines. Or reboot.)
4. `qnx/capture -l -u 5` should now list `jpeg`; `qnx/capture -u 5 -t jpeg -n 20 -v`.

There is also a stand-alone config, used by QNX's sample: `sensor -U 521:521 -r /data/share/sensor -c /system/etc/config/sensor/usb/usb_camera_jpeg.conf` (DOCS [12]). The docs give the folder as `/etc/config/sensor/usb` (DOCS [1]); use whichever exists. With a stand-alone config the camera is probably **unit 1**, because the sample then runs `mjpeg_example` with its default unit 1 (GUESS from [12]).

Known weakness, in QNX's own words in `mjpeg_example.c`: "Currently some received JPEG frames are invalid" (DOCS [12]). `capture` drops frames that do not start and end like a JPEG and counts them as "unusable"; a few are normal.

To see MJPEG working independently of our code, build and run QNX's sample: `mjpeg_example -l`, then `mjpeg_example -w 1280 -h 720 -f 30` (DOCS [12]).

### 6d. Third choice: a camera QNX lists as supported

Ask the QNX mentors for one of these.

| Camera | Unit | Notes | Label |
|---|---|---|---|
| Logitech C920 | 5 (try 3 if not) | same commands as above | DOCS [1] [11] |
| Pi Camera Module 3 on **DISP0**, Pi powered off while connecting | 3 | delivers NV12; `qnx/capture -u 3 -t keep -w 0 -h 0`. Frames are 2304x1296 [10], so add `-r 10` and expect larger files. | DOCS [10] for unit, port and format; `capture`'s NV12 path LAPTOP only |

QNX's Python helper (`qnx_camera_helper.py` in the codelab) would also work for the Camera Module 3 as written. We did not adapt it for the webcam: it "requires NV12 and rejects other frame formats" (DOCS [10]) because it hard-codes the NV12 frame layout and the number QNX uses for NV12; the numbers for YUYV and JPEG in the 8.0 header are not published anywhere we could read, and guessing them from Python would fail silently. A C program gets them from the header at build time.

### 6e. Last resort: the laptop's camera

On the Pi: `python3 qnx/frame_relay.py receive --out /tmp/frame.jpg`
On the laptop: `python qnx/frame_relay.py send --url http://<pi-address>:8090 --camera 0`

(LAPTOP: receiver tested by posting JPEGs to it. On QNX: GUESS.) Everything except the lens still runs on QNX. Say so to the judges without being asked.

## Step 7 — Run it (5 min)

Two terminals (or SSH sessions).

Terminal 1, the camera, restarted if it stops (it exits by itself after 10 s without frames):

```
while true; do qnx/capture -u 5 -o /tmp/frame.jpg -v; sleep 1; done
```

Terminal 2, the app under its supervisor:

```
python3 -m readtheroom.supervisor -- --frame-file /tmp/frame.jpg
```

From the laptop open `http://<pi-address>:8000/nurse` and `http://<pi-address>:8000/`. Press "Show camera view" to aim the camera, then "Start calibration". The nurse page shows frames per second; under 5 it warns.

(The app and supervisor: LAPTOP. On QNX: GUESS, including that `subprocess` and `terminate()` behave as on a Mac.)

If the camera process dies, the app notices within a second (no new frame) and shows "cannot see patient" instead of acting on an old picture.

## Step 8 — Priorities, the QNX way (10 min)

Facts first:

| Fact | Label |
|---|---|
| Priorities run 0–255, higher wins. Without root you may use 1–63. | DOCS [4] [6] |
| Ordinary processes on the image run at 10 (`10r` in the listing = priority 10, round-robin); the SD-card driver is at 21. | DOCS [3] |
| `on -p <prio>[f\|r] command` starts a command at that priority; `f` = FIFO, `r` = round-robin. It sets the main thread only. | DOCS [4] |
| Threads a program creates inherit the creator's priority unless the program says otherwise. | DOCS [9] |
| A child process (our supervisor starting the app) inherits its parent's priority. | GUESS; check with `pidin` below |
| `slay -P <prio>[f\|r] name` changes a running process, all its threads. `-f` = do not ask when several match. | DOCS [6] |
| `nice -n-2 command` runs it two levels **above** the current priority (negative = higher). | DOCS [5] |
| `pidin -p <name>` lists the threads with a `prio` column. | DOCS [3] [8] |

Commands. The camera feeds everything, so it goes highest; the app sits above the desktop, shells and anything else at 10, and below the drivers.

```
on -p 16r sh -c 'while true; do qnx/capture -u 5 -o /tmp/frame.jpg; sleep 1; done'
on -p 15r python3 -m readtheroom.supervisor -- --frame-file /tmp/frame.jpg
```

Check, and fix if the app did not inherit:

```
pidin -p python3            # every thread should show 15r
pidin -p capture            # 16r
slay -f -P 15r python3      # sets all python3 processes and threads to 15
```

(`on -p ... sh -c '...'`: the options are DOCS [4]; wrapping a shell loop this way is a GUESS.)

What this does and does not give you. If something else on the Pi goes busy (a compile, a second demo), QNX will always run our two processes first; show it by running `on -p 10 sh -c 'while :; do :; done' &` four times (one per core) and pointing at the unchanged frame rate on the nurse page, then `slay -f sh`. It does **not** put alert delivery above face tracking: both are threads of one Python process at one priority. Do not claim otherwise. Splitting them into two processes is the honest next step if there is time.

## Step 9 — The temperature sensor (10 min)

Wiring (DOCS [14]): SHT3x +3.3V → pin 1, GND → pin 6, SDA → pin 3 (GPIO 2), SCL → pin 5 (GPIO 3). Bus 1, address 0x44 (DOCS [14]).

I2C needs root: "any app using this API needs to be executed as root to access the I2C device driver" (DOCS [14]). So run the app as root:

```
sudo on -p 15r python3 -m readtheroom.supervisor -- --frame-file /tmp/frame.jpg
```

Quick test on its own:

```
sudo python3 -c 'from readtheroom.sensor import Sht3x; print(Sht3x().read())'
```

Expected: something like `(23.4, 41.0)`. `None` means the sensor answered badly or not at all.

| Fact | Label |
|---|---|
| `import smbus` and `smbus.SMBus(1)` on the image | DOCS [14] (QNX's Python samples do exactly this) |
| `write_i2c_block_data(addr, cmd, [..])` and `read_i2c_block_data(addr, cmd, n)` exist with those arguments and raise `OSError` on failure | DOCS [15] (read in the module's C source) |
| Our exact byte sequence for the SHT3x through that module | GUESS. QNX's C sample [14] sends `0x2C 0x06`, waits 15 ms and then reads 6 bytes with a plain read; ours sends `0x24 0x00`, waits 20 ms and reads through a "send one byte, then read" call. If ours returns `None`, this is the first suspect. |
| `sudo` keeps the working folder so `-m readtheroom` is found | GUESS; if not, `cd` inside: `sudo sh -c 'cd /data/home/qnxuser/readtheroom && python3 -m ...'` |

Running as root creates `data/` files owned by root. If you later run as `qnxuser`, fix with `sudo chown -R qnxuser data` (GUESS).

Without the sensor the app runs normally and says "No temperature sensor found; room-climate prompts are off."

## Step 10 — Object detection (optional)

The app turns object detection on only if `models/scene.onnx` exists **and** `import onnxruntime` works. Otherwise it prints "Object detection is off" and everything else runs.

What we could establish:

- The QNX port of ONNX Runtime (version 1.23.2) is built with `ENABLE_PYTHON ?= OFF` by default, and its instructions only cover the C++ library and tests (DOCS [16]). So the `onnxruntime` apk package **may well have no Python module**.
- Whether the apk package was built differently is unknown. The check takes ten seconds: `python3 -c 'import onnxruntime; print(onnxruntime.__version__)'`, and `apk search onnxruntime` shows whether a separate Python package exists.

If there is no Python module, in order:

1. Ask a QNX mentor whether a Python package exists under another name.
2. Run the detector on the laptop that already holds the scene camera. Not built yet: `scene_sender` would need to run `SceneDetector` itself and post the results instead of the photo.
3. Demo without object detection. The gaze, question, answer and alert path does not depend on it.

Trying `cv2.dnn.readNetFromONNX` on the Pi is a long shot (GUESS; YOLO-World exports often use operators OpenCV cannot load).

## What to tell the judges about QNX

Say only what you have seen working on the day.

- **The camera does not belong to our program.** On QNX it belongs to the sensor service, a separate process; our capture program asks it for frames and is told about each one by a kernel message (a pulse). If our capture program crashes, the camera driver and the rest of the system are untouched, and a loop restarts it. That is the microkernel design doing its job. (True by construction: `qnx/capture.c`.)
- **Three processes, each replaceable.** Camera capture, the app, and a supervisor that restarts the app if its loop stalls for 5 seconds. Demonstrate two failures. First `slay -f capture`: the patient screen says it cannot see the patient within a second, then recovers when the loop restarts the capture program. Second, stop only the app: the supervisor prints `app started (pid N ...)`, so run `slay N` with that number (`slay` accepts a process ID, DOCS [6]); the supervisor restarts the app and open requests are still on the nurse page. Do not `slay python3` by name for this, because that also stops the supervisor.
- **Priorities that hold under load.** Step 8's demonstration.
- **A stalled camera is never trusted.** A frame older than one second counts as "cannot see patient".
- **The AI runs on the device.** QNX's own MediaPipe package; no video leaves the Pi. Pull the network cable: tracking and the patient screen carry on.

Do not say "hard real-time", "guaranteed latency" or "alerts have priority over perception". We have not measured the first two and the third is not true yet.

## Symptom → likely cause → what to try

Commands in this table are UNVERIFIED GUESSES unless they appear with a DOCS label here or earlier in the guide. The causes are our best reading of the sources, not experience.

| Symptom | Likely cause | What to try |
|---|---|---|
| `ssh: Could not resolve hostname qnxpi.local` | laptop has no mDNS, or different network | use the address from the Pi's welcome screen or `ifconfig`; same Wi-Fi/switch |
| `apk add mediapipe`: no such package | old image (8.0.4) or stale index | `sudo apk update`; check `TAGID` in `first_hour.txt`; ask a mentor for the September 2026 image |
| `import mediapipe` fails after install | Python version and package mismatch | `python3 --version`, `apk info -L mediapipe \| head`; ask a mentor |
| Step 5: error creating the landmarker, or it mentions GPU / delegate / graph | Face Landmarker not usable in this build | re-download the model (a cut-off download gives the same error); ask a mentor "does Face Landmarker run in your MediaPipe build?"; PLAN.md's backup is face detection plus a small gaze model on ONNX Runtime |
| Step 5: `face found: True, gaze readable: False` on the test photo | blendshapes or the head matrix missing in this build | report the exact output to the team; `features.py` already returns "no gaze" rather than crashing |
| `usb -vvv` shows no Logitech | black (USB 2) port, loose cable, not root | blue port; `sudo`; another cable or port; check the camera on a laptop |
| Logitech in `usb`, but `/dev/sensor/camera5` missing | sensor service not running or a different config | `sudo pidin ar \| grep sensor`; `sudo slog2info -b sensor_service \| tail -40` (DOCS [11]); restart with the noted line |
| `camera_example3_viewfinder -u 5` shows moving colour bars | you opened the simulator unit | wrong unit number: list `/dev/sensor`, try 3 and 5 (units 1 and 2 are simulation and playback, DOCS [1]) |
| `Failed to open CAMERA_UNIT_5` from `capture` | unit busy, wrong number, or no permission | `slay -f camera_example3_viewfinder`; `ls -al /dev/sensor` (group `sensor`): run `id`, and try once with `sudo` |
| `capture` builds but `camera/camera_api.h` not found | dev package missing | `sudo apk add qnx-sf-base-dev libjpeg-turbo-dev`, reboot if `sensor` was updated (DOCS [12]) |
| `capture` compile error in our code | an `UNSURE` spot | search `UNSURE` in `qnx/capture.c`; compare with `mjpeg_example.c` |
| `The camera unit refused JPEG` | default USB config is not the MJPEG one | expected; either let it use YUYV (`-t auto`) or do step 6c |
| `No frame for 2 s` repeating | stream did not start; USB bandwidth; another program has the unit | lower size `-w 640 -h 480`; blue port; restart the sensor service; try QNX's `camera_example2_events -u 5` to see whether any frames arrive at all |
| many "unusable" frames with MJPEG | QNX's known invalid-JPEG issue [12] | accept if the written rate stays above 10/s; otherwise go back to YUYV at 640x480 |
| nurse page shows 1 frame per second although `capture -v` reports 15 | file times kept in whole seconds | already handled in `camera.py` (compares the bytes); if still stuck, move `-o` to another folder |
| app frame rate under 5 | face model too slow at this size, or Pi throttling | `cat /dev/thermal` (DOCS [1]); `capture -w 640 -h 480`; add a heat sink or fan |
| picture upside down or mirrored | camera mounting | calibration absorbs it; only the nurse preview looks odd |
| `Sht3x().read()` gives `None` or raises | not root; wiring; the command sequence | `sudo`; `ls /dev/i2c1`; swap SDA/SCL; build QNX's `temp-humidity-sht3x` C sample [14] to prove the sensor itself |
| screens do not load from the laptop | different network, or wrong address | `ifconfig` on the Pi; `curl http://localhost:8000/health` on the Pi itself |
| app restarts every 30 s | supervisor's start-up allowance too short for the Pi | `python3 -m readtheroom.supervisor --startup 90 -- --frame-file /tmp/frame.jpg` |

## MediaPipe versions (tested on the Mac, Python 3.12)

MediaPipe 0.10.26, the version in QNX's example output [10], has no macOS build on PyPI (the list jumps from 0.10.21 to 0.10.30), so it could not be installed here. The versions either side were tested instead, with the same model file and the same photo:

| MediaPipe | NumPy | OpenCV | `check_camera --image` | Test suite |
|---|---|---|---|---|
| 0.10.21 | 1.26.4 | 4.11.0 | face found, gaze readable, same 17 numbers | 50 passed, 1 skipped |
| 0.10.30 | 2.4.1 | 5.1.0 (headless) | same | 50 passed, 1 skipped |
| 1.2.3 (the laptop's) | 2.5.5 | 5.1.0 | same | — |

In all three: the Tasks imports, every `FaceLandmarkerOptions` field we set, `detect_for_video(image, timestamp_ms)`, `mp.Image` / `mp.ImageFormat.SRGB`, 478 landmarks (iris included), the same 52 blendshape names (including all eight `eyeLook*` and both `eyeBlink*`), and a 4x4 head matrix. **The only difference found:** the matrix is `float64` in 0.10.21 and `float32` from 0.10.30 on; `features.py` already converts it, so no change was needed.

What this does not prove: that QNX's own build of 0.10.26 includes Face Landmarker, or how fast it is on the Pi. Step 5 answers the first in three minutes. (The skipped test needs `onnxruntime`, which the test environments did not have.)

## Sources

1. QNX quick start image guide, "Next steps" (camera units, config files, restart command, blue ports, curl, audio, network): https://www.qnx.com/developers/docs/qnxeverywhere/com.qnx.doc.target_images/topic/qsti/next-steps.html
2. Same guide, "Interacting with the system" (logins, SSH, mDNS): https://www.qnx.com/developers/docs/qnxeverywhere/com.qnx.doc.target_images/topic/qsti/interacting-with-the-system.html
3. Same guide, "QNX OS on the Raspberry Pi" (`pidin`, filesystems, `/dev/shmem`): https://www.qnx.com/developers/docs/qnxeverywhere/com.qnx.doc.target_images/topic/qsti/qnxos_on_rpi.html
4. `on`: https://www.qnx.com/developers/docs/8.0/com.qnx.doc.neutrino.utilities/topic/o/on.html
5. `nice`: https://www.qnx.com/developers/docs/8.0/com.qnx.doc.neutrino.utilities/topic/n/nice.html
6. `slay`: https://www.qnx.com/developers/docs/8.0/com.qnx.doc.neutrino.utilities/topic/s/slay.html
7. `usb`: https://www.qnx.com/developers/docs/8.0/com.qnx.doc.neutrino.utilities/topic/u/usb.html
8. `pidin`: https://www.qnx.com/developers/docs/8.0/com.qnx.doc.neutrino.utilities/topic/p/pidin.html
9. `pthread_attr_setinheritsched()`: https://www.qnx.com/developers/docs/8.0/com.qnx.doc.neutrino.lib_ref/topic/p/pthread_attr_setinheritsched.html
10. Codelab "Run MediaPipe face detection from a QNX camera with Python" and its `qnx_camera_helper.py`: https://qnx.github.io/codelabs/mediapipe-camera-python/ (source: https://github.com/qnx/codelabs/tree/main/markdown/mediapipe-camera-python)
11. Codelab "Configure multiple camera sources on Raspberry Pi 5": https://qnx.github.io/codelabs/configure-multiple-camera-sources-on-RPI5/
12. `mjpeg_example` (README and `mjpeg_example.c`): https://gitlab.com/qnx/projects/camera-projects/applications/mjpeg_example
13. `camera_example2_events`, `camera_example1_callback`, `camera_example3_viewfinder`, `camera-dump-frame-no-screen`: https://gitlab.com/qnx/projects/camera-projects/applications
14. `hardware-component-samples` (I2C needs root; `temp-humidity-sht3x`; Python `smbus` samples): https://gitlab.com/qnx/projects/hardware-component-samples
15. `rpi-gpio`, `python/smbus/smbus-qnx.c`: https://gitlab.com/qnx/projects/rpi-gpio
16. ONNX Runtime port for QNX (`README.md`, `common.mk`): https://github.com/qnx-ports/build-files/tree/main/ports/onnxruntime
17. Sensor configuration file reference (QNX 7.1; QNX notes it may be out of date for 8.0): https://www.qnx.com/developers/docs/7.1/com.qnx.doc.adas.system_services/topic/sensor_service_configuration_file.html
18. Other QNX codelabs that use `apk search` and `apk info -e`: https://github.com/qnx/codelabs/tree/main/markdown

The QNX 8.0 Camera library reference pages are not on the public documentation site (the 8.0 addresses return "not found"); the 7.1 pages exist but list a shorter set of frame types than the 8.0 samples use. So for the camera API the sample source code [12] [13] and the codelab helper [10] are the authority, not the 7.1 reference.

## Questions for the QNX mentors

1. Is the Logitech C270 known to work with the sensor service? If not, can we borrow a C920 or a Camera Module 3?
2. Does Face Landmarker (with blendshapes) run in your MediaPipe package?
3. Does the `onnxruntime` package include a Python module?
4. Which of your modules count as the "AI module" for the prize?
5. Is there a preferred way to give threads inside one Python process different priorities?
