"""Start/stop/restart the voice pipeline and report its status.

Two modes, auto-detected:
* **systemd** (preferred on the Linux host): a per-user unit ``g1-interactive.service``
  driven with ``systemctl --user`` — survives logout, auto-restarts on crash.
* **subprocess**: the panel launches ``python main.py`` itself and tracks the PID.

All systemd/os-specific calls are guarded so this module imports and runs (in
subprocess mode) on Windows for development too.
"""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from . import paths

SERVICE = "g1-interactive.service"
USER_UNIT = Path.home() / ".config" / "systemd" / "user" / SERVICE
UNIT_TEMPLATE = paths.PROJECT_DIR / "deploy" / SERVICE
PID_FILE = paths.STATE_DIR / "pipeline.pid"


def _has_systemctl() -> bool:
    return shutil.which("systemctl") is not None


def _systemd_active() -> bool:
    """True when the user unit is installed (so we should manage via systemd)."""
    return _has_systemctl() and USER_UNIT.exists()


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=20)


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False
    except Exception:
        # Windows: os.kill(pid, 0) isn't supported — best-effort assume alive.
        return True


class ProcessManager:
    def __init__(self) -> None:
        self._proc: subprocess.Popen | None = None

    # ---- public API ----
    def status(self) -> dict[str, Any]:
        if _systemd_active():
            return self._systemd_status()
        return self._subprocess_status()

    def start(self) -> dict[str, Any]:
        if _systemd_active():
            _run(["systemctl", "--user", "start", SERVICE])
        else:
            self._subprocess_start()
        time.sleep(0.4)
        return self.status()

    def stop(self) -> dict[str, Any]:
        if _systemd_active():
            _run(["systemctl", "--user", "stop", SERVICE])
        else:
            self._subprocess_stop()
        time.sleep(0.2)
        return self.status()

    def restart(self) -> dict[str, Any]:
        if _systemd_active():
            _run(["systemctl", "--user", "restart", SERVICE])
            time.sleep(0.4)
            return self.status()
        self._subprocess_stop()
        time.sleep(0.3)
        self._subprocess_start()
        time.sleep(0.4)
        return self.status()

    def install_service(self) -> dict[str, Any]:
        """Render deploy/g1-interactive.service into the user unit dir and enable it."""
        if not _has_systemctl():
            return {"ok": False, "detail": "systemctl not found (not a systemd host)."}
        if not UNIT_TEMPLATE.exists():
            return {"ok": False, "detail": f"missing template {UNIT_TEMPLATE}"}
        try:
            text = UNIT_TEMPLATE.read_text(encoding="utf-8")
            text = (text.replace("__PROJECT_DIR__", str(paths.PROJECT_DIR))
                        .replace("__PYTHON__", sys.executable))
            USER_UNIT.parent.mkdir(parents=True, exist_ok=True)
            USER_UNIT.write_text(text, encoding="utf-8")
            _run(["systemctl", "--user", "daemon-reload"])
            _run(["systemctl", "--user", "enable", SERVICE])
            _run(["loginctl", "enable-linger", os.environ.get("USER", "")])
            return {"ok": True, "detail": f"installed {USER_UNIT}; manage via systemd now."}
        except Exception as exc:  # pragma: no cover
            return {"ok": False, "detail": f"install failed: {exc}"}

    # ---- systemd mode ----
    def _systemd_status(self) -> dict[str, Any]:
        active = _run(["systemctl", "--user", "is-active", SERVICE]).stdout.strip()
        running = active == "active"
        pid = None
        since = None
        show = _run(["systemctl", "--user", "show", SERVICE,
                     "-p", "MainPID", "-p", "ExecMainStartTimestampMonotonic"]).stdout
        for line in show.splitlines():
            if line.startswith("MainPID="):
                try:
                    pid = int(line.split("=", 1)[1]) or None
                except ValueError:
                    pid = None
        return {
            "mode": "systemd", "running": running, "pid": pid, "since": since,
            "detail": f"unit {SERVICE} is {active}", "systemd_available": True,
        }

    # ---- subprocess mode ----
    def _read_pid(self) -> int | None:
        try:
            return int(PID_FILE.read_text().strip())
        except Exception:
            return None

    def _subprocess_status(self) -> dict[str, Any]:
        pid = self._read_pid()
        running = pid is not None and _pid_alive(pid)
        if not running:
            pid = None
        return {
            "mode": "subprocess" if running else "stopped",
            "running": running, "pid": pid, "since": None,
            "detail": "managed by control panel" if running else "not running",
            "systemd_available": _has_systemctl(),
        }

    def _subprocess_start(self) -> None:
        if self._subprocess_status()["running"]:
            return
        paths.ensure_state_dir()
        kwargs: dict[str, Any] = {"cwd": str(paths.PROJECT_DIR)}
        if os.name == "posix":
            kwargs["start_new_session"] = True
        self._proc = subprocess.Popen([sys.executable, "main.py"], **kwargs)
        PID_FILE.write_text(str(self._proc.pid), encoding="utf-8")

    def _subprocess_stop(self) -> None:
        pid = self._read_pid()
        if pid is None:
            return
        try:
            if os.name == "posix":
                os.killpg(os.getpgid(pid), signal.SIGINT)
            else:
                os.kill(pid, signal.SIGTERM)
        except Exception:
            try:
                os.kill(pid, signal.SIGTERM)
            except Exception:
                pass
        try:
            PID_FILE.unlink()
        except Exception:
            pass
        self._proc = None
