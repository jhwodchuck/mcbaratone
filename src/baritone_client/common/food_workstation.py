"""Route food-worker bread crafting to an observed saved-home workstation."""

import math
from typing import Any, Callable, Mapping, Optional, Sequence, Tuple

from .tasks import PlayerDeathDetected, SurvivalRecoveryRequired


BREAD = "minecraft:bread"
HOME_HORIZONTAL_TOLERANCE = 8.0
HOME_FLOOR_MARGIN = 2.0


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
        if live.get("is_pathing") is not False:
            return None
        if "overworld" not in str(live.get("dimension", "")).lower():
            return None
        if "game_mode" in live and str(live.get("game_mode", "")).casefold() != "survival":
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
        and anchor[1] - HOME_FLOOR_MARGIN <= position[1] <= anchor[1] + 8.0
    )


def _starter_house_origin(state: Any, anchor):
    custom = getattr(state, "custom_data", None)
    structures = custom.get("structures") if isinstance(custom, Mapping) else None
    house = structures.get("starter_house") if isinstance(structures, Mapping) else None
    origin = house.get("origin") if isinstance(house, Mapping) else None
    if not isinstance(origin, Sequence) or isinstance(origin, (str, bytes)) or len(origin) != 3:
        return None
    if any(isinstance(value, bool) or not isinstance(value, int) for value in origin):
        return None
    result = tuple(origin)
    if math.hypot(result[0] - anchor[0], result[2] - anchor[2]) > 24.0:
        return None
    if result[1] < anchor[1] - HOME_FLOOR_MARGIN:
        return None
    return result


def _safe_stand_block(client, position):
    from .farming import _block_data

    x, y, z = position
    try:
        if any(_block_data(client, x, dy, z).get("id") != "minecraft:air" for dy in (y, y + 1)):
            return False
        support = str(_block_data(client, x, y - 1, z).get("id", ""))
        return bool(support) and not any(
            token in support for token in (
                "air", "water", "lava", "powder_snow", "magma", "campfire",
                "cactus", "dripstone", "leaves", "fence", "pane", "slab",
                "stairs", "trapdoor",
            )
        )
    except Exception:
        return False


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

    This helper never creates a workstation or searches outside the recorded
    starter house. It routes to an observed table only after returning to the
    saved home, and delegates the final scoped craft to inventory.
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

        origin = _starter_house_origin(state, anchor)
        if origin is None:
            return False
        from .home_respawn import _bed_slots
        from .farming import _block_data
        from .navigation import goto

        ox, _, oz = origin
        interior_slots = _bed_slots(state, origin)
        interior = set(interior_slots)
        tables = [
            cell for cell in interior_slots
            if _block_data(client, *cell).get("id") == "minecraft:crafting_table"
        ]
        if not tables:
            return False
        for tx, ty, tz in tables:
            stands = [
                (tx + dx, ty, tz + dz)
                for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1))
                if (tx + dx, ty, tz + dz) in interior
            ]
            for stand in stands:
                if not _safe_stand_block(client, stand):
                    continue
                if not goto(client, *stand, timeout=20, tolerance=0.5, radius=0):
                    continue
                if not survival_ready(client):
                    return False
                arrived = _fresh_safe_position(client)
                if (
                    arrived is None
                    or math.floor(arrived[0]) != stand[0]
                    or math.floor(arrived[1]) != stand[1]
                    or math.floor(arrived[2]) != stand[2]
                    or arrived[1] < anchor[1] - HOME_FLOOR_MARGIN
                ):
                    continue
                from .enclosed_workstation import sheltered_bread_table

                restricted, observed = sheltered_bread_table(client)
                if restricted is not True or observed not in tables:
                    continue
                verified = _fresh_safe_position(client)
                if verified is None or not survival_ready(client):
                    return False
                if (
                    not (ox < math.floor(verified[0]) < ox + 6)
                    or not (oz < math.floor(verified[2]) < oz + 6)
                    or verified[1] < anchor[1] - HOME_FLOOR_MARGIN
                ):
                    continue
                return bool(craft(client, BREAD, count))
        return False
    except (PlayerDeathDetected, SurvivalRecoveryRequired):
        raise
    except Exception:
        return False
