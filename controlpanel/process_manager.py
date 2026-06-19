"""Start/stop/restart the voice pipeline and report its status.

Two modes, auto-detected:
* **systemd** (preferred on the Linux host): a per-user unit ``g1-interactive.service``
  driven with ``systemctl --user`` — survives logout, auto-restarts on crash.
* **subprocess**: the panel launches ``python main.py`` itself and tracks the PID.

All systemd/os-specific calls are guarded so this module imports and runs (in
subprocess mode) on Windows for development too.

Diagnostics: in subprocess mode the child's stdout+stderr are captured to
``logs/pipeline.out.log`` so a crash *on launch* (e.g. a bad import or a missing
key) is visible instead of the panel silently showing "not running". ``start()``
waits for the process to settle and, if it died immediately, returns the exit code
and the tail of that log. In systemd mode the unit's ActiveState/SubState/Result
and (on failure) the last journal lines are surfaced the same way.
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
# Child stdout+stderr (subprocess mode) so launch-time crashes are visible.
STDOUT_LOG = paths.LOGS_DIR / "pipeline.out.log"
# How long to wait after launch before deciding the process stayed up.
SETTLE_S = 2.5


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


def _tail(path: Path, n: int = 20) -> str:
    """Last ``n`` non-empty-ish lines of a text file, or "" if unreadable."""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return ""
    tail = [ln.rstrip() for ln in lines[-n:]]
    return "\n".join(tail).strip()


class ProcessManager:
    def __init__(self) -> None:
        self._proc: subprocess.Popen | None = None
        self._out_fh = None  # open file handle for the captured child output
        self._started_at: float | None = None

    # ---- public API ----
    def status(self) -> dict[str, Any]:
        if _systemd_active():
            return self._systemd_status()
        return self._subprocess_status()

    def start(self) -> dict[str, Any]:
        if _systemd_active():
            r = _run(["systemctl", "--user", "start", SERVICE])
            # systemctl returns non-zero if the unit fails to start; capture why.
            time.sleep(0.5)
            st = self._systemd_status()
            if not st["running"] and r.stderr.strip():
                st["detail"] = f"{st['detail']} — {r.stderr.strip()}"
            return st
        return self._subprocess_start_and_settle()

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
            time.sleep(0.5)
            return self._systemd_status()
        self._subprocess_stop()
        time.sleep(0.3)
        return self._subprocess_start_and_settle()

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
        props = self._systemd_show(
            "ActiveState", "SubState", "Result", "MainPID",
            "ExecMainStatus", "ExecMainCode", "StatusText",
        )
        active = props.get("ActiveState", "unknown")
        sub = props.get("SubState", "")
        result = props.get("Result", "")
        running = active == "active"
        try:
            pid = int(props.get("MainPID", "0")) or None
        except ValueError:
            pid = None

        detail = f"unit {SERVICE}: {active}" + (f" ({sub})" if sub else "")
        recent = ""
        if not running:
            # Surface WHY it isn't running: the unit Result + the last journal lines.
            if result and result != "success":
                detail += f" — result={result}"
            if props.get("StatusText"):
                detail += f" — {props['StatusText']}"
            recent = self._journal_tail(25)

        out: dict[str, Any] = {
            "mode": "systemd", "running": running, "pid": pid, "since": None,
            "detail": detail, "systemd_available": True,
            "active_state": active, "sub_state": sub, "result": result,
        }
        if recent:
            out["recent_output"] = recent
        return out

    def _systemd_show(self, *props: str) -> dict[str, str]:
        args = ["systemctl", "--user", "show", SERVICE]
        for p in props:
            args += ["-p", p]
        out: dict[str, str] = {}
        for line in _run(args).stdout.splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                out[k] = v.strip()
        return out

    def _journal_tail(self, n: int) -> str:
        if shutil.which("journalctl") is None:
            return ""
        try:
            r = _run(["journalctl", "--user", "-u", SERVICE, "-n", str(n),
                      "--no-pager", "-o", "cat"])
            return r.stdout.strip()
        except Exception:
            return ""

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
        out: dict[str, Any] = {
            "mode": "subprocess" if running else "stopped",
            "running": running, "pid": pid,
            "since": self._started_at if running else None,
            "detail": "managed by control panel" if running else "not running",
            "systemd_available": _has_systemctl(),
        }
        if not running:
            # If we have a handle for a process we launched, report its exit code.
            code = self._proc.poll() if self._proc is not None else None
            if code is not None:
                out["exit_code"] = code
                out["detail"] = f"exited (code {code})"
            tail = _tail(STDOUT_LOG, 25)
            if tail:
                out["recent_output"] = tail
                out["detail"] += " — see recent output"
        return out

    def _subprocess_start_and_settle(self) -> dict[str, Any]:
        self._subprocess_start()
        # Poll until the process either stays up past SETTLE_S or dies early.
        deadline = time.monotonic() + SETTLE_S
        while time.monotonic() < deadline:
            if self._proc is not None and self._proc.poll() is not None:
                break  # died early — stop waiting, report the failure now
            time.sleep(0.2)
        return self._subprocess_status()

    def _subprocess_start(self) -> None:
        if self._subprocess_status()["running"]:
            return
        paths.ensure_state_dir()
        paths.LOGS_DIR.mkdir(parents=True, exist_ok=True)
        # Truncate so recent_output reflects THIS run, not a stale crash.
        try:
            self._out_fh = open(STDOUT_LOG, "w", encoding="utf-8", buffering=1)
        except Exception:
            self._out_fh = subprocess.DEVNULL  # type: ignore[assignment]
        kwargs: dict[str, Any] = {
            "cwd": str(paths.PROJECT_DIR),
            "stdout": self._out_fh,
            "stderr": subprocess.STDOUT,
        }
        if os.name == "posix":
            kwargs["start_new_session"] = True
        self._proc = subprocess.Popen([sys.executable, "main.py"], **kwargs)
        self._started_at = time.time()
        PID_FILE.write_text(str(self._proc.pid), encoding="utf-8")

    def _subprocess_stop(self) -> None:
        pid = self._read_pid()
        if pid is not None:
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
        if self._out_fh not in (None, subprocess.DEVNULL):
            try:
                self._out_fh.close()
            except Exception:
                pass
        self._out_fh = None
        self._proc = None
        self._started_at = None
