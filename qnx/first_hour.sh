#!/bin/sh
# first_hour.sh - what does this QNX Raspberry Pi have, and what is missing?
#
#   sh qnx/first_hour.sh              read-only report (safe, run it as often as you like)
#   sh qnx/first_hour.sh --sudo       same, but uses sudo for the checks that need root
#   sh qnx/first_hour.sh --install    ONLY installs packages (asks first), then stop
#   sh qnx/first_hour.sh --build      ONLY builds qnx/capture (asks first), then stop
#
# Save the report:   sh qnx/first_hour.sh --sudo 2>&1 | tee first_hour.txt
#
# NOT YET RUN ON QNX. It was only run on a laptop to check that it does not
# crash when commands are missing. Every check prints the command it ran, so
# if one fails you can see what to try by hand. docs/qnx-bringup.md says where
# each command comes from and which ones are guesses.

MODE=report
SUDO=""
for arg in "$@"; do
    case "$arg" in
        --sudo)    SUDO="sudo" ;;
        --install) MODE=install ;;
        --build)   MODE=build ;;
        *) echo "usage: sh qnx/first_hour.sh [--sudo | --install | --build]"; exit 2 ;;
    esac
done

HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(dirname "$HERE")

have() { command -v "$1" >/dev/null 2>&1; }
section() { echo; echo "=== $1"; }
run() {         # print a command, run it, never stop the script
    echo "\$ $*"
    "$@" 2>&1 || echo "  (failed or not available: exit $?)"
}
confirm() {
    printf "%s [y/N] " "$1"
    read -r answer
    [ "$answer" = "y" ] || [ "$answer" = "Y" ]
}

# ----------------------------------------------------------------------------
# Install step. Only runs when asked, and asks again before changing anything.
# ----------------------------------------------------------------------------
if [ "$MODE" = install ]; then
    echo "This will run, with sudo:"
    echo "  apk update"
    echo "  apk add mediapipe python3-pip curl            (face model, OpenCV, NumPy)"
    echo "  apk add qnx-sf-base-dev libjpeg-turbo-dev     (to build qnx/capture)"
    echo "  apk add onnxruntime                           (object detection; optional)"
    confirm "Go ahead?" || { echo "Nothing installed."; exit 0; }
    sudo apk update || exit 1
    sudo apk add mediapipe python3-pip curl || echo "!! mediapipe install failed"
    sudo apk add qnx-sf-base-dev libjpeg-turbo-dev || echo "!! build packages failed"
    sudo apk add onnxruntime || echo "!! onnxruntime not installed (object detection stays off)"
    echo "Done. Now run:  sh qnx/first_hour.sh --sudo"
    exit 0
fi

if [ "$MODE" = build ]; then
    CC=""
    have clang && CC=clang
    [ -z "$CC" ] && have gcc && CC=gcc
    [ -z "$CC" ] && { echo "No clang or gcc found. Run: sh qnx/first_hour.sh --install"; exit 1; }
    echo "This will run:  $CC $HERE/capture.c -o $HERE/capture -lcamapi -lturbojpeg"
    confirm "Go ahead?" || { echo "Nothing built."; exit 0; }
    if $CC "$HERE/capture.c" -o "$HERE/capture" -lcamapi -lturbojpeg; then
        echo "Built $HERE/capture. Next:  $HERE/capture -l -u 5"
    else
        echo "!! Build failed. Open qnx/capture.c and search for UNSURE."
        exit 1
    fi
    exit 0
fi

# ----------------------------------------------------------------------------
# Report. Everything below only reads.
# ----------------------------------------------------------------------------
echo "Read-only report. Nothing on this machine is changed."
[ -z "$SUDO" ] && [ "$(id -u 2>/dev/null)" != "0" ] && \
    echo "Tip: some checks need root. Re-run with --sudo to include them."

section "1. Operating system"
run uname -a
[ -r /etc/os-release ] && run cat /etc/os-release
if [ -e /proc/boot/procnto-smp-instr ] && have use; then
    echo "\$ $SUDO use -i /proc/boot/procnto-smp-instr | grep TAGID   (want QNXOS_805 or later)"
    $SUDO use -i /proc/boot/procnto-smp-instr 2>&1 | grep -i tagid || echo "  (no TAGID shown; needs root)"
fi
have pidin && run pidin info
echo "\$ id"; id 2>&1
echo "  (the camera devices belong to group 'sensor'; I2C needs root)"

section "2. Packages (apk)"
if have apk; then
    echo "Package index age matters: run 'sudo apk update' first if 'available' looks wrong."
    for pkg in mediapipe python3-pip onnxruntime opencv tflite-runtime python3-tflite-runtime \
               python3-numpy sdl2-compat qnx-sf-base-dev libjpeg-turbo-dev libturbojpeg \
               qnx-sensor-framework qnx-sensor-framework-example-configs; do
        if apk info -e "$pkg" >/dev/null 2>&1; then
            state="INSTALLED"
        elif apk search -x "$pkg" 2>/dev/null | grep -q .; then
            state="available, not installed"
        else
            state="NOT FOUND in the package index"
        fi
        printf "  %-40s %s\n" "$pkg" "$state"
    done
    echo "Anything else with these names (look for a Python binding of onnxruntime):"
    run apk search onnxruntime
    run apk search mediapipe
