#!/bin/sh
# Builds the hosted caregiver dashboard: the same nurse page the device
# serves, handed the central API's address so it reads from there instead
# of a device's stream. Vercel runs this from the repo root (see vercel.json).
set -e
cd "$(dirname "$0")/.."
API="${CENTRAL_API_URL:-https://read-the-room-tau.vercel.app}"
rm -rf dashboard/dist
mkdir -p dashboard/dist
cp readtheroom/static/theme.css dashboard/dist/theme.css
awk -v api="$API" '{ if (!done && $0 == "<script>") { print "<script>window.CENTRAL = {api: \"" api "\"};</script>"; done = 1 } print }' \
  readtheroom/static/nurse.html > dashboard/dist/index.html
echo "dashboard built for $API"
