"""Shared "is this bot fit to fight" gate for hostile-facing objectives.

A refusal that does not also fix the thing it refuses over is a livelock, not
a safety feature. Bot17 proved it on 2026-08-06: once the leather expedition
started declining to hunt at 0/4 armor, it stopped dying and also stopped
progressing, sitting on 10 unspent iron ingots because nothing in
``ENCHANTING_PIPELINE`` ever rearmed it.

``NETHER_AND_BLAZE`` already owns a working rearm -- equip what is carried,
craft from carried iron, stabilize food and health, then provision the rest.
This exposes it to any phase that needs the same guarantee instead of growing
a third copy.
"""

from __future__ import annotations

from typing import Any


def ensure_combat_readiness(client: Any, state: Any) -> bool:
    """Restore armor, weapon, food, and health before hostile work.

    Returns True only when the bot is actually fit to fight. Its single
    Nether-specific branch (return to the Overworld first) is a no-op for
    Overworld callers.
    """
    from .nether_prep import NetherAndBlazeHandler

    try:
        return bool(
            NetherAndBlazeHandler()._ensure_nether_readiness(client, state)
        )
    except Exception as exc:  # pragma: no cover - defensive
        print(f"  Combat readiness check failed: {exc}")
        return False
