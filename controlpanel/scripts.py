"""List, validate, upload and run the project's diagnostic/action scripts.

Scripts live in ``tools/`` (shipped helpers) and ``scripts/`` (user-added). They are
run as ``python <file>`` with cwd=PROJECT_DIR (argv list, never a shell) and their
combined stdout/stderr is streamed to the panel console over a WebSocket.
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path
from typing import AsyncIterator

from . import paths

_DIRS = {"tools": paths.TOOLS_DIR, "scripts": paths.SCRIPTS_DIR}


def _first_doc_line(path: Path) -> str:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for _ in range(40):
                line = f.readline()
                if not line:
                    break
                s = line.strip().strip('"').strip("'").strip()
                if s and not s.startswith("#") and not s.startswith("from __future__"):
                    return s[:120]
    except Exception:
        pass
    return ""


def list_scripts() -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for label, directory in _DIRS.items():
        if not directory.exists():
            continue
        for path in sorted(directory.glob("*.py")):
            out.append({"name": path.name, "dir": label, "desc": _first_doc_line(path)})
    return out


def resolve(name: str, directory: str) -> Path:
    base = _DIRS.get(directory)
    if base is None:
        raise ValueError("unknown directory")
    return paths.safe_child(base, name, (".py",))


def save_upload(filename: str, data: bytes) -> str:
    paths.SCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
    target = paths.safe_child(paths.SCRIPTS_DIR, filename, (".py",))
    target.write_bytes(data)
    return target.name


class ScriptRunner:
    """Holds running subprocesses keyed by an opaque run_id."""

    def __init__(self) -> None:
        self._procs: dict[str, asyncio.subprocess.Process] = {}

    async def start(self, name: str, directory: str) -> str:
        path = resolve(name, directory)
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-u", str(path),
            cwd=str(paths.PROJECT_DIR),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        run_id = uuid.uuid4().hex
        self._procs[run_id] = proc
        return run_id

    async def stream(self, run_id: str) -> AsyncIterator[tuple[str, object]]:
        """Yield ('line', text) for each output line, then ('done', returncode)."""
        proc = self._procs.get(run_id)
        if proc is None:
            yield ("done", -1)
            return
        try:
            assert proc.stdout is not None
            async for raw in proc.stdout:
                yield ("line", raw.decode("utf-8", "replace").rstrip("\r\n"))
            await proc.wait()
            yield ("done", proc.returncode)
        finally:
            self._procs.pop(run_id, None)
