"""Start the web server.

  python run_server.py                 HTTPS on 0.0.0.0:8443 using data/certs/server.crt|key
  python run_server.py --dev           HTTP on 127.0.0.1:8443 (local testing only; cookies not 'Secure')
  python run_server.py --port 9443 --host 0.0.0.0
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=None)
    ap.add_argument("--port", type=int, default=int(os.environ.get("HSCO_PORT", "8443")))
    ap.add_argument("--dev", action="store_true", help="plain HTTP on localhost")
    a = ap.parse_args()
    if a.dev:
        os.environ.setdefault("HSCO_INSECURE_COOKIES", "1")
        os.environ.setdefault("HSCO_ORIGIN", f"http://localhost:{a.port}")
    import uvicorn
    from app.config import get_config
    from app.main import create_app

    cfg = get_config()
    kw = {}
    if not a.dev:
        crt, key = cfg.data_dir / "certs" / "server.crt", cfg.data_dir / "certs" / "server.key"
        if not crt.exists():
            sys.exit("No TLS certificate. Run: python -m app.cli make-cert <server-hostname>  (or start with --dev)")
        kw = {"ssl_certfile": str(crt), "ssl_keyfile": str(key)}
    uvicorn.run(create_app(), host=a.host or ("127.0.0.1" if a.dev else "0.0.0.0"), port=a.port, proxy_headers=False,
                server_header=False, log_level="info", **kw)


if __name__ == "__main__":
    main()
