"""Persistence helpers for bounded death-recovery circuits."""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from ..core.interfaces import ActionResult


def persist_pending_recovery(state: Any, expected: Dict[str, int]) -> None:
    """Best-effort checkpoint of a grave target before controller exit."""
    save = getattr(state, "save_checkpoint", None)
    if not callable(save):
        return
    try:
        save(expected)
    except Exception as exc:
        print(f"RECOVERY: pending grave checkpoint deferred ({exc})")


def mark_newer_death_unsafe(
    state: Any,
    recovery: Dict[str, Any],
    location: Any,
    dimension: str,
    expected: Dict[str, int],
) -> None:
    """Persist proof that a pending grave approach caused another death."""
    recovery["pending_location"] = [int(value) for value in location]
    recovery["pending_dimension"] = dimension
    recovery["unsafe_failures"] = max(
        1, int(recovery.get("unsafe_failures", 0)) + 1
    )
    persist_pending_recovery(state, expected)


def abandon_repeated_unsafe_pending_recovery(
    state: Any,
    inventory: Dict[str, int],
) -> Optional[Dict[str, Any]]:
    """Open the circuit before replaying a persisted lethal grave route.

    ``unsafe_failures`` is written only after an approach ended dead or could
    not reach the grave.  A supervisor relaunch can therefore abandon that
    exact route without risking another naked traversal through the same
    hazard.
    """
    custom_data = getattr(state, "custom_data", {})
    recovery = custom_data.get("death_recovery")
    if not isinstance(recovery, dict):
        return None
    location = recovery.get("pending_location")
    failures = int(recovery.get("unsafe_failures", 0))
    if (
        not isinstance(location, (list, tuple))
        or len(location) != 3
        or failures < 1
    ):
        return None

    abandoned = {
        "location": [int(value) for value in location],
        "dimension": str(
            recovery.get("pending_dimension", "minecraft:overworld")
        ),
        "unsafe_failures": failures,
        "reason": "repeated_unsafe_grave_route",
    }
    custom_data["last_abandoned_death_recovery"] = abandoned
    custom_data.pop("death_recovery", None)

    save = getattr(state, "save_checkpoint", None)
    if callable(save):
        save(inventory)
    return abandoned


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


def abandon_noncritical_alive_pending_recovery(
    state: Any,
    inventory: Dict[str, int],
) -> Optional[Dict[str, Any]]:
    """Clear a stale naked grave while the player is already alive.

    A failed approach used to leave ``pending_location`` with an empty
    ``expected_critical`` mapping. Because the alive path ignored that record,
    the next unrelated death resumed the old grave. There is nothing valuable
    to recover from an empty expectation, so preserving that target only adds
    risk.
    """
    custom_data = getattr(state, "custom_data", {})
    recovery = custom_data.get("death_recovery")
    if not isinstance(recovery, dict):
        return None
    location = recovery.get("pending_location")
    expected = recovery.get("expected_critical", {})
    if (
        not isinstance(location, (list, tuple))
        or len(location) != 3
        or not isinstance(expected, dict)
        or expected
    ):
        return None
    abandoned = {
        "location": [int(value) for value in location],
        "dimension": str(
            recovery.get("pending_dimension", "minecraft:overworld")
        ),
        "unsafe_failures": int(recovery.get("unsafe_failures", 0)),
        "reason": "alive_with_no_critical_items_pending",
    }
    custom_data["last_abandoned_death_recovery"] = abandoned
    custom_data.pop("death_recovery", None)
    save = getattr(state, "save_checkpoint", None)
    if callable(save):
        save(inventory)
    return abandoned


def abandon_unrecoverable_grave(
    state: Any,
    death_coords: Any,
    shortfall: Dict[str, int],
    inventory: Dict[str, int],
    failures: int,
) -> None:
    """Give up on a grave whose critical items can never be recovered.

    Items despawn five minutes after a death, and anything dropped into water
    washes away. Once that happens the ``expected_critical`` shortfall is
    permanently unsatisfiable, so retrying the same grave is an infinite loop:
    the controller reaches it, still comes up short, stops automation, and the
    supervisor relaunches straight back into the same trap. Confirmed live --
    Bot10 died 12 times in two hours cycling on one river grave whose iron kit
    was long gone. Clearing ``death_recovery`` drops both the pending location
    and its expected-item list so the next launch starts clean instead of
    resuming the trap.
    """
    custom_data = getattr(state, "custom_data", {})
    custom_data["last_abandoned_death_recovery"] = {
        "location": [int(value) for value in death_coords],
        "dimension": "minecraft:overworld",
        "unsafe_failures": int(failures),
        "missing": dict(shortfall),
        "reason": "critical_items_unrecoverable",
    }
    custom_data.pop("death_recovery", None)

    save = getattr(state, "save_checkpoint", None)
    if callable(save):
        try:
            save(inventory)
        except Exception as exc:  # never let a checkpoint write break recovery
            print(f"RECOVERY: abandoned-grave checkpoint deferred ({exc})")


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
        abandoned = abandon_noncritical_alive_pending_recovery(
            context.state,
            inventory_loader(context.client),
        )
    if abandoned is None:
        return ActionResult.ok("No death detected")
    print(
        "RECOVERY_CIRCUIT: abandoned stale or exhausted grave "
        f"{tuple(abandoned['location'])} ({abandoned['reason']})"
    )
    return ActionResult.ok(
        "Stale or exhausted pending grave abandoned while alive",
        grave_abandoned=True,
        location=abandoned["location"],
    )
