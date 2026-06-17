"""Wake-word matching for the standby state.

We reuse the same STT we already pay for: in standby the controller records short
utterances (gated by VAD so we only transcribe when someone actually speaks),
transcribes them, and asks this matcher whether a wake phrase was said. This keeps
the whole stack uniform (no extra wake-word engine/model to install) and matches
both English ("hi robot") and Arabic ("هاي روبوت") robustly.

If you later want a fully-offline, zero-cost wake word, swap this for openWakeWord
or Porcupine behind the same ``matches()`` interface.
"""
from __future__ import annotations

import re
import unicodedata

# Arabic diacritics (harakat, shadda, sukun, tatweel).
_AR_DIACRITICS = re.compile(r"[ؐ-ًؚ-ٰٟۖ-ۭـ]")


def normalize(text: str) -> str:
    """Lower-case, strip Arabic diacritics, unify alef/ya/ta-marbuta, squash spaces."""
    text = unicodedata.normalize("NFKC", text or "").lower()
    text = _AR_DIACRITICS.sub("", text)
    text = (text.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
                .replace("ى", "ي").replace("ة", "ه"))
    text = re.sub(r"[^\w؀-ۿ]+", " ", text)  # punctuation -> space
    return re.sub(r"\s+", " ", text).strip()


class WakeWordDetector:
    def __init__(self, wake_words: list[str]) -> None:
        self.wake_words = [normalize(w) for w in wake_words if w.strip()]

    def matches(self, text: str) -> bool:
        if not text:
            return False
        norm = normalize(text)
        return any(w and w in norm for w in self.wake_words)
