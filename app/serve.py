"""Run the web server (child process of the supervisor in run_server.py, or directly with --no-supervisor).

Exit code 75 = "restart me" (an update has been staged); the supervisor then applies it.
"""
from __future__ import annotations

import argparse
import os
import sys


def parse(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=None)
    ap.add_argument("--port", type=int, default=int(os.environ.get("HSCO_PORT", "8443")))
    ap.add_argument("--dev", action="store_true", help="plain HTTP on localhost")
    ap.add_argument("--no-supervisor", action="store_true")
    return ap.parse_args(argv)


def _parent_alive(pid: int) -> bool:
    if os.name == "nt":
        import ctypes
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x00100000, False, pid)          # SYNCHRONIZE
        if not h:
            return False
        try:
            return k.WaitForSingleObject(h, 0) == 0x102      # WAIT_TIMEOUT → still running
        finally:
            k.CloseHandle(h)
    try:
        os.kill(pid, 0)                                      # POSIX only (on Windows this would kill it)
        return True
    except OSError:
        return False


def _watch_parent() -> None:
    """If the supervisor is stopped (e.g. Task Scheduler 'End'), don't linger holding the port."""
    import threading
    import time
    pid = int(os.environ.get("HSCO_SUPERVISOR_PID", "0") or 0)
    if not pid:
        return

    def loop():
        while True:
            time.sleep(3)
            if not _parent_alive(pid):
                os._exit(0)
    threading.Thread(target=loop, name="parent-watchdog", daemon=True).start()


def main(argv=None) -> int:
    a = parse(argv)
    _watch_parent()
    if a.dev:
        os.environ.setdefault("HSCO_INSECURE_COOKIES", "1")
    import uvicorn

    from app import updater
    from app.config import get_config
    from app.main import create_app

    cfg = get_config()
    kw = {}
    if not a.dev:
        crt, key = cfg.data_dir / "certs" / "server.crt", cfg.data_dir / "certs" / "server.key"
        if not crt.exists():
            sys.exit("No TLS certificate. Run: python -m app.cli make-cert <server-hostname>  (or start with --dev)")
        kw = {"ssl_certfile": str(crt), "ssl_keyfile": str(key)}
    server = uvicorn.Server(uvicorn.Config(create_app(), host=a.host or ("127.0.0.1" if a.dev else "0.0.0.0"),
                                           port=a.port, proxy_headers=False, server_header=False, log_level="info",
                                           **kw))
    restart = {"flag": False}

    def request_restart():
        restart["flag"] = True
        server.should_exit = True

    updater.set_restart_hook(request_restart)
    server.run()
    return updater.RESTART_EXIT_CODE if restart["flag"] else 0


if __name__ == "__main__":
    sys.exit(main())
