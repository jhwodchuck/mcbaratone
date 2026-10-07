"""Route food-worker bread crafting to an observed saved-home workstation."""

import math
from typing import Any, Callable, Mapping, Optional, Sequence, Tuple

from .tasks import PlayerDeathDetected, SurvivalRecoveryRequired


BREAD = "minecraft:bread"
HOME_HORIZONTAL_TOLERANCE = 8.0
HOME_HEIGHT_TOLERANCE = 4.0


def _fresh_safe_position(client: Any) -> Optional[Tuple[float, float, float]]:
    """Read complete, alive Survival state before trusting a home arrival."""
    try:
        response = client.transport.dispatch("get_state", {})
        if not isinstance(response, Mapping):
            return None
        for envelope in (response, response.get("data")):
            if isinstance(envelope, Mapping) and (
                envelope.get("success") is False
                or str(envelope.get("status", "")).lower() == "error"
                or envelope.get("error")
            ):
                return None
        live = response.get("data", response)
        if not isinstance(live, Mapping):
            return None
        health = live.get("health")
        if isinstance(health, bool) or not isinstance(health, (int, float)):
            return None
        if not math.isfinite(float(health)) or float(health) < 12:
            return None
        if live.get("is_dead") is not False:
            return None
        if "overworld" not in str(live.get("dimension", "")).lower():
            return None
        raw_position = live.get("block_position") or live.get("position")
        if not isinstance(raw_position, Mapping):
            return None
        position = []
        for axis in ("x", "y", "z"):
            value = raw_position.get(axis)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return None
            number = float(value)
            if not math.isfinite(number):
                return None
            position.append(number)
        return tuple(position)
    except (PlayerDeathDetected, SurvivalRecoveryRequired):
        raise
    except Exception:
        return None


def _near_home(
    position: Tuple[float, float, float], anchor: Tuple[float, float, float]
) -> bool:
    return (
        math.hypot(position[0] - anchor[0], position[2] - anchor[2])
        <= HOME_HORIZONTAL_TOLERANCE
        and abs(position[1] - anchor[1]) <= HOME_HEIGHT_TOLERANCE
    )


def craft_bread_at_saved_home(
    client: Any,
    state: Any,
    home_anchor: Optional[Sequence[int]],
    count: int,
    *,
    survival_ready: Callable[[Any], bool],
    return_home: Callable[[Any, Sequence[int]], bool],
    craft: Callable[[Any, str, int], bool],
) -> bool:
    """Craft bread only at the saved home's freshly verified enclosed table.

    This helper never creates a workstation or searches outside the house. The
    inventory craft contract performs the final scoped-table approach/open and
    verifies the craft; this function establishes the saved-home precondition.
    """
    if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
        return False
    if (
        not isinstance(home_anchor, Sequence)
        or isinstance(home_anchor, (str, bytes))
        or len(home_anchor) != 3
    ):
        return False
    try:
        if any(isinstance(value, bool) for value in home_anchor):
            return False
        anchor = tuple(float(value) for value in home_anchor)
    except (TypeError, ValueError, OverflowError):
        return False
    if not all(math.isfinite(value) for value in anchor):
        return False

    from .home_surface import bind_home_surface

    try:
        bind_home_surface(client, state)
        protected = getattr(client, "_protected_home_anchor", None)
        if protected is None or tuple(float(value) for value in protected) != anchor:
            return False
        if not survival_ready(client):
            return False
        position = _fresh_safe_position(client)
        if position is None:
            return False
        if not _near_home(position, anchor):
            if not return_home(client, home_anchor):
                return False
            if not survival_ready(client):
                return False
            position = _fresh_safe_position(client)
            if position is None or not _near_home(position, anchor):
                return False

        from .enclosed_workstation import sheltered_bread_table

        restricted, table = sheltered_bread_table(client)
        if restricted is not True or table is None:
            return False
        return bool(craft(client, BREAD, count))
    except (PlayerDeathDetected, SurvivalRecoveryRequired):
        raise
    except Exception:
        return False
