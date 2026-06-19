"""Experimental: drive the G1 a short, bounded distance on a voice command.

OFF by default (MOVEMENT_COMMANDS_ENABLED). When on, a recognised movement order sends
a low, time-bounded velocity to the G1 ``LocoClient`` and then stops. The robot must be
STANDING in Main/Regular mode (R1+X), the same mode arm gestures need. Best-effort and
conservative — a failure logs and the conversation continues; it never raises.

Source: ``unitree_sdk2py.g1.loco.g1_loco_client.LocoClient`` — ``Start()`` enters the
locomotion FSM, ``Move(vx, vy, omega)`` sets a velocity, ``StopMove()`` halts. We re-send
``Move`` at ~10 Hz for the command's duration (continuous-velocity controllers expect a
steady stream / have a watchdog) then ``StopMove``.
"""
from __future__ import annotations

import asyncio
import time

from app.logging_setup import get_logger, log_exception
from app.movement import MAX_DURATION_S, MAX_VX, MAX_VY, MAX_VYAW, MovementCommand
from config import settings

log = get_logger("robot.locomotion")


class NullLocomotion:
    """No-op locomotion used when movement is disabled or the SDK/robot is absent."""

    enabled = False

    async def execute(self, cmd: MovementCommand) -> bool:
        return False

    async def stop(self) -> None:
        pass

    async def close(self) -> None:
        pass


class G1Locomotion:
    def __init__(self) -> None:
        from unitree_sdk2py.g1.loco.g1_loco_client import LocoClient

        self._loco = LocoClient()
        self._loco.SetTimeout(10.0)
        self._loco.Init()
        self.enabled = True
        log.info("G1Locomotion ready (EXPERIMENTAL — robot must be standing in Main mode).")

    async def execute(self, cmd: MovementCommand) -> bool:
        """Run one bounded movement. Returns True if it was dispatched without error."""
        try:
            await asyncio.get_running_loop().run_in_executor(None, self._run_blocking, cmd)
            return True
        except Exception:
            log_exception(log, "Movement failed (non-fatal)")
            return False

    def _run_blocking(self, cmd: MovementCommand) -> None:
        # Make sure we're in the locomotion FSM. If Start() fails (robot sitting/damped,
        # wrong mode, or a comms error) DO NOT drive — issuing Move() then would be
        # unsafe (lurch/fall). A stop is always safe to attempt.
        try:
            self._loco.Start()
        except Exception:
            log_exception(log, "LocoClient.Start() failed — not driving (robot may not be standing)")
            if cmd.kind == "stop":
                self._stop_blocking()
            return
        if cmd.kind == "stop" or cmd.duration_s <= 0:
            self._stop_blocking()
            return
        # Clamp to safe bounds regardless of config, as a backstop (shared constants).
        vx = max(-MAX_VX, min(MAX_VX, cmd.vx))
        vy = max(-MAX_VY, min(MAX_VY, cmd.vy))
        vyaw = max(-MAX_VYAW, min(MAX_VYAW, cmd.vyaw))
        dur = max(0.0, min(MAX_DURATION_S, cmd.duration_s))
        log.info("Move %s (vx=%.2f vy=%.2f vyaw=%.2f) for %.1fs", cmd.kind, vx, vy, vyaw, dur)
        t_end = time.monotonic() + dur
        while time.monotonic() < t_end:
            self._loco.Move(vx, vy, vyaw)
            time.sleep(0.1)
        self._stop_blocking()

    def _stop_blocking(self) -> None:
        try:
            self._loco.StopMove()
        except Exception:
            try:
                self._loco.Move(0.0, 0.0, 0.0)
            except Exception:
                log_exception(log, "StopMove failed")

    async def stop(self) -> None:
        try:
            await asyncio.get_running_loop().run_in_executor(None, self._stop_blocking)
        except Exception:
            pass

    async def close(self) -> None:
        await self.stop()