else
    echo "  apk not found: this is not the QNX quick start image (or PATH is wrong)."
fi

section "3. Python"
if have python3; then
    run python3 --version
    for mod in numpy cv2 mediapipe onnxruntime smbus sdl2; do
        python3 -c "
import importlib, sys
try:
    m = importlib.import_module('$mod')
    print('  %-12s %s' % ('$mod', getattr(m, '__version__', 'imports (no version shown)')))
except Exception as exc:
    print('  %-12s MISSING: %s' % ('$mod', exc))
" 2>&1
    done
    echo "  (smbus normally imports only as root on QNX; sdl2 is optional)"
    MODEL="$ROOT/models/face_landmarker.task"
    if [ -f "$MODEL" ]; then
        echo "  face model present: $MODEL"
    else
        echo "  face model MISSING: $MODEL  (see docs/qnx-bringup.md step 3)"
    fi
    if [ -f "$ROOT/models/scene.onnx" ]; then
        echo "  object detector present: models/scene.onnx"
    else
        echo "  object detector not present (optional): models/scene.onnx"
    fi
else
    echo "  python3 not found."
fi

section "4. Build tools for qnx/capture"
for tool in clang gcc make git; do
    if have "$tool"; then echo "  $tool: $(command -v "$tool")"; else echo "  $tool: not found"; fi
done
for header in camera/camera_api.h turbojpeg.h; do
    found=""
    for dir in /usr/include /usr/local/include /system/usr/include /data/usr/include; do
        [ -f "$dir/$header" ] && found="$dir/$header"
    done
    echo "  $header: ${found:-not found in the usual include folders}"
done
if [ -x "$HERE/capture" ]; then echo "  qnx/capture is built"; else echo "  qnx/capture is not built yet (sh qnx/first_hour.sh --build)"; fi

section "5. USB camera"
if have usb; then
    echo "\$ $SUDO usb    (QNX docs: needs root; look for Vendor 0x046d Logitech)"
    $SUDO usb 2>&1 | grep -i -E "vendor|product|class  |speed|usb [0-9]" || echo "  (nothing shown: no devices, or needs root; try: sudo usb -vvv)"
    echo "  Full detail:  sudo usb -vvv"
else
    echo "  'usb' command not found."
fi

section "6. Sensor service and its config"
CONF=""
if have pidin; then
    echo "\$ $SUDO pidin ar | grep sensor"
    LINE=$($SUDO pidin ar 2>/dev/null | grep '[s]ensor ' | grep -e '-c' | head -1)
    if [ -n "$LINE" ]; then
        echo "  $LINE"
        CONF=$(echo "$LINE" | sed -n 's/.* -c *\([^ ]*\).*/\1/p')
    else
        echo "  sensor service not seen (not running, or needs root: use --sudo)"
    fi
fi
[ -z "$CONF" ] && [ -f /etc/config/sensor/sensor_rpi5.conf ] && CONF=/etc/config/sensor/sensor_rpi5.conf
if [ -n "$CONF" ] && [ -r "$CONF" ]; then
    echo "Config in use (or the default): $CONF"
    echo "Lines that decide the camera units ('#' = switched off):"
    grep -n -E "SENSOR_UNIT|type *=|address *=|default_video_format|default_video_resolution|create_window|memory_allocator" "$CONF" 2>&1 | sed 's/^/  /'
else
    echo "  no readable sensor config found (${CONF:-none})"
fi
run ls -l /dev/sensor
[ -d /etc/config/sensor/usb ] && run ls /etc/config/sensor/usb
[ -d /system/etc/config/sensor/usb ] && run ls /system/etc/config/sensor/usb
echo "  Expected on the Pi 5 image: camera5 = USB camera."

section "7. I2C (temperature sensor)"
echo "\$ ls /dev/i2c*"
ls -l /dev/i2c* 2>&1 | sed 's/^/  /'
if have pidin; then
    echo "\$ $SUDO pidin ar | grep i2c"
    $SUDO pidin ar 2>/dev/null | grep '[i]2c' | sed 's/^/  /'
fi
echo "  The SHT3x should be on /dev/i2c1 at address 0x44. Reading needs root."

section "8. Network"
run hostname
if have ifconfig; then
    echo "\$ ifconfig | grep inet"
    ifconfig 2>/dev/null | grep "inet " | sed 's/^/  /'
fi
echo "  Screens from a laptop: http://<address above>:8000/  and  /nurse"
echo "  The image also answers to the name qnxpi.local on most networks."

section "9. Temperature of the Pi itself"
[ -r /dev/thermal ] && run cat /dev/thermal

echo
echo "Report finished. Next: docs/qnx-bringup.md"
