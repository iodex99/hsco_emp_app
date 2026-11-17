"""Supervisor behaviour on a real process tree: crash restart and no orphaned web server."""
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _health(port):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=2) as r:
            return json.loads(r.read())
    except Exception:  # noqa: BLE001
        return None


def _wait(fn, timeout):
    end = time.time() + timeout
    while time.time() < end:
        if fn():
            return True
        time.sleep(0.25)
    return False


def test_killing_supervisor_takes_server_down(tmp_path):
    port = _port()
    env = {**{k: v for k, v in os.environ.items() if k not in ("HSCO_SUPERVISED",)},
           "HSCO_DATA_DIR": str(tmp_path / "data"), "HSCO_DISABLE_UPDATE_THREAD": "1"}
    p = subprocess.Popen([sys.executable, "run_server.py", "--dev", "--port", str(port)], cwd=str(ROOT), env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        assert _wait(lambda: _health(port), 60), "server did not start"
        # kill ONLY the supervisor (not the tree) — the child must notice and exit
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/PID", str(p.pid)], capture_output=True)
        else:
            os.kill(p.pid, signal.SIGKILL)
        assert _wait(lambda: _health(port) is None, 15), "web server kept running after its supervisor died"
    finally:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True)
        else:
            p.kill()
