"""The one-click Windows installer (Install.cmd → deploy/install_windows.ps1, v1.9.1): it parses, a dry run shows the
plan without changing anything, and the first-admin prompt confirms the password."""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PS = shutil.which("powershell") or shutil.which("pwsh")
needs_ps = pytest.mark.skipif(not (sys.platform == "win32" and PS), reason="Windows PowerShell only")


def test_install_cmd_runs_the_installer_elevated():
    cmd = (ROOT / "Install.cmd").read_text(encoding="utf-8")
    assert "-Verb RunAs" in cmd and r"deploy\install_windows.ps1" in cmd and "ExecutionPolicy Bypass" in cmd
    assert "pause" in cmd                                          # the window stays open to show the result


@needs_ps
def test_installer_parses():
    r = subprocess.run([PS, "-NoProfile", "-Command",
                        "$e=$null; [void][System.Management.Automation.Language.Parser]::ParseFile("
                        f"'{ROOT / 'deploy' / 'install_windows.ps1'}',[ref]$null,[ref]$e); $e.Count"],
                       capture_output=True, text=True, timeout=60)
    assert r.stdout.strip() == "0", r.stdout + r.stderr


@needs_ps
def test_installer_dry_run_changes_nothing(tmp_path):
    app, data = tmp_path / "app", tmp_path / "data"
    r = subprocess.run([PS, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ROOT / "deploy" / "install_windows.ps1"),
                        "-DryRun", "-HostName", "hsco-test", "-AppDir", str(app), "-DataDir", str(data), "-Port", "9443"],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "https://hsco-test:9443" in r.stdout and "DRY RUN - nothing was changed." in r.stdout
    assert str(app) in r.stdout and str(data) in r.stdout
    assert not app.exists() and not data.exists()


def test_create_admin_confirms_a_typed_password(data_dir, monkeypatch, capsys):
    from app import cli
    from app.db import session_scope
    from app.models import User
    from sqlalchemy import select
    answers = iter(["Strong#Pass123", "Strong#Pass124"])
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": next(answers))
    with pytest.raises(SystemExit) as e:
        cli.create_admin(["boss"])
    assert e.value.code == 3 and "different" in capsys.readouterr().out
    answers = iter(["Strong#Pass123", "Strong#Pass123"])
    cli.create_admin(["boss"])
    with session_scope() as db:
        assert db.scalar(select(User).where(User.username == "boss")).role == "admin"
