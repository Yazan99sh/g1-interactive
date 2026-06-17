"""Play a 1-second test tone out the G1's speaker — confirms the audio path.

    python tools/g1_speaker_test.py [interface]

If you hear a clean 440 Hz beep, PlayStream + PCM format + DDS interface are all
correct, and the ElevenLabs → speaker path will work.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import settings  # noqa: E402
from robot.dds import init_dds  # noqa: E402


def tone_pcm(freq: int = 440, seconds: float = 1.0, rate: int = 16000) -> bytes:
    out = bytearray()
    for n in range(int(rate * seconds)):
        val = int(0.3 * 32767 * math.sin(2 * math.pi * freq * n / rate))
        out += int(val).to_bytes(2, "little", signed=True)
    return bytes(out)


def main() -> None:
    iface = sys.argv[1] if len(sys.argv) > 1 else settings.DDS_INTERFACE
    init_dds(iface, settings.DDS_DOMAIN)
    from robot.speaker import G1Speaker

    spk = G1Speaker()
    print("Playing a 440 Hz tone for 1 second…")
    spk._play_blocking(tone_pcm())
    print("Done. If you heard a clean beep, the speaker path is good.")


if __name__ == "__main__":
    main()
