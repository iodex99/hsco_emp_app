"""Server-side administration commands.

  python -m app.cli create-admin            create the first administrator (interactive)
  python -m app.cli reset-mfa <username>    remove a user's 2FA (break-glass, run on the server)
  python -m app.cli backup                  consistent SQLite backup into data/backups
  python -m app.cli make-cert <hostname>    self-signed TLS certificate for HTTPS (data/certs)
  python -m app.cli import <files...>       import files from the command line (e.g. scheduled)
"""
from __future__ import annotations

import getpass
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import delete, select

from app.config import get_config
from app.db import init_db, session_scope
from app.models import MfaMethod, RecoveryCode, User
from app.security import core as sec


def create_admin(argv):
    init_db()
    username = (argv[0] if argv else input("Admin username: ")).strip().lower()
    pw = argv[1] if len(argv) > 1 else getpass.getpass("Password: ")
    probs = sec.password_problems(pw, 10, username)
    if probs:
        print("Password needs: " + ", ".join(probs))
        sys.exit(2)
    with session_scope() as db:
        if db.scalar(select(User).where(User.username == username)):
            print("User exists")
            sys.exit(1)
        db.add(User(username=username, full_name=username, role="admin", password_hash=sec.hash_password(pw)))
    print(f"Admin '{username}' created. Sign in and set up two-step verification.")


def reset_mfa(argv):
    init_db()
    with session_scope() as db:
        u = db.scalar(select(User).where(User.username == argv[0].lower()))
        if not u:
            print("No such user")
            sys.exit(1)
        db.execute(delete(MfaMethod).where(MfaMethod.user_id == u.id))
        db.execute(delete(RecoveryCode).where(RecoveryCode.user_id == u.id))
        u.locked_until = None
        u.failed_attempts = 0
    print("2FA reset; the user will enrol again at next sign-in.")


def backup(argv=None) -> Path:
    cfg = get_config()
    src = cfg.data_dir / "hsco.db"
    dst = cfg.data_dir / "backups" / f"hsco_{datetime.now():%Y%m%d_%H%M%S}.db"
    with sqlite3.connect(src) as a, sqlite3.connect(dst) as b:
        a.backup(b)
    keep = sorted((cfg.data_dir / "backups").glob("hsco_*.db"))
    for old in keep[:-60]:
        old.unlink()
    print(f"Backup written: {dst}")
    return dst


def make_cert(argv):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    import ipaddress

    host = argv[0] if argv else "localhost"
    cfg = get_config()
    d = cfg.data_dir / "certs"
    d.mkdir(exist_ok=True)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host)])
    sans = [x509.DNSName(host), x509.DNSName("localhost")]
    for h in argv[1:]:
        try:
            sans.append(x509.IPAddress(ipaddress.ip_address(h)))
        except ValueError:
            sans.append(x509.DNSName(h))
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(datetime.now(timezone.utc) - timedelta(days=1))
            .not_valid_after(datetime.now(timezone.utc) + timedelta(days=825))
            .add_extension(x509.SubjectAlternativeName(sans), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .sign(key, hashes.SHA256()))
    (d / "server.key").write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                                                     serialization.NoEncryption()))
    (d / "server.crt").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    print(f"Certificate for {host} written to {d}. Import server.crt into 'Trusted Root Certification Authorities' "
          "on client PCs (or use the firm's own certificate).")


def import_files(argv):
    from app.services.imports import import_file

    init_db()
    for f in argv:
        p = Path(f)
        with session_scope() as db:
            for r in import_file(db, p.read_bytes(), p.name, None):
                print(f"{p.name} [{r['sheet']}] → {r['label']} {r['period']} rows={r['rows']} {'; '.join(r['warnings'])}")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    cmd, argv = sys.argv[1], sys.argv[2:]
    {"create-admin": create_admin, "reset-mfa": reset_mfa, "backup": backup, "make-cert": make_cert,
     "import": import_files}.get(cmd, lambda a: print(__doc__))(argv)


if __name__ == "__main__":
    main()
