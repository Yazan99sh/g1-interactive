"""DDS initialisation shared by every robot client.

``unitree_sdk2_python`` uses one global CycloneDDS participant. Initialise it
ONCE, before constructing any client, binding to the NIC that reaches the robot's
``192.168.123.x`` network — the same wired interface used for teleop (e.g.
``ens37`` / ``eth0``). This mirrors the Go2 ``ChannelFactoryInitialize(0, iface)``
pattern that already works in this project.
"""
from __future__ import annotations

from app.logging_setup import get_logger

log = get_logger("robot.dds")

_INITIALISED = False


def init_dds(interface: str, domain: int = 0) -> None:
    global _INITIALISED
    if _INITIALISED:
        return
    # Imported here so the app still runs (host fallback) without the SDK installed.
    from unitree_sdk2py.core.channel import ChannelFactoryInitialize

    if interface:
        ChannelFactoryInitialize(domain, interface)
    else:
        ChannelFactoryInitialize(domain)
    _INITIALISED = True
    log.info("ChannelFactoryInitialize(%d, '%s')", domain, interface or "<auto>")


def is_initialised() -> bool:
    return _INITIALISED
