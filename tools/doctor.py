#!/usr/bin/env python3
"""Preflight diagnostics for the G1 Interactive host — a PASS/WARN/FAIL table.

No paid API calls: only presence/format checks plus a single robot ping. Reads .env
with a tiny local parser (does not import the app, so it runs even before deps are
installed). Exit code 0 if no FAIL, 1 otherwise.

    python tools/doctor.py            # standard checks
    python tools/doctor.py --hw       # also note the robot SDK if present
"""
from __future__ import annotations

import argparse
import importlib.util
import platform
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV = ROOT / ".env"

_fails = 0
_warns = 0


def line(status: str, name: str, hint: str = "") -> None:
    global _fails, _warns
    if status == "FAIL":
        _fails += 1
    elif status == "WARN":
        _warns += 1
    print(f"[{status:4}] {name}" + (f"  ->  {hint}" if hint else ""))


def parse_env() -> dict[str, str]:
    out: dict[str, str] = {}
    if ENV.exists():
        for raw in ENV.read_text(encoding="utf-8", errors="replace").splitlines():
            s = raw.strip()
            if s and not s.startswith("#") and "=" in s:
                key, value = s.split("=", 1)
                out[key.strip()] = value.strip()
    return out


def have(mod: str) -> bool:
    try:
        return importlib.util.find_spec(mod) is not None
    except Exception:
        return False


def mask(v: str) -> str:
    return f"{v[:4]}…{v[-3:]}" if v and len(v) > 10 else ("set" if v else "")


def check_python() -> None:
    v = sys.version_info
    ok = v >= (3, 10)
    line("PASS" if ok else "FAIL", f"Python {v.major}.{v.minor}.{v.micro}",
         "" if ok else "need >= 3.10")


def check_imports() -> None:
    for m in ("httpx", "dotenv", "numpy", "sounddevice"):
        line("PASS" if have(m) else "FAIL", f"core dep: {m}",
             "" if have(m) else "pip install -r requirements.txt")
    for m in ("fastapi", "uvicorn"):
        line("PASS" if have(m) else "WARN", f"panel dep: {m}",
             "" if have(m) else "pip install -r controlpanel/requirements.txt")


def check_keys(env: dict[str, str]) -> None:
    if not ENV.exists():
        line("FAIL", ".env present", "cp .env.example .env")
        return
    line("PASS", ".env present")
    # LLM works with any one provider — the chain (ai/llm.py) drops endpoints without a key.
    if env.get("OPENAI_API_KEY") or env.get("OPENROUTER_API_KEY") or env.get("GEMINI_API_KEY"):
        line("PASS", "LLM key", "OpenAI / OpenRouter / Gemini set")
    else:
        line("FAIL", "LLM key", "set OPENAI_API_KEY, OPENROUTER_API_KEY or GEMINI_API_KEY")
    # STT needs OpenAI (gpt-4o-transcribe) or Groq (Whisper) — Gemini can't transcribe.
    if env.get("OPENAI_API_KEY") or env.get("GROQ_API_KEY"):
        line("PASS", "STT key", "OpenAI / Groq set")
    else:
        line("FAIL", "STT key", "set OPENAI_API_KEY or GROQ_API_KEY")
    if env.get("ELEVENLABS_API_KEY"):
        line("PASS", "ElevenLabs key", mask(env["ELEVENLABS_API_KEY"]))
    else:
        line("FAIL", "ElevenLabs key", "set ELEVENLABS_API_KEY")


def check_interface(env: dict[str, str]) -> None:
    iface = env.get("DDS_INTERFACE", "")
    if not iface:
        line("WARN", "DDS_INTERFACE", "not set in .env")
        return
    names: set[str] = set()
    try:
        import socket
        if hasattr(socket, "if_nameindex"):
            names = {n for _, n in socket.if_nameindex()}
    except Exception:
        pass
    if names:
        line("PASS" if iface in names else "WARN", f"DDS_INTERFACE={iface}",
             "" if iface in names else f"not found; have {sorted(names)}")
    else:
        line("WARN", f"DDS_INTERFACE={iface}", "could not enumerate NICs on this OS")


def check_robot() -> None:
    host = "192.168.123.161"
    flag = "-n" if platform.system() == "Windows" else "-c"
    try:
        r = subprocess.run(["ping", flag, "1", host], capture_output=True, timeout=6)
        line("PASS" if r.returncode == 0 else "WARN", f"robot ping {host}",
             "" if r.returncode == 0 else "no reply — check cabling / DDS_INTERFACE")
    except Exception as exc:
        line("WARN", f"robot ping {host}", str(exc))


def check_audio() -> None:
    if not have("sounddevice"):
        line("WARN", "input audio devices", "sounddevice not installed")
        return
    try:
        import sounddevice as sd
        ins = [d["name"] for d in sd.query_devices() if d["max_input_channels"] > 0]
        line("PASS" if ins else "WARN", "input audio devices", f"{len(ins)} found")
    except Exception as exc:
        line("WARN", "input audio devices", str(exc))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hw", action="store_true", help="note the robot SDK if present")
    args = ap.parse_args()

    print("=== G1 Interactive - doctor ===")
    env = parse_env()
    check_python()
    check_imports()
    check_keys(env)
    check_interface(env)
    check_robot()
    check_audio()
    if args.hw:
        line("PASS" if have("unitree_sdk2py") else "WARN", "unitree_sdk2py",
             "present" if have("unitree_sdk2py") else "not installed (host fallback)")

    print(f"\nSummary: {_fails} FAIL, {_warns} WARN")
    sys.exit(1 if _fails else 0)


if __name__ == "__main__":
    main()
