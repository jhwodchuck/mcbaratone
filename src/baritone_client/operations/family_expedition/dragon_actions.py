"""Shared, fail-closed dragon actions for family expedition roles."""

from __future__ import annotations

import math
from typing import Any, Mapping, Optional

from ...common.combat import defend_or_flee
from ...common.combat_intent import CombatIntent, combat_intent
from ...common.combat_melee import execute_melee_strike
from ...common.combat_ranged import fire_best_ranged_attack
from ...common.dragon_combat import dragon_action_state
from ...common.navigation import goto
from .end_sequence import DragonRole


def _position(state: Mapping[str, Any]) -> tuple[float, float, float] | None:
    value = state.get("position", state.get("block_position"))
    if not isinstance(value, Mapping):
        return None
    try:
        return tuple(float(value[axis]) for axis in ("x", "y", "z"))
    except (KeyError, TypeError, ValueError):
        return None


def _guard_defense_snapshot(client: Any) -> dict | None:
    """Return the exact atomic End/Survival frame guard defense will use."""
    try:
        response = client.transport.dispatch(
            "get_combat_snapshot", {"radius": 64}
        )
    except Exception:
        return None
    if not isinstance(response, Mapping) or response.get("error"):
        return None
    data = response.get("data", response)
    if not isinstance(data, Mapping):
        return None
    state = data.get("player")
    entities = data.get("entities")
    skipped = data.get("skipped_count")
    if (
        not isinstance(state, Mapping)
        or not isinstance(entities, list)
        or not all(isinstance(entity, Mapping) for entity in entities)
        or isinstance(skipped, bool)
        or not isinstance(skipped, int)
        or skipped != 0
        or str(state.get("dimension", "")).lower()
        not in ("the_end", "minecraft:the_end")
        or str(state.get("game_mode", "")).lower() != "survival"
    ):
        return None
    return {
        "player": dict(state),
        "entities": [dict(entity) for entity in entities],
        "skipped_count": 0,
    }


def default_dragon_callback(
    _name: str,
    client: Any,
    role: DragonRole,
    target: Optional[Mapping[str, Any]],
) -> bool:
    """Run one bounded role tick through shared boss admission and tactics."""
    if role in {DragonRole.CHILD_GUARD, DragonRole.RESERVE}:
        snapshot = _guard_defense_snapshot(client)
        if snapshot is None:
            try:
                client.transport.dispatch("cancel", {})
            except Exception:
                pass
            return False
        defend_or_flee(client, observed_snapshot=snapshot)
        return True
    try:
        state = client.transport.dispatch("get_state", {})
    except Exception:
        return False
    if not (
        isinstance(state, Mapping)
        and str(state.get("dimension", "")).lower()
        in ("the_end", "minecraft:the_end")
        and str(state.get("game_mode", "")).lower() == "survival"
    ):
        try:
            client.transport.dispatch("cancel", {})
        except Exception:
            pass
        return False
    if target is None:
        return True
    position = target.get("position", {})
    if not isinstance(position, Mapping):
        return False
    target_data = dict(target)
    if target_data.get("id") is None:
        return False
    intent = CombatIntent.for_target(
        target_data["id"],
        purpose="family_expedition_dragon_support",
        target_type=target_data.get("type", ""),
        allow_boss=True,
    )
    state = dragon_action_state(client, target_data)
    if state is None:
        return False
    if role is DragonRole.CRYSTAL_ARCHER:
        with combat_intent(client, intent):
            return fire_best_ranged_attack(client, target_data)
    try:
        dragon = tuple(float(position[axis]) for axis in ("x", "y", "z"))
    except (KeyError, TypeError, ValueError):
        return False
    if abs(dragon[0]) > 12 or abs(dragon[2]) > 12 or dragon[1] > 85:
        return True
    bot_position = _position(state)
    if bot_position is None:
        return False
    if math.dist(bot_position, (0.0, 64.0, 0.0)) > 8.0:
        with combat_intent(client, intent):
            reached = goto(client, 0, 64, 0, timeout=8, tolerance=6.0)
        if not reached:
            after = _position(client.transport.dispatch("get_state", {}))
            if (
                after is None
                or bool(getattr(client, "_last_navigation_survival_abort", False))
                or math.dist(bot_position, after) < 1.5
            ):
                return False
        return True
    with combat_intent(client, intent):
        response = execute_melee_strike(
            client, target_data, dict(state), min_cooldown=0.9
        )
    return response.get("attacked") is True or response.get("reason") == "cooldown"


__all__ = ["default_dragon_callback"]
