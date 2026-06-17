"""Test a microphone on this host the SAME way the pipeline does (sounddevice).

Records a few seconds, shows a live RMS level meter while you speak, plays it back
so you can confirm it captured, and prints a suggested SILENCE_RMS_THRESHOLD plus
the exact MIC_DEVICE value to put in .env.

    python tools/mic_test.py                  # default input device, 4 seconds
    python tools/mic_test.py --device 5       # index from tools/list_audio_devices.py
    python tools/mic_test.py --device LARK     # match an input device by name substring
    python tools/mic_test.py --seconds 6 --no-playback

The device index/name you pass here is exactly what MIC_DEVICE expects.
"""
from __future__ import annotations

import argparse


def _resolve_device(sd, device: str | None):
    """Return a sounddevice index (or None for default) from an index or name."""
    if device is None or device == "":
        return None
    if device.isdigit():
        return int(device)
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0 and device.lower() in d["name"].lower():
            return i
    raise SystemExit(f"No INPUT device matching '{device}'. Run tools/list_audio_devices.py.")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--device", default=None, help="input device index or name substring")
    ap.add_argument("--seconds", type=float, default=4.0, help="record duration")
    ap.add_argument("--no-playback", action="store_true", help="don't play the recording back")
    args = ap.parse_args()

    try:
        import numpy as np
        import sounddevice as sd
    except Exception as exc:
        print(f"sounddevice/numpy not available: {exc}")
        print("Fix: sudo apt install -y libportaudio2 ; pip install sounddevice numpy")
        return

    dev = _resolve_device(sd, args.device)
    try:
        info = sd.query_devices(dev if dev is not None else sd.default.device[0], "input")
    except Exception as exc:
        print(f"Could not open input device {dev!r}: {exc}")
        print("Run tools/list_audio_devices.py to see valid indices/names.")
        return
    rate = int(info["default_samplerate"]) or 48000
    name = info["name"]
    print(f"Input device : [{dev if dev is not None else 'default'}] {name} @ {rate} Hz")
    print(f"Recording {args.seconds:.0f}s — SPEAK NOW (watch the level)…\n")

    blocks: list = []
    peak = [0.0]

    def cb(indata, _frames, _t, status):
        if status:
            pass  # over/underflows are non-fatal for a quick test
        block = indata[:, 0].copy()
        blocks.append(block)
        rms = float(np.sqrt(np.mean(block.astype(np.float64) ** 2))) if block.size else 0.0
        peak[0] = max(peak[0], rms)
        bar = "#" * min(50, int(rms / 200))
        print(f"\r  RMS {rms:6.0f} |{bar:<50}|", end="", flush=True)

    try:
        with sd.InputStream(samplerate=rate, channels=1, dtype="int16",
                            device=dev, blocksize=int(rate * 0.05), callback=cb):
            sd.sleep(int(args.seconds * 1000))
    except Exception as exc:
        print(f"\nRecording failed: {exc}")
        print("Try a different --device, or set the mic as the system default input.")
        return

    print()
    audio = np.concatenate(blocks) if blocks else np.zeros(0, dtype=np.int16)
    mean_rms = float(np.sqrt(np.mean(audio.astype(np.float64) ** 2))) if audio.size else 0.0
    print(f"\nCaptured {audio.size / rate:.1f}s — mean RMS {mean_rms:.0f}, peak RMS {peak[0]:.0f}")

    if peak[0] < 60:
        print("⚠️  Very low level — mic muted / wrong device / gain too low, or you didn't speak.")
    else:
        suggested = max(150, int(peak[0] * 0.3))
        print(f"✅ Mic is picking up sound. Suggested SILENCE_RMS_THRESHOLD ≈ {suggested}")
        print("   (speech peaks well above it; ambient silence should sit below it — tune to taste)")

    if not args.no_playback and audio.size:
        print("\nPlaying it back…")
        try:
            sd.play(audio, samplerate=rate)
            sd.wait()
        except Exception as exc:
            print(f"Playback failed (recording still valid): {exc}")

    idx = dev if dev is not None else "<index from tools/list_audio_devices.py>"
    print("\nTo use this mic, set in .env (or the panel's Environment tab):")
    print("  MIC_SOURCE=host")
    print(f"  MIC_DEVICE={idx}")


if __name__ == "__main__":
    main()
