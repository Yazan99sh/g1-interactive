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
RIGHT_HAND_UP = 23
RELEASE = 99

# Which gesture to play for each reply emotion (None = stay still for that mood).
# Used by the one-shot express() (e.g. emotion gesture in say()).
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

# The single move the robot does when it STARTS talking. Used only when
# TALK_GESTURE_IDS is empty (normally that config gives the one move directly);
# every mood maps to a gesture so the robot is never frozen as it begins to speak.
_DEFAULT_TALK_GESTURE = RIGHT_HAND_UP
TALK_GESTURE_FOR_EMOTION: dict[Emotion, int] = {
    Emotion.HAPPY: FACE_WAVE,
    Emotion.EXCITED: CLAP,
    Emotion.PLAYFUL: HIGH_WAVE,
    Emotion.CURIOUS: RIGHT_HAND_UP,
    Emotion.SURPRISED: HIGH_WAVE,
    Emotion.THOUGHTFUL: RIGHT_HAND_UP,
    Emotion.NEUTRAL: RIGHT_HAND_UP,
    Emotion.SAD: RIGHT_HAND_UP,
    Emotion.ANGRY: RIGHT_HAND_UP,
    Emotion.SLEEPY: RIGHT_HAND_UP,
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
        """Do ONE arm move the moment the robot starts talking, then hold the pose
        until ``stop`` is set (speech finished) — "one move is enough", no looping.
        Best-effort — never raises; if disabled it just waits for ``stop``."""
        if stop.is_set() or not settings.TALK_GESTURES_ENABLED:
            await stop.wait()
            return
        gid = self._talk_gesture(emotion)
        if gid is not None and not stop.is_set():
            await self._execute(gid, f"talk:{emotion.value}")
        await stop.wait()

    def _talk_gesture(self, emotion: Emotion) -> int | None:
        if settings.TALK_GESTURE_IDS:
            return settings.TALK_GESTURE_IDS[0]
        return TALK_GESTURE_FOR_EMOTION.get(emotion, _DEFAULT_TALK_GESTURE)

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
