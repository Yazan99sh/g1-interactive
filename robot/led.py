"""Head-LED state indicator for the G1.

Colours the robot's head LED by pipeline state so the robot's state is readable at
a glance — and "breathes" (smoothly pulses) for busy states like *thinking*.

Why a small background task instead of a one-shot ``set_led`` per state change:
a solid colour is just one RPC and then we idle (block on an event until the state
changes — no polling, no RPC spam). A *pulsing* state needs periodic updates, so the
task wakes ~12×/s and ramps the brightness. Pulsing defaults to *thinking* only,
during which no audio is playing, so the extra ``LedControl`` RPCs never contend with
``PlayStream`` on the speaker's RPC lock.

Everything is best-effort: the underlying ``sink.set_led`` already swallows errors,
and a missing/none sink (host dev mode) makes this a no-op.
"""
from __future__ import annotations

import asyncio
import math
from typing import Optional

from app.logging_setup import get_logger
from config import settings

log = get_logger("robot.led")

_FRAME_S = 0.08  # ~12 fps while pulsing


class LedIndicator:
    """Drives the head LED from the current pipeline state.

    Construct with the audio sink (which exposes ``set_led(r,g,b)``); call
    :meth:`start` once inside the event loop and :meth:`stop` on shutdown. Set the
    state from anywhere on the loop with :meth:`set_state` (cheap, non-blocking).
    """

    def __init__(self, sink=None) -> None:
        self._sink = sink
        self._enabled = bool(settings.HEAD_LED_ENABLED) and sink is not None and hasattr(sink, "set_led")
        self._colors: dict[str, tuple[int, int, int]] = {
            "standby": _rgb(settings.LED_STANDBY),
            "listening": _rgb(settings.LED_LISTENING),
            "thinking": _rgb(settings.LED_THINKING),
            "speaking": _rgb(settings.LED_SPEAKING),
            "error": _rgb(settings.LED_ERROR),
        }
        self._pulse = {s for s in settings.LED_PULSE_STATES if s in self._colors}
        self._period = max(0.2, settings.LED_PULSE_PERIOD_MS / 1000.0)
        self._state = "standby"
        self._override: Optional[tuple[int, int, int]] = None  # transient flash colour
        self._last_led: Optional[tuple[int, int, int]] = None   # de-dupe identical RPCs
        self._wake = asyncio.Event()
        self._task: Optional[asyncio.Task] = None
        self._stopped = False

    # ---- public API -------------------------------------------------------
    def set_state(self, state: str) -> None:
        """Switch the indicator to ``state`` (standby/listening/thinking/speaking)."""
        if state == self._state:
            return
        self._state = state
        self._wake.set()

    async def flash(self, state_or_color, ms: int = 600) -> None:
        """Briefly show a colour (a state name or an (r,g,b) tuple), then resume the
        current state. Used e.g. to blink red when a turn fails."""
        if not self._enabled:
            return
        color = self._colors.get(state_or_color) if isinstance(state_or_color, str) else _rgb(state_or_color)
        if color is None:
            return
        self._override = color
        self._wake.set()
        try:
            await asyncio.sleep(max(0.0, ms / 1000.0))
        finally:
            self._override = None
            self._wake.set()

    async def start(self) -> None:
        if not self._enabled or self._task is not None:
            return
        self._stopped = False
        self._task = asyncio.create_task(self._run(), name="led-indicator")
        log.info("Head-LED indicator on (pulse: %s).", ", ".join(sorted(self._pulse)) or "none")

    async def stop(self) -> None:
        self._stopped = True
        self._wake.set()
        task, self._task = self._task, None
        if task is not None:
            try:
                await asyncio.wait_for(task, timeout=2.0)
            except asyncio.TimeoutError:  # render task is wedged — force it down
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
            except Exception:
                pass  # task raised on its own and already finished
            # NB: a CancelledError of stop() itself is allowed to propagate.
        self._apply((0, 0, 0), force=True)  # leave the LED off on shutdown

    # ---- internals --------------------------------------------------------
    def _apply(self, color: tuple[int, int, int], force: bool = False) -> None:
        if self._sink is None:
            return
        if not force and color == self._last_led:
            return  # already showing this colour — don't re-send (true "idle" hold)
        self._last_led = color
        try:
            self._sink.set_led(*color)
        except Exception:
            pass  # set_led is already best-effort; never break the loop

    async def _wait_change(self) -> None:
        """Block until the state/override changes (no RPCs while a solid colour holds)."""
        try:
            await self._wake.wait()
        finally:
            self._wake.clear()

    async def _run(self) -> None:
        phase = 0.0
        step = 2 * math.pi * _FRAME_S / self._period
        while not self._stopped:
            if self._override is not None:
                self._apply(self._override)
                await self._wait_change()
                continue
            base = self._colors.get(self._state)
            if base is None:
                await self._wait_change()
                continue
            if self._state in self._pulse:
                # Smooth cosine breathe between 30% and 100% brightness.
                k = 0.30 + 0.70 * (0.5 - 0.5 * math.cos(phase))
                self._apply((int(base[0] * k), int(base[1] * k), int(base[2] * k)))
                phase = (phase + step) % (2 * math.pi)
                await asyncio.sleep(_FRAME_S)
            else:
                self._apply(base)
                phase = 0.0
                await self._wait_change()


def _rgb(value) -> tuple[int, int, int]:
    """Coerce a config list/tuple like [r, g, b] into a clamped (r, g, b) tuple."""
    try:
        r, g, b = (int(v) for v in list(value)[:3])
    except Exception:
        return (0, 0, 0)
    clamp = lambda c: max(0, min(255, c))  # noqa: E731
    return (clamp(r), clamp(g), clamp(b))
