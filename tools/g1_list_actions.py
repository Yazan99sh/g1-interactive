"""Print the G1's arm-action preset list — run ON the robot LAN to confirm ids.

Action ids can vary by firmware, so verify the wave/clap/release ids this code
uses (25/26/17/27/99) against what your robot actually reports.

    python tools/g1_list_actions.py [interface]
    # interface defaults to DDS_INTERFACE from .env (e.g. ens37)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import settings  # noqa: E402
from robot.dds import init_dds  # noqa: E402


def main() -> None:
    iface = sys.argv[1] if len(sys.argv) > 1 else settings.DDS_INTERFACE
    init_dds(iface, settings.DDS_DOMAIN)
    from unitree_sdk2py.g1.arm.g1_arm_action_client import G1ArmActionClient

    arm = G1ArmActionClient()
    arm.SetTimeout(10.0)
    arm.Init()
    print("Arm action list (match the names to the ids used in arm_gestures.py):\n")
    print(arm.GetActionList())


if __name__ == "__main__":
    main()
