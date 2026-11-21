"""Keeps the device running: restarts the app if it exits or stops responding.

    python -m readtheroom.supervisor -- --camera 0
    python -m readtheroom.supervisor --port 8000 -- --simulate

Everything after `--` is passed to the app. The app counts as hung when its
main loop has not published a new state for `--max-age` seconds.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.request


def health(port: int):
    """Seconds since the app last published, or None if it cannot be reached
    or has not published yet."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as r:
            return json.load(r).get("age_s")
    except (OSError, ValueError):
        return None


def log(text: str) -> None:
    print(f"[supervisor {time.strftime('%H:%M:%S')}] {text}", flush=True)


def stop(child: subprocess.Popen) -> None:
    child.terminate()
    try:
        child.wait(timeout=3)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait()


def main() -> None:
    p = argparse.ArgumentParser(prog="readtheroom.supervisor")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--max-age", type=float, default=5.0)
    p.add_argument("--startup", type=float, default=30.0,
                   help="seconds allowed for the app to start responding")
    p.add_argument("app_args", nargs="*")
    args = p.parse_args()

    command = [sys.executable, "-m", "readtheroom", "--port", str(args.port), *args.app_args]
    restarts = 0
    while True:
        child = subprocess.Popen(command)
        started = time.monotonic()
        log(f"app started (pid {child.pid}, restart {restarts})")
        responded = False
        reason = None
        try:
            while reason is None:
                time.sleep(1.0)
                if child.poll() is not None:
                    reason = f"app exited with code {child.returncode}"
                    break
                age = health(args.port)
                if age is not None and age <= args.max_age:
                    responded = True
                elif responded:
                    # One more look before acting, so a single slow answer
                    # does not cause a restart.
                    time.sleep(1.0)
                    age = health(args.port)
                    if age is None or age > args.max_age:
                        reason = "app stopped responding"
                elif time.monotonic() - started > args.startup:
                    reason = "app did not start responding"
        except KeyboardInterrupt:
            stop(child)
            return
        log(f"{reason}; restarting")
        if child.poll() is None:
            stop(child)
        restarts += 1
        # Do not spin if the app fails immediately every time.
        time.sleep(min(10.0, 1.0 if time.monotonic() - started > 10 else 3.0))


if __name__ == "__main__":
    main()
