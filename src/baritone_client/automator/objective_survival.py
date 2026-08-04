"""Global survival admission before selecting another objective."""

from typing import Any, Mapping

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


def recover_survival_before_objective(client, state) -> bool:
    """Recover a critical player and fail closed until its margin is safe."""
    try:
        snapshot = client.transport.dispatch("get_state", {})
    except Exception as exc:
        print(f"RECOVERY: objective survival gate could not read state ({exc})")
        return False
    if objective_survival_safe(snapshot):
        return True

    health = float(snapshot.get("health", 0) or 0)
    food = int(snapshot.get("food_level", snapshot.get("food", 0)) or 0)
    print(
        "RECOVERY: blocking objective selection at "
        f"health={health:.1f}, food={food}"
    )
    try:
        _attempt_survival_recovery_food(client, state)
        snapshot = client.transport.dispatch("get_state", {})
        if objective_survival_safe(snapshot):
            return True
        _acquire_checkpointed_emergency_food(client, state)
        snapshot = client.transport.dispatch("get_state", {})
    except PlayerDeathDetected:
        return False
    except Exception as exc:
        print(f"RECOVERY: objective survival gate failed non-fatally ({exc})")
        return False
    if objective_survival_safe(snapshot):
        return True
    print("RECOVERY: objective selection remains blocked by survival state")
    return False
