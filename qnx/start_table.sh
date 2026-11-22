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
for pid in $(pidin -F "%a %A" 2>/dev/null | grep "scene_sender" | grep -v grep | awk '{print $1}'); do
  kill $pid 2>/dev/null
done
# The app and its supervisor (SIGTERM; they are not camera clients).
for pid in $(pidin -F "%a %A" 2>/dev/null | grep -E "readtheroom\.supervisor|-m readtheroom " | grep -v grep | awk '{print $1}'); do
  kill $pid 2>/dev/null
done
sleep 3

# Gaze frames: start the receiver only if it is not already running.
if ! pidin -F "%A" 2>/dev/null | grep -q "frame_relay.py receive"; then
  nohup python3 qnx/frame_relay.py receive --out /tmp/gaze.jpg > logs/relay.log 2>&1 < /dev/null &
fi
# The camera: start the capture loop only if nothing holds unit 5.
if ! pidin -F "%A" 2>/dev/null | grep -q "qnx/capture -u 5"; then
  nohup sh -c 'while true; do qnx/capture -u 5 -w 640 -h 480 -r 2 -o /tmp/scene.jpg; sleep 1; done' \
    > logs/capture.log 2>&1 < /dev/null &
fi

nohup python3 -m readtheroom.supervisor --port 8080 --startup 90 -- \
  --frame-file /tmp/gaze.jpg --table-file /tmp/scene.jpg \
  > logs/app.log 2>&1 < /dev/null &
echo "started table mode; log: logs/app.log"
