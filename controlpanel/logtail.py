"""Tail log / event files for the live console and transcript (poll-based, no deps).

``read_last`` returns the last N lines now; ``follow`` is an async generator that
first yields the last N lines then yields each new appended line as the file grows
(surviving rotation/truncation). Works on any OS.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import AsyncIterator


def read_last(path: Path, n: int = 200) -> list[str]:
    if not path.exists():
        return []
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()[-n:]
    except Exception:
        return []


async def follow(path: Path, last_n: int = 100, poll_s: float = 0.5) -> AsyncIterator[str]:
    for line in read_last(path, last_n):
        yield line
    pos = path.stat().st_size if path.exists() else 0
    buf = ""
    while True:
        await asyncio.sleep(poll_s)
        if not path.exists():
            pos, buf = 0, ""
            continue
        size = path.stat().st_size
        if size < pos:  # file rotated or truncated — start over
            pos, buf = 0, ""
        if size <= pos:
            continue
        try:
            with path.open("r", encoding="utf-8", errors="replace") as f:
                f.seek(pos)
                buf += f.read()
                pos = f.tell()
        except Exception:
            continue
        parts = buf.split("\n")
        buf = parts.pop()  # keep the trailing partial line
        for line in parts:
            yield line
