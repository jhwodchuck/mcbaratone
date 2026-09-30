"""Global survival admission before selecting another objective."""

from typing import Any, Mapping

from ..common.home_respawn import secure_home_respawn
from ..common.survival_farm import local_farm_wait_reason, tend_local_farm_for_food
from ..common.home_surface import bind_home_surface
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


def _secure_home_respawn(client, state) -> None:
    """Keep the home bed as the respawn point; never block the gate on it.

    It runs even while survival is critical: that is when the next death is
    likeliest, and without it the player respawns at distant world spawn.
    """
    try:
        secure_home_respawn(client, state)
    except Exception as exc:
        print(f"HOME RESPAWN: attempt failed non-fatally ({exc})")


def _survival_admitted(snapshot: Mapping[str, Any], strategy=None) -> bool:
    safe = objective_survival_safe(snapshot)
    if safe and strategy is not None:
        strategy.resume_from_survival()
    return safe


def recover_survival_before_objective(client, state, strategy=None) -> bool:
    """Recover a critical player and fail closed until its margin is safe."""
    bind_home_surface(client, state)
    try:
        snapshot = client.transport.dispatch("get_state", {})
    except Exception as exc:
        print(f"RECOVERY: objective survival gate could not read state ({exc})")
        if strategy is not None:
            strategy.suspend_for_survival("live survival state is unavailable")
        return False
    _secure_home_respawn(client, state)
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
        try:
            tend_local_farm_for_food(client, state)
        except PlayerDeathDetected:
            raise
        except Exception as exc:
            print(f"RECOVERY: local farm tending failed ({exc}); searching instead")
        # False may mean a partial meal, cooldown, or growing crops. Refresh
        # regardless; never turn an unmet hunger target into a blind trip.
        snapshot = client.transport.dispatch("get_state", {})
        if _survival_admitted(snapshot, strategy):
            return True
        wait_reason = local_farm_wait_reason(client, state, snapshot)
        if wait_reason:
            client.transport.dispatch("cancel", {})
            if strategy is not None:
                strategy.suspend_for_survival(wait_reason)
            print(f"RECOVERY: {wait_reason}; objective selection remains blocked")
            return False
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
