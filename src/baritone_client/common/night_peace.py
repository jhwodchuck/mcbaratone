"""Decide whether a night is worth hiding from at all."""

from __future__ import annotations

import time
from typing import Any

#: A hostile night essentially always has something within this far of the
#: player. Zero across consecutive sweeps means the world is not spawning any.
PEACEFUL_SCAN_RADIUS = 64
PEACEFUL_CONFIRMATIONS = 2
#: Shelter and defence get the opening of every night uncontested. Only once
#: the bot has been standing still this long, having seen nothing, is the
#: quiet worth trusting -- and nothing is scanned before then, so this cannot
#: preempt a threat response that would otherwise have run.
PEACEFUL_GRACE_SECONDS = 60.0

_SWEEPS = "_peaceful_night_sweeps"
_SINCE = "_peaceful_night_since"


def _reset(client: Any) -> None:
    setattr(client, _SWEEPS, 0)
    setattr(client, _SINCE, 0.0)


def night_is_over_or_harmless(client: Any, day_time: int) -> bool:
    """True when it is daytime, or when there is nothing to shelter from.

    Waiting out a night costs roughly seven minutes, and on a world with
    hostile spawning disabled it buys nothing: dragon-a's sealed campaign
    recorded 205 daylight holds against 3 completed objectives, the single
    largest category of lost runtime in the progression lane.

    A non-op client cannot read the gamerule, so the world is judged by what it
    contains. The risk runs both ways -- skipping shelter on a hostile world
    sends an unarmed bot out to die -- so this is deliberately slow to trust:
    nothing is scanned for the first minute, the sweep radius is wide enough
    that a hostile night would not leave it clear, consecutive empty sweeps are
    required, and any sighting resets the evidence.
    """
    if day_time < 12000:
        print(f"Daylight safety: daylight confirmed (time={day_time}).")
        _reset(client)
        return True

    started = float(getattr(client, _SINCE, 0.0) or 0.0)
    now = time.monotonic()
    if started <= 0.0:
        setattr(client, _SINCE, now)
        return False
    if now - started < PEACEFUL_GRACE_SECONDS:
        return False

    from .combat import scan_for_threats

    try:
        threats = scan_for_threats(client, radius=PEACEFUL_SCAN_RADIUS)
    except Exception:
        setattr(client, _SWEEPS, 0)
        return False
    if threats:
        setattr(client, _SWEEPS, 0)
        return False

    sweeps = int(getattr(client, _SWEEPS, 0) or 0) + 1
    setattr(client, _SWEEPS, sweeps)
    if sweeps < PEACEFUL_CONFIRMATIONS:
        return False
    print(
        f"Daylight safety: nothing hostile within {PEACEFUL_SCAN_RADIUS} blocks "
        f"across {sweeps} sweeps; not waiting out this night."
    )
    _reset(client)
    return True
