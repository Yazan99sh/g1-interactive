"""Robot-side interfaces the pipeline depends on.

Keeping these abstract lets the conversation pipeline run identically whether a
real G1 is attached or not:

* ``ArmController`` — friendly arm gestures while talking. Real impl
  (``robot.arm_gestures.G1ArmGestures``) is finalised from the verified research
  spec; ``NullArmController`` logs and no-ops for robot-less development.
* The audio-out interface is ``audio.sink.AudioSink`` (``G1Speaker`` vs ``HostSpeaker``).

The controller picks concrete implementations in ``main.py`` based on config.
"""
from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod

from app.logging_setup import get_logger
from app.state import Emotion

log = get_logger("robot.arm")


class ArmController(ABC):
    @abstractmethod
    async def greet(self) -> None:
        """A short 'hello' wave — used when the robot acknowledges the wake word."""

    @abstractmethod
    async def express(self, emotion: Emotion) -> None:
        """Play a single friendly gesture matching ``emotion`` (one-shot)."""

    async def talk(self, emotion: Emotion, stop: asyncio.Event) -> None:
        """Do one move as the robot starts talking, then hold until ``stop`` is set.

        Runs concurrently with audio playback and must return promptly once
        ``stop`` is set (the speech finished). The default expresses ``emotion`` once
        then waits; ``G1ArmGestures`` overrides it to use the configured talk gesture.
        """
        if not stop.is_set():
            await self.express(emotion)
        await stop.wait()

    @abstractmethod
    async def relax(self) -> None:
        """Return the arms to a neutral/resting pose after speaking."""

    async def close(self) -> None:
        """Release resources / put arms safely to rest."""


class NullArmController(ArmController):
    """No-op arms — used when ROBOT_ENABLED=false or the SDK isn't present."""

    async def greet(self) -> None:
        log.info("[null-arm] greet (wave)")

    async def express(self, emotion: Emotion) -> None:
        log.info("[null-arm] express %s", emotion.value)

    async def relax(self) -> None:
        log.info("[null-arm] relax")

    async def close(self) -> None:
        log.info("[null-arm] close")
