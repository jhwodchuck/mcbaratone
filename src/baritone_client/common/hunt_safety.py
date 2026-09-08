"""Resolve a hostile that interrupts a passive hunt, before giving up on it.

``hunt_mobs`` and ``execute_safe_combat``'s mid-approach check both correctly
detect an unrequested hostile intruding on a passive hunt (a cow-hunt
interrupted by a zombie, say) and abort -- but aborting only cancels the
beneficial action; it never engages the intruder. Live: a zombie killed the
bot outright across several "Gather 46 leather" cycles this way, each cycle
healing back to full between abort-and-do-nothing retries until one cycle
lost the race.
"""

from __future__ import annotations

import time
from typing import Any


def resolve_intruding_hostile(client: Any, *, timeout: float = 20.0) -> None:
    """Fight or flee the hostile that just aborted a passive hunt."""
    from .combat import defend_or_flee

    deadline = time.time() + timeout
    while time.time() < deadline and defend_or_flee(client):
        pass
