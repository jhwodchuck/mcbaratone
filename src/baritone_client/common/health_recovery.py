"""Focused health stabilization used by survival and food recovery."""

from __future__ import annotations

import time
from typing import Any


def recover_health(
    client: Any,
    minimum_health: float = 12.0,
    timeout: float = 45.0,
) -> bool:
    """Hold position and consume available food until work is safe to resume."""
    from .combat import (
        _emergency_food_count,
        ensure_alive,
        heal_if_needed,
    )

    state = client.transport.dispatch("get_state", {})
    health = float(state.get("health", 20) or 0)
    if health >= minimum_health:
        return True

    print(f"RECOVERY: health {health:.1f}/{minimum_health:.1f}; stopping work")
    client.transport.dispatch("cancel", {})
    client.transport.dispatch("chat", {"message": "#stop"})

    deadline = time.time() + timeout
    last_food_attempt = 0.0
    while time.time() < deadline:
        state = client.transport.dispatch("get_state", {})
        health = float(state.get("health", 20) or 0)
        if health >= minimum_health:
            print(f"RECOVERY: safe to resume at {health:.1f} health")
            return True
        if health <= 0:
            ensure_alive(client)
            return False

        now = time.time()
        if now - last_food_attempt >= 4.0:
            last_food_attempt = now
            if not heal_if_needed(client, threshold=minimum_health):
                if _emergency_food_count(client) == 0:
                    print(
                        "RECOVERY: no edible food available; "
                        "refusing unsafe work"
                    )
                    return False
        time.sleep(2)

    print(f"RECOVERY: timed out below safe health ({health:.1f})")
    return False
