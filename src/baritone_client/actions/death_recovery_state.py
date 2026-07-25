"""Persistence helpers for bounded death-recovery circuits."""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from ..core.interfaces import ActionResult


def abandon_exhausted_pending_recovery(
    state: Any,
    inventory: Dict[str, int],
) -> Optional[Dict[str, Any]]:
    """Clear a circuit-open grave after an operator restarts an alive player.

    The controller supervisor deliberately stops after an unsafe grave
    recovery. A later manual controller start is the repair boundary: if the
    player is already alive and the checkpoint records an unsafe failure, the
    old grave must not replace the location of the next real death.
    """
    custom_data = getattr(state, "custom_data", {})
    recovery = custom_data.get("death_recovery")
    if not isinstance(recovery, dict):
        return None
    location = recovery.get("pending_location")
    if (
        not isinstance(location, (list, tuple))
        or len(location) != 3
        or int(recovery.get("unsafe_failures", 0)) < 1
    ):
        return None

    abandoned = {
        "location": [int(value) for value in location],
        "dimension": str(
            recovery.get("pending_dimension", "minecraft:overworld")
        ),
        "unsafe_failures": int(recovery.get("unsafe_failures", 0)),
        "reason": "operator_restart_after_terminal_safety_stop",
    }
    custom_data["last_abandoned_death_recovery"] = abandoned
    custom_data.pop("death_recovery", None)

    save = getattr(state, "save_checkpoint", None)
    if callable(save):
        save(inventory)
    return abandoned


def handle_alive_pending_recovery(
    context: Any,
    live_state: Dict[str, Any],
    inventory_loader: Callable[[Any], Dict[str, int]],
) -> Optional[ActionResult]:
    """Return the no-death or operator-restart result for a living player."""
    if live_state.get("is_dead", False) or live_state.get("health", 20) <= 0:
        return None
    abandoned = abandon_exhausted_pending_recovery(
        context.state,
        inventory_loader(context.client),
    )
    if abandoned is None:
        return ActionResult.ok("No death detected")
    print(
        "RECOVERY_CIRCUIT: operator restart abandoned exhausted grave "
        f"{tuple(abandoned['location'])}"
    )
    return ActionResult.ok(
        "Exhausted pending grave abandoned after operator restart",
        grave_abandoned=True,
        location=abandoned["location"],
    )
