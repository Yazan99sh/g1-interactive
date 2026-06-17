"""Offline, audio-based wake-word detection via openWakeWord (optional).

This is an alternative to the STT-based wake matcher (``audio/wake.py``). When
``WAKE_ENGINE=openwakeword`` the standby loop feeds raw mic frames straight to a
local model — no OpenAI STT call per phrase, lower latency, fully offline — at the
cost of being phrase-/English-only (built-in models don't know "Hi Robot"; train a
custom model and point ``OWW_MODEL`` at its ``.onnx``/``.tflite``).

The package is an OPTIONAL dependency: it's imported lazily so the app runs without
it, and ``main.py`` falls back to the STT engine if construction fails. Audio in is
the same 16 kHz mono PCM16 the rest of the pipeline uses.
"""
from __future__ import annotations

from pathlib import Path

from app.logging_setup import get_logger, log_exception
from config import settings

log = get_logger("audio.wake_oww")


class OpenWakeWordDetector:
    """Streaming wake detector. ``feed(pcm)`` returns True when the phrase fires."""

    def __init__(
        self,
        model: str | None = None,
        threshold: float | None = None,
        framework: str | None = None,
    ) -> None:
        self.threshold = settings.OWW_THRESHOLD if threshold is None else threshold
        model = model or settings.OWW_MODEL
        framework = framework or settings.OWW_INFERENCE_FRAMEWORK

        # Lazy import so the app runs fine when openwakeword isn't installed.
        from openwakeword.model import Model  # type: ignore

        # A filesystem path => custom model; otherwise a built-in model name.
        if model and (Path(model).exists() or "/" in model or "\\" in model):
            kwargs = {"wakeword_models": [model]}
        else:
            kwargs = {"wakeword_models": [model]} if model else {}
        try:
            self._model = Model(inference_framework=framework, **kwargs)
        except Exception:
            # Some versions download built-ins on first use; retry with defaults.
            log_exception(log, f"Loading oww model '{model}' failed; trying defaults")
            self._model = Model(inference_framework=framework)
        self._labels = list(getattr(self._model, "models", {}).keys())
        log.info("OpenWakeWord ready (models=%s, threshold=%.2f, fw=%s).",
                 self._labels or [model], self.threshold, framework)

    def reset(self) -> None:
        """Clear the model's rolling buffers so we don't immediately re-trigger."""
        try:
            self._model.reset()
        except Exception:
            try:  # older versions: clear the prediction buffers manually
                for buf in getattr(self._model, "prediction_buffer", {}).values():
                    buf.clear()
            except Exception:
                pass

    def feed(self, pcm: bytes) -> bool:
        """Feed one mic chunk (16 kHz mono PCM16). True => wake phrase detected."""
        if not pcm:
            return False
        try:
            import numpy as np

            samples = np.frombuffer(pcm, dtype=np.int16)
            if samples.size == 0:
                return False
            scores = self._model.predict(samples)
            score = max(scores.values()) if scores else 0.0
            if score >= self.threshold:
                log.info("Wake (openWakeWord) score=%.2f >= %.2f", score, self.threshold)
                self.reset()
                return True
        except Exception:
            log_exception(log, "openWakeWord predict failed (non-fatal)")
        return False
