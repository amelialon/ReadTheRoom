#!/bin/sh
# Pi side, table mode: the C270 over the table takes the one table photo,
# gaze frames arrive from the laptop (frame_relay), all reading runs here.
#
# Leaves the camera alone: qnx/capture keeps holding unit 5 and writing
# /tmp/scene.jpg, and that file is the table photo. Stops only the app, its
# supervisor and the old scene-sender loop, politely (no SIGKILL near the
# camera), then starts the app again in table mode.
cd ~/readtheroom || exit 1
mkdir -p logs

# The old scene-sender loop is not needed: the app reads /tmp/scene.jpg itself.
# (pidin lists this script and any shell that launched it too; skip those lines.)
procs() { pidin -F "%a %A" 2>/dev/null | grep -v -E "grep|start_table|bash -c|sh -c"; }
for pid in $(procs | grep "scene_sender" | awk '{print $1}'); do
  kill $pid 2>/dev/null
done
# The app and its supervisor (SIGTERM; they are not camera clients).
for pid in $(procs | grep -E "readtheroom\.supervisor" | awk '{print $1}'); do
  kill $pid 2>/dev/null
done
sleep 2
for pid in $(procs | grep -E "\-m readtheroom " | awk '{print $1}'); do
  kill $pid 2>/dev/null
done
sleep 3

# Gaze frames: start the receiver only if it is not already running.
if ! procs | grep -q "frame_relay.py receive"; then
  nohup python3 qnx/frame_relay.py receive --out /tmp/gaze.jpg > logs/relay.log 2>&1 < /dev/null &
fi
# The camera: start the capture loop only if nothing holds unit 5.
if ! procs | grep -q "qnx/capture -u 5"; then
  nohup sh -c 'while true; do qnx/capture -u 5 -w 640 -h 480 -r 2 -o /tmp/scene.jpg; sleep 1; done' \
    > logs/capture.log 2>&1 < /dev/null &
fi

# The temperature log, if it is not already running.
if ! procs | grep -q "heartbeat.sh"; then
  nohup sh heartbeat.sh > /dev/null 2>&1 < /dev/null &
fi

# Reporting to the central API, when central.env is here (not in git):
#   RTR_CENTRAL_URL, RTR_DEVICE_ID, RTR_PATIENT_ID, RTR_DEVICE_TOKEN
CENTRAL=""
if [ -f central.env ]; then
  . ./central.env
  export RTR_DEVICE_TOKEN
  CENTRAL="--central-url $RTR_CENTRAL_URL --device-id $RTR_DEVICE_ID --patient-id $RTR_PATIENT_ID"
fi

# 12 reads a second: the gaze frames arrive at about that rate anyway, and
# the board stays cooler than at 30. Reboots seen here at 85 degrees.
nohup python3 -m readtheroom.supervisor --port 8080 --startup 90 -- \
  --frame-file /tmp/gaze.jpg --table-file /tmp/scene.jpg --loop-hz 12 $CENTRAL \
  > logs/app.log 2>&1 < /dev/null &
echo "started table mode; log: logs/app.log"
