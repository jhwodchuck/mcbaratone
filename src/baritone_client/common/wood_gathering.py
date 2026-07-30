"""Boundaries and recovery policy for long-running wood expeditions."""

from __future__ import annotations

from typing import Any, Optional, Sequence


AQUATIC_STOP = "after aquatic safety intervention"


def required_log_count(client: Any, count: int) -> Optional[int]:
    """Return logs still needed, or ``None`` when carried wood is sufficient."""
    from . import resources as api

    total_logs = sum(api.count_item(client, block) for block in api.LOG_BLOCKS)
    if total_logs >= count:
        print(f"DEBUG: Already have {total_logs} logs, skipping gather")
        return None
    total_planks = sum(api.count_item(client, item) for item in api.PLANK_ITEMS)
    equivalent_logs = total_planks // 4
    if total_logs + equivalent_logs >= count:
        print(
            f"DEBUG: Have {total_logs} logs + {total_planks} planks "
            f"({equivalent_logs} log equiv) = enough! Skipping gather"
        )
        return None
    needed = count - total_logs - equivalent_logs
    print(
        f"DEBUG: Need {needed} more logs "
        f"(have {total_logs} logs, {total_planks} planks)"
    )
    return needed


def stop_reason(
    client: Any,
    state: dict,
    *,
    origin_x: float,
    origin_z: float,
    latest_world_time: Optional[int],
    max_distance_from_origin: Optional[float],
    minimum_health: float,
) -> Optional[str]:
    """Cancel an expedition at its aquatic, time, health, or range boundary."""
    from .combat import survival_tick

    reason = None
    if survival_tick(client, state):
        reason = AQUATIC_STOP
    elif (
        latest_world_time is not None
        and int(state.get("world_time", 0)) % 24000 >= int(latest_world_time)
    ):
        reason = "at its return-home boundary"
    elif float(state.get("health", 20.0)) < float(minimum_health):
        reason = "below safe health"
    else:
        position = state.get("block_position", state.get("position", {}))
        distance = (
            (float(position.get("x", 0)) - origin_x) ** 2
            + (float(position.get("z", 0)) - origin_z) ** 2
        ) ** 0.5
        if (
            max_distance_from_origin is not None
            and distance > float(max_distance_from_origin)
        ):
            reason = "at its expedition radius"
    if reason is None:
        return None
    print(f"DEBUG: Wood gathering stopped {reason}")
    client.transport.dispatch("cancel", {})
    return reason


def stop_reason_after_defense(
    client: Any,
    *,
    origin_x: float,
    origin_z: float,
    latest_world_time: Optional[int],
    max_distance_from_origin: Optional[float],
    minimum_health: float,
) -> Optional[str]:
    """Recheck an expedition after defense may have moved the bot."""
    from . import resources as api

    if getattr(client, "_last_defense_intervention", None) == "aquatic":
        print("DEBUG: Wood gathering stopped after aquatic defense intervention")
        client.transport.dispatch("cancel", {})
        return AQUATIC_STOP
    state = api._read_state_optional(
        client,
        retries=3,
        label="Wood gather post-defense state",
    )
    if state is None:
        return "because post-defense state was unavailable"
    return stop_reason(
        client,
        state,
        origin_x=origin_x,
        origin_z=origin_z,
        latest_world_time=latest_world_time,
        max_distance_from_origin=max_distance_from_origin,
        minimum_health=minimum_health,
    )


def recover_after_aquatic_stop(
    client: Any,
    reason: str,
    *,
    quantity: int,
    movement_watchdogs: Sequence[Any],
) -> bool:
    """Relocate to verified dry terrain before restarting a wood mine."""
    from . import resources as api

    if reason != AQUATIC_STOP or not api._relocate_to_dry_stone_terrain(client):
        return False
    state = api._read_state_optional(
        client,
        retries=3,
        label="Wood gather dry relocation",
    )
    if state is None:
        return False
    api._start_mine_process(client, api.LOG_BLOCKS, quantity)
    for watchdog in movement_watchdogs:
        watchdog.reset(state)
    return True


def maintain_survival_window(
    client: Any,
    state: dict,
    *,
    quantity: int,
) -> Optional[bool]:
    """Handle hunger/daylight gates and report whether mining was restarted."""
    from . import resources as api
    from .combat import eat_until_hunger

    food_level = int(state.get("food_level", state.get("food", 20)))
    if food_level <= 10:
        client.transport.dispatch("cancel", {})
        if not eat_until_hunger(client, minimum_food=14):
            print("DEBUG: Wood gathering stopped because food ran out")
            return False
        api._start_mine_process(client, api.LOG_BLOCKS, quantity)
        return True
    if not api._ensure_outdoor_daylight(client, state):
        return False
    if int(state.get("world_time", 0)) % 24000 < 12000:
        return None
    client.transport.dispatch(
        "mine",
        {"blocks": api.LOG_BLOCKS, "quantity": quantity},
    )
    return True
