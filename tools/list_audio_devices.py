"""List audio input/output devices so you can set MIC_DEVICE in .env.

    python tools/list_audio_devices.py
"""
from __future__ import annotations


def main() -> None:
    try:
        import sounddevice as sd
    except Exception as exc:  # pragma: no cover
        print(f"sounddevice not available: {exc}")
        print("Install PortAudio + `pip install sounddevice`.")
        return

    print("Index  In/Out  Name")
    print("-----  ------  ----")
    for i, dev in enumerate(sd.query_devices()):
        io = []
        if dev["max_input_channels"] > 0:
            io.append("IN")
        if dev["max_output_channels"] > 0:
            io.append("OUT")
        print(f"{i:>5}  {'/'.join(io):<6}  {dev['name']}")
    try:
        default_in, default_out = sd.default.device
        print(f"\nDefault input index : {default_in}")
        print(f"Default output index: {default_out}")
    except Exception:
        pass


if __name__ == "__main__":
    main()
