"""G1 friendly arm gestures while talking — an ``ArmController`` over the verified
``G1ArmActionClient``.

Source-verified (``g1_arm_action_client.py`` + C++ header):

* ``ExecuteAction(action_id: int)`` (service "arm", api id 7106); ``GetActionList()``.
* Action ids (high confidence): **25** face wave, **26** high wave, **17** clap,
  **27** shake hand, **99** release/relax. (NB: id **11 is a two-hand *kiss*, not a
  wave** — a common mistake.) The full preset table can vary by firmware, so run
  ``tools/g1_list_actions.py`` on the real robot to confirm.
* Arm actions require an **arm-control FSM** (ids {500, 501, 801}) and the robot
  **standing in Main/Regular mode** (R3: ``R1 + X``). Audio does not; gestures do.

Design: gestures are **best-effort** — every call is wrapped so a failure logs and
the conversation continues. Audio is the core; arms are a nicety.
"""
from __future__ import annotations

import asyncio

from app.logging_setup import get_logger, log_exception
from app.state import Emotion
from config import settings
from robot.interfaces import ArmController

log = get_logger("robot.arm")

# Verified action ids.
FACE_WAVE = 25
HIGH_WAVE = 26
CLAP = 17
SHAKE = 27
RELEASE = 99

# Which gesture to play for each reply emotion (None = stay still for that mood).
# Used by the one-shot express(); the talking loop uses TALK_PALETTE below.
GESTURE_FOR_EMOTION: dict[Emotion, int | None] = {
    Emotion.HAPPY: FACE_WAVE,
    Emotion.EXCITED: CLAP,
    Emotion.PLAYFUL: HIGH_WAVE,
    Emotion.CURIOUS: FACE_WAVE,
    Emotion.SURPRISED: HIGH_WAVE,
    Emotion.THOUGHTFUL: None,
    Emotion.NEUTRAL: None,
    Emotion.SAD: None,
    Emotion.ANGRY: None,
    Emotion.SLEEPY: None,
}

# Gestures cycled *continuously while the robot is talking* (see talk()). Unlike
# GESTURE_FOR_EMOTION, every mood has at least one gesture so the robot is NEVER
# frozen mid-reply — that was the "no movement while responding" bug. Expressive
# moods get a livelier palette; quieter moods get a single calm wave on repeat.
_CALM = [FACE_WAVE]
TALK_PALETTE: dict[Emotion, list[int]] = {
    Emotion.HAPPY: [FACE_WAVE, HIGH_WAVE],
    Emotion.EXCITED: [HIGH_WAVE, CLAP],
    Emotion.PLAYFUL: [HIGH_WAVE, FACE_WAVE, SHAKE],
    Emotion.CURIOUS: [FACE_WAVE, SHAKE],
    Emotion.SURPRISED: [HIGH_WAVE, CLAP],
    Emotion.THOUGHTFUL: _CALM,
    Emotion.NEUTRAL: _CALM,
    Emotion.SAD: _CALM,
    Emotion.ANGRY: _CALM,
    Emotion.SLEEPY: _CALM,
}


class G1ArmGestures(ArmController):
    def __init__(self) -> None:
        from unitree_sdk2py.g1.arm.g1_arm_action_client import G1ArmActionClient

        self._arm = G1ArmActionClient()
        self._arm.SetTimeout(10.0)
        self._arm.Init()
        self._loco = None
        if settings.ARM_ENTER_FSM:
            self._enter_arm_mode()
        else:
            log.warning(
                "ARM_ENTER_FSM=false — put the robot in Main mode (R1+X) + standing "
                "via the R3 remote, or gestures will be rejected (audio is unaffected)."
            )
        log.info("G1ArmGestures ready.")

    def _enter_arm_mode(self) -> None:
        try:
            from unitree_sdk2py.g1.loco.g1_loco_client import LocoClient

            self._loco = LocoClient()
            self._loco.SetTimeout(10.0)
            self._loco.Init()
            self._loco.Start()  # enter the standing/arm-capable FSM
            log.info("Entered locomotion FSM via LocoClient.Start().")
        except Exception:
            log_exception(log, "Could not enter arm FSM (gestures may be rejected)")

    # ---- ArmController ----
    async def greet(self) -> None:
        # Wake acknowledgement gesture (configurable via WAKE_GESTURE_ID / panel).
        await self._execute(settings.WAKE_GESTURE_ID, "greet")

    async def express(self, emotion: Emotion) -> None:
        action = GESTURE_FOR_EMOTION.get(emotion)
        if action is None:
            log.debug("No gesture for emotion %s", emotion.value)
            return
        await self._execute(action, f"express:{emotion.value}")

    async def talk(self, emotion: Emotion, stop: asyncio.Event) -> None:
        """Cycle gestures until ``stop`` is set, so the arms move the whole time the
        robot speaks. Each ExecuteAction is a blocking preset that plays to
        completion; we check ``stop`` between actions and bail out of the inter-
        gesture pause the instant speech ends. Best-effort — never raises."""
        if stop.is_set() or not settings.TALK_GESTURES_ENABLED:
            await stop.wait()
            return
        palette = self._talk_palette(emotion)
        if not palette:
            await stop.wait()
            return
        gap = max(0.0, settings.TALK_GESTURE_GAP_MS / 1000.0)
        cap = settings.TALK_GESTURE_MAX_PER_REPLY
        played = 0
        i = 0
        while not stop.is_set() and (cap <= 0 or played < cap):
            await self._execute(palette[i % len(palette)], f"talk:{emotion.value}")
            played += 1
            i += 1
            if stop.is_set() or gap == 0.0:
                continue
            # Pause between gestures, but wake immediately when speech ends.
            try:
                await asyncio.wait_for(stop.wait(), timeout=gap)
            except asyncio.TimeoutError:
                pass
        if played >= cap > 0:
            log.debug("talk loop hit the per-reply cap (%d); holding until speech ends.", cap)
            await stop.wait()

    def _talk_palette(self, emotion: Emotion) -> list[int]:
        if settings.TALK_GESTURE_IDS:
            return settings.TALK_GESTURE_IDS
        return TALK_PALETTE.get(emotion, _CALM)

    async def relax(self) -> None:
        await self._execute(RELEASE, "relax")

    async def close(self) -> None:
        await self.relax()

    # ---- internals ----
    async def _execute(self, action_id: int, label: str) -> None:
        try:
            await asyncio.get_running_loop().run_in_executor(
                None, self._arm.ExecuteAction, action_id
            )
            log.info("Arm gesture %s (id=%d)", label, action_id)
        except Exception:
            log_exception(log, f"Arm gesture {label} (id={action_id}) failed (non-fatal)")
