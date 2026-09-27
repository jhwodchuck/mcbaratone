"""Global survival admission before selecting another objective."""

from typing import Any, Mapping

from ..common.survival_farm import tend_local_farm_for_food
from ..common.tasks import PlayerDeathDetected
from .phase_executor import (
    _acquire_checkpointed_emergency_food,
    _attempt_survival_recovery_food,
)


def objective_survival_safe(snapshot: Mapping[str, Any]) -> bool:
    """Return whether any ordinary objective is safe to start."""
    return (
        not bool(snapshot.get("is_dead", False))
        and float(snapshot.get("health", 20) or 0) >= 12.0
        and int(snapshot.get("food_level", snapshot.get("food", 20)) or 0) >= 10
    )


def _has_carried_emergency_bread_materials(client) -> bool:
    """Return whether carried wheat can replace a dangerous food trip."""
    try:
        from ..common.emergency_food import emergency_food_count
        from ..common.inventory import count_item

        return (
            emergency_food_count(client) == 0
            and count_item(client, "minecraft:wheat") >= 3
        )
    except Exception:
        return False


def _survival_admitted(snapshot: Mapping[str, Any], strategy=None) -> bool:
    safe = objective_survival_safe(snapshot)
    if safe and strategy is not None:
        strategy.resume_from_survival()
    return safe


def recover_survival_before_objective(client, state, strategy=None) -> bool:
    """Recover a critical player and fail closed until its margin is safe."""
    try:
        snapshot = client.transport.dispatch("get_state", {})
    except Exception as exc:
        print(f"RECOVERY: objective survival gate could not read state ({exc})")
        if strategy is not None:
            strategy.suspend_for_survival("live survival state is unavailable")
        return False
    if _survival_admitted(snapshot, strategy):
        return True

    health = float(snapshot.get("health", 0) or 0)
    food = int(snapshot.get("food_level", snapshot.get("food", 0)) or 0)
    print(
        "RECOVERY: blocking objective selection at "
        f"health={health:.1f}, food={food}"
    )
    try:
        if _has_carried_emergency_bread_materials(client):
            print("RECOVERY: using carried wheat before any storage navigation")
            _acquire_checkpointed_emergency_food(client, state)
            snapshot = client.transport.dispatch("get_state", {})
            if _survival_admitted(snapshot, strategy):
                return True
        # A farm beside the player is the one food source that needs neither
        # health nor exploration; try it before the blind search refuses.
        if tend_local_farm_for_food(client, state):
            snapshot = client.transport.dispatch("get_state", {})
            if _survival_admitted(snapshot, strategy):
                return True
        _attempt_survival_recovery_food(client, state)
        snapshot = client.transport.dispatch("get_state", {})
        if _survival_admitted(snapshot, strategy):
            return True
        _acquire_checkpointed_emergency_food(client, state)
        snapshot = client.transport.dispatch("get_state", {})
    except PlayerDeathDetected:
        if strategy is not None:
            strategy.suspend_for_survival("player death requires recovery")
        return False
    except Exception as exc:
        print(f"RECOVERY: objective survival gate failed non-fatally ({exc})")
        if strategy is not None:
            strategy.suspend_for_survival(f"survival recovery failed: {exc}")
        return False
    if _survival_admitted(snapshot, strategy):
        return True
    if strategy is not None:
        strategy.suspend_for_survival(
            f"critical survival margin: health={float(snapshot.get('health', 0) or 0):.1f}, "
            f"food={int(snapshot.get('food_level', snapshot.get('food', 0)) or 0)}"
        )
    print("RECOVERY: objective selection remains blocked by survival state")
    return False
