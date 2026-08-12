"""Supervised Ender Dragon tactics for the production World Unlock path."""

from __future__ import annotations

import math
import time
from typing import Any

from . import combat_telemetry
from .combat import (
    defend_or_flee,
    eat_until_hunger,
    ensure_alive,
    heal_if_needed,
)
from .combat_intent import CombatIntent, combat_intent
from .combat_melee import execute_melee_strike
from .combat_ranged import fire_best_ranged_attack, ranged_attack_in_flight
from .dragon_readiness import (
    _live_dragon_kit_ready,
    dragon_kit_provision_requirements,
    dragon_kit_ready,
)
from .end_search import crystal_cage_search_payload, exit_portal_search_payload

_ADMISSION_HEALTH = 18.0
_ADMISSION_FOOD = 18
_CONTINUE_HEALTH = 14.0
_CONTINUE_FOOD = 14
_MAX_CRYSTAL_OPERATIONS = 4


def _unwrap(response: Any) -> dict:
    if not isinstance(response, dict):
        return {}
    data = response.get("data")
    return data if isinstance(data, dict) else response


def _entity_frame(response: Any) -> tuple[list[dict], int] | None:
    if not isinstance(response, dict) or response.get("error"):
        return None
    data = response.get("data", response)
    values = data.get("entities") if isinstance(data, dict) else None
    skipped = data.get("skipped_count", 0) if isinstance(data, dict) else None
    if (
        not isinstance(values, list)
        or not all(isinstance(value, dict) for value in values)
        or isinstance(skipped, bool)
        or not isinstance(skipped, int)
        or skipped < 0
    ):
        return None
    return values, skipped


def _found(response: Any) -> list[dict]:
    values = _unwrap(response).get("found", [])
    return [value for value in values if isinstance(value, dict)]


def _exit_portal_visible(client: Any) -> bool:
    return bool(
        _found(
            client.transport.dispatch(
                "find_blocks",
                exit_portal_search_payload(),
            )
        )
    )


def _survival_values(state: Any) -> tuple[float, int] | None:
    """Read bridge health/food without inventing missing telemetry."""
    if not isinstance(state, dict):
        return None
    health_value = state.get("health")
    food_value = state.get("food_level", state.get("food"))
    if (
        isinstance(health_value, bool)
        or not isinstance(health_value, (int, float))
        or isinstance(food_value, bool)
        or not isinstance(food_value, int)
    ):
        return None
    health = float(health_value)
    if not math.isfinite(health) or health < 0 or not 0 <= food_value <= 20:
        return None
    return health, food_value


def _is_survival_end_state(state: Any) -> bool:
    """Require the exact world and game mode before any boss mutation."""
    return bool(
        isinstance(state, dict)
        and str(state.get("dimension", "")).lower()
        in ("the_end", "minecraft:the_end")
        and str(state.get("game_mode", "")).lower() == "survival"
    )


def dragon_action_state(client: Any, target: dict) -> dict | None:
    """Return a fresh state only when one coordinated boss action is safe."""
    try:
        state = client.transport.dispatch("get_state", {})
        ensure_alive(client, state)
        if not _is_survival_end_state(state):
            client.transport.dispatch("cancel", {})
            return None
        if not _fight_margin_ready(client, state, admission=True):
            return None
        state = client.transport.dispatch("get_state", {})
        if not _fight_margin_ready(client, state, admission=True):
            return None
        if not _live_dragon_kit_ready(client):
            client.transport.dispatch("cancel", {})
            combat_telemetry.record_combat_action(
                client, "dragon_kit", outcome="action_refused"
            )
            return None
        frame = _entity_frame(
            client.transport.dispatch("get_entities", {"radius": 128})
        )
        if frame is None or frame[1]:
            client.transport.dispatch("cancel", {})
            return None
        observed = frame[0]
        local_dragon = next(
            (
                entity
                for entity in observed
                if entity.get("type") == "minecraft:ender_dragon"
            ),
            target if target.get("type") == "minecraft:ender_dragon" else None,
        )
        crystal = target if target.get("type") == "minecraft:end_crystal" else None
        with combat_intent(client, _fight_intent(local_dragon, crystal)):
            if defend_or_flee(
                client,
                allow_safe_recovery_movement=True,
                observed_snapshot={
                    "player": state,
                    "entities": observed,
                    "skipped_count": frame[1],
                },
            ):
                return None
        return state
    except Exception:
        try:
            client.transport.dispatch("cancel", {})
        except Exception:
            pass
        return None


def _fight_margin_ready(client: Any, state: dict, *, admission: bool) -> bool:
    """Restore a bounded survival margin or refuse another attack cycle."""
    minimum_health = _ADMISSION_HEALTH if admission else _CONTINUE_HEALTH
    minimum_food = _ADMISSION_FOOD if admission else _CONTINUE_FOOD
    if not _is_survival_end_state(state):
        client.transport.dispatch("cancel", {})
        combat_telemetry.record_combat_action(
            client, "survival_margin", outcome="invalid_world_context"
        )
        return False
    ensure_alive(client, state)
    survival = _survival_values(state)
    if survival is None:
        client.transport.dispatch("cancel", {})
        combat_telemetry.record_combat_action(
            client, "survival_margin", outcome="invalid_telemetry"
        )
        return False
    health, food = survival
    if health >= minimum_health and food >= minimum_food:
        return True
    client.transport.dispatch("cancel", {})
    combat_telemetry.record_combat_action(
        client,
        "survival_margin",
        outcome="recovering",
        health=health,
        food=food,
    )
    if food < 18:
        eat_until_hunger(client, minimum_food=18)
    if health < 18.0:
        heal_if_needed(client, threshold=18.0)
    refreshed = client.transport.dispatch("get_state", {})
    ensure_alive(client, refreshed)
    if not _is_survival_end_state(refreshed):
        client.transport.dispatch("cancel", {})
        return False
    refreshed_survival = _survival_values(refreshed)
    if refreshed_survival is None:
        return False
    refreshed_health, refreshed_food = refreshed_survival
    return refreshed_health >= minimum_health and refreshed_food >= minimum_food


def _escape_breath(client: Any, clouds: list[dict], state: dict) -> bool | None:
    """Move away from nearby dragon-breath clouds before choosing a target."""
    position = state.get("block_position", state.get("position", {})) or {}
    if not all(axis in position for axis in ("x", "y", "z")):
        return False if clouds else None
    px, py, pz = (float(position[axis]) for axis in ("x", "y", "z"))
    nearby = []
    for cloud in clouds:
        cloud_pos = cloud.get("position") or {}
        if not all(axis in cloud_pos for axis in ("x", "z")):
            continue
        dx = px - float(cloud_pos["x"])
        dz = pz - float(cloud_pos["z"])
        distance = math.hypot(dx, dz)
        if distance <= 8.0:
            nearby.append((dx, dz, distance))
    if not nearby:
        return None
    away_x = sum(dx / max(0.25, distance) for dx, _dz, distance in nearby)
    away_z = sum(dz / max(0.25, distance) for _dx, dz, distance in nearby)
    length = math.hypot(away_x, away_z)
    if length < 0.1:
        away_x, away_z, length = 1.0, 0.0, 1.0
    from . import navigation

    destination = (
        int(round(px + away_x / length * 12.0)),
        int(round(py)),
        int(round(pz + away_z / length * 12.0)),
    )
    combat_telemetry.record_combat_action(
        client,
        "dragon_breath_escape",
        outcome="attempted",
        destination={"x": destination[0], "y": destination[1], "z": destination[2]},
    )
    return navigation.goto(client, *destination, timeout=12, tolerance=4.0)


def _fight_intent(dragon: dict | None, crystal: dict | None) -> CombatIntent:
    ids = {
        int(entity["id"])
        for entity in (dragon, crystal)
        if entity is not None and entity.get("id") is not None
    }
    return CombatIntent(
        purpose="ender_dragon_fight",
        target_ids=frozenset(ids),
        # Type authorization survives an ID refresh without hiding other threats.
        target_types=frozenset({"ender_dragon"}),
        allow_boss=True,
        authorize_matching_types=True,
    )


def _verified_entities(client: Any, radius: int) -> list[dict] | None:
    try:
        response = client.transport.dispatch("get_entities", {"radius": radius})
    except Exception:
        return None
    frame = _entity_frame(response)
    return frame[0] if frame is not None and frame[1] == 0 else None


def _crystal_progress(client: Any, crystal: dict, timeout: float = 2.5) -> bool:
    """Verify disappearance or a health decrease; a dispatch alone is not a hit."""
    crystal_id = crystal.get("id")
    if crystal_id is None:
        return False
    before_health = crystal.get("health")
    before_health = (
        float(before_health)
        if isinstance(before_health, (int, float)) and not isinstance(before_health, bool)
        else None
    )
    deadline = time.time() + max(0.0, timeout)
    while time.time() < deadline:
        values = _verified_entities(client, 128)
        if values is not None:
            current = next(
                (value for value in values if value.get("id") == crystal_id), None
            )
            if current is None:
                return True
            current_health = current.get("health")
            if (
                before_health is not None
                and isinstance(current_health, (int, float))
                and not isinstance(current_health, bool)
                and float(current_health) < before_health
            ):
                return True
        time.sleep(0.15)
    return False


def _block_id(client: Any, position: tuple[int, int, int]) -> str | None:
    try:
        response = client.transport.dispatch(
            "get_block", {"x": position[0], "y": position[1], "z": position[2]}
        )
    except Exception:
        return None
    if not isinstance(response, dict) or response.get("error"):
        return None
    data = response.get("data", response)
    block_id = data.get("id") if isinstance(data, dict) else None
    return str(block_id) if block_id is not None else None


def _cage_blocks(
    client: Any, crystal: dict, state: dict
) -> list[tuple[int, int, int]] | None:
    """Find loaded cage bars close enough to surround this exact crystal."""
    position = crystal.get("position") or {}
    if not all(isinstance(position.get(axis), (int, float)) for axis in ("x", "y", "z")):
        return None
    try:
        response = client.transport.dispatch(
            "find_blocks",
            crystal_cage_search_payload(position),
        )
    except Exception:
        return None
    if not isinstance(response, dict) or response.get("error"):
        return None
    data = response.get("data", response)
    found = data.get("found") if isinstance(data, dict) else None
    if not isinstance(found, list) or not all(isinstance(value, dict) for value in found):
        return None
    requested = crystal_cage_search_payload(position)["center"]
    observed_center = data.get("center")
    complete_centered_scan = bool(
        data.get("complete") is True
        and data.get("dimension") == "minecraft:the_end"
        and isinstance(observed_center, dict)
        and all(
            isinstance(observed_center.get(axis), int)
            and observed_center[axis] == requested[axis]
            for axis in ("x", "y", "z")
        )
    )
    cx, cy, cz = (float(position[axis]) for axis in ("x", "y", "z"))
    bars = [
        (int(value["x"]), int(value["y"]), int(value["z"]))
        for value in found
        if all(isinstance(value.get(axis), int) for axis in ("x", "y", "z"))
        and abs(int(value["x"]) - cx) <= 2.0
        and abs(int(value["y"]) - cy) <= 3.0
        and abs(int(value["z"]) - cz) <= 2.0
    ]
    if not bars:
        return [] if complete_centered_scan else None
    # A genuine cage surrounds the crystal on at least two horizontal sides;
    # one unrelated bar must never authorize destructive mining.
    horizontal = {
        (int(math.copysign(1, value[0] - cx)) if value[0] != cx else 0,
         int(math.copysign(1, value[2] - cz)) if value[2] != cz else 0)
        for value in bars
    }
    if len(horizontal) < 2:
        return [] if complete_centered_scan else None
    player = state.get("block_position", state.get("position", {})) or {}
    if all(isinstance(player.get(axis), (int, float)) for axis in ("x", "y", "z")):
        bars.sort(
            key=lambda value: sum(
                (float(player[axis]) - value[index]) ** 2
                for index, axis in enumerate(("x", "y", "z"))
            )
        )
    return bars


def _open_crystal_cage(
    client: Any, bars: list[tuple[int, int, int]], crystal: dict, state: dict
) -> bool:
    """Verify one bar removal, then return to a verified safe standoff."""
    if not bars or not _fight_margin_ready(client, state, admission=False):
        return False
    anchor_data = state.get("block_position", state.get("position", {})) or {}
    if not all(
        isinstance(anchor_data.get(axis), (int, float)) for axis in ("x", "y", "z")
    ):
        return False
    anchor = tuple(int(round(float(anchor_data[axis]))) for axis in ("x", "y", "z"))
    target = bars[0]
    from . import navigation

    if not navigation.goto(client, *target, timeout=30, tolerance=4.0):
        return False
    refreshed = client.transport.dispatch("get_state", {})
    ensure_alive(client, refreshed)
    if not _fight_margin_ready(client, refreshed, admission=False):
        return False
    player = refreshed.get("block_position", refreshed.get("position", {})) or {}
    if not all(isinstance(player.get(axis), (int, float)) for axis in ("x", "y", "z")):
        return False
    distance = math.sqrt(
        sum(
            (float(player[axis]) - target[index]) ** 2
            for index, axis in enumerate(("x", "y", "z"))
        )
    )
    if distance > 5.0:
        return False
    client.transport.dispatch(
        "dig_block",
        {"x": target[0], "y": target[1], "z": target[2], "max_ticks": 200},
    )
    opened = False
    deadline = time.time() + 10.0
    try:
        while time.time() < deadline:
            block_id = _block_id(client, target)
            if block_id is None:
                return False
            if block_id != "minecraft:iron_bars":
                opened = True
                break
            current = client.transport.dispatch("get_state", {})
            ensure_alive(client, current)
            survival = _survival_values(current)
            if (
                not _is_survival_end_state(current)
                or survival is None
                or survival[0] < _CONTINUE_HEALTH
                or survival[1] < _CONTINUE_FOOD
            ):
                client.transport.dispatch("cancel", {})
                return False
            time.sleep(0.25)
        if not opened:
            return False
    finally:
        client.transport.dispatch("cancel", {})
    if not navigation.goto(client, *anchor, timeout=30, tolerance=3.0):
        return False
    returned = client.transport.dispatch("get_state", {})
    ensure_alive(client, returned)
    if not _fight_margin_ready(client, returned, admission=False):
        return False
    current = returned.get("block_position", returned.get("position", {})) or {}
    crystal_position = crystal.get("position") or {}
    if not all(
        isinstance(value.get(axis), (int, float))
        for value in (current, crystal_position)
        for axis in ("x", "y", "z")
    ):
        return False
    return math.sqrt(
        sum(
            (float(current[axis]) - float(crystal_position[axis])) ** 2
            for axis in ("x", "y", "z")
        )
    ) >= 12.0


def _attack_crystal(client: Any, crystal: dict, state: dict) -> tuple[bool, bool]:
    """Return (bounded operation consumed, verified crystal progress)."""
    if ranged_attack_in_flight(client):
        return False, False
    cage = _cage_blocks(client, crystal, state)
    if cage is None:
        return True, False
    if cage:
        _open_crystal_cage(client, cage, crystal, state)
        return True, False
    if fire_best_ranged_attack(client, crystal, allow_throwables=True):
        return True, _crystal_progress(client, crystal)
    position = crystal.get("position") or {}
    player = state.get("block_position", state.get("position", {})) or {}
    if not all(axis in position for axis in ("x", "y", "z")):
        return True, False
    # Do not pillar into a crystal explosion without ranged gear.
    if float(position["y"]) - float(player.get("y", position["y"])) > 6.0:
        return True, False
    if float(state.get("health", 20) or 0) < 18.0:
        return True, False
    from . import navigation

    if not navigation.goto(
        client,
        int(position["x"]),
        int(position["y"]),
        int(position["z"]),
        timeout=20,
        tolerance=3.0,
    ):
        return True, False
    refreshed = client.transport.dispatch("get_state", {})
    ensure_alive(client, refreshed)
    if not _fight_margin_ready(client, refreshed, admission=False):
        return True, False
    frame = _entity_frame(client.transport.dispatch("get_entities", {"radius": 12}))
    if frame is None or frame[1]:
        return True, False
    observed = frame[0]
    current = next(
        (entity for entity in observed if entity.get("id") == crystal.get("id")),
        None,
    )
    if current is None:
        return True, True
    attacked = bool(execute_melee_strike(client, current, refreshed).get("attacked"))
    return True, attacked and _crystal_progress(client, current)


def _attack_dragon(client: Any, dragon: dict, state: dict) -> bool:
    position = dragon.get("position") or {}
    if not all(axis in position for axis in ("x", "y", "z")):
        return False
    perched = (
        abs(float(position["x"])) < 12.0
        and abs(float(position["z"])) < 12.0
        and float(position["y"]) < 82.0
    )
    if not perched:
        return fire_best_ranged_attack(client, dragon)
    from . import navigation

    if not navigation.goto(client, 0, 64, 0, timeout=15, tolerance=3.0):
        return False
    refreshed = client.transport.dispatch("get_state", {})
    ensure_alive(client, refreshed)
    if not _fight_margin_ready(client, refreshed, admission=False):
        return False
    frame = _entity_frame(client.transport.dispatch("get_entities", {"radius": 32}))
    if frame is None or frame[1]:
        return False
    observed = frame[0]
    current = next(
        (entity for entity in observed if entity.get("id") == dragon.get("id")),
        None,
    )
    if current is None:
        return False
    return bool(execute_melee_strike(client, current, refreshed).get("attacked"))


def fight_ender_dragon(client: Any, timeout: int = 1200) -> bool:
    """Fight under shared survival, defense, loadout, and telemetry policy."""
    print("Beginning supervised Ender Dragon fight sequence...")
    recorder = combat_telemetry.get_combat_telemetry(client)
    owns_encounter = False
    crystal_operations: dict[int, int] = {}
    admitted = False
    incomplete_frames = 0
    deadline = time.time() + max(0, timeout)
    try:
        while time.time() < deadline:
            state = client.transport.dispatch("get_state", {})
            ensure_alive(client, state)
            if not _is_survival_end_state(state):
                client.transport.dispatch("cancel", {})
                return False
            entity_response = client.transport.dispatch(
                "get_entities", {"radius": 128}
            )
            frame = _entity_frame(entity_response)
            if frame is None:
                client.transport.dispatch("cancel", {})
                return False
            observed, skipped_count = frame
            if skipped_count:
                client.transport.dispatch("cancel", {})
                incomplete_frames += 1
                if incomplete_frames >= 3:
                    return False
                time.sleep(0.25)
                continue
            incomplete_frames = 0
            crystals = [
                entity
                for entity in observed
                if entity.get("type") == "minecraft:end_crystal"
            ]
            dragon = next(
                (
                    entity
                    for entity in observed
                    if entity.get("type") == "minecraft:ender_dragon"
                ),
                None,
            )
            clouds = [
                entity
                for entity in observed
                if entity.get("type") == "minecraft:area_effect_cloud"
            ]
            target = dragon or (crystals[0] if crystals else None)
            if target is not None and recorder.encounter_id is None:
                owns_encounter = recorder.begin_encounter(
                    source="fight_ender_dragon",
                    purpose="ender_dragon_fight",
                    player=state,
                    threats=observed,
                    target_id=target.get("id"),
                    target_metadata=target,
                )
            recorder.record_snapshot(player=state, threats=observed)

            if dragon is None and not crystals and _exit_portal_visible(client):
                if owns_encounter:
                    recorder.end_encounter(
                        outcome="target_killed",
                        reason="exit_portal_verified",
                        player=state,
                        threats=observed,
                    )
                return True
            if not _fight_margin_ready(client, state, admission=not admitted):
                if owns_encounter:
                    recorder.end_encounter(
                        outcome="disengaged",
                        reason="survival_margin",
                        player=state,
                        threats=observed,
                    )
                return False
            if not admitted and not _live_dragon_kit_ready(client):
                combat_telemetry.record_combat_action(
                    client, "dragon_kit", outcome="admission_refused"
                )
                if owns_encounter:
                    recorder.end_encounter(
                        outcome="disengaged",
                        reason="dragon_kit",
                        player=state,
                        threats=observed,
                    )
                return False
            admitted = True

            viable_crystals = [
                value
                for value in crystals
                if crystal_operations.get(int(value.get("id", -1)), 0)
                < _MAX_CRYSTAL_OPERATIONS
            ]
            if crystals and not viable_crystals:
                if owns_encounter:
                    recorder.end_encounter(
                        outcome="disengaged",
                        reason="crystal_attempts_exhausted",
                        player=state,
                        threats=observed,
                    )
                return False
            crystal = (
                min(
                    viable_crystals,
                    key=lambda value: (
                        crystal_operations.get(int(value.get("id", -1)), 0),
                        float(value.get("distance", 999)),
                    ),
                )
                if viable_crystals
                else None
            )
            with combat_intent(client, _fight_intent(dragon, crystal)):
                breath_escape = _escape_breath(client, clouds, state)
                if breath_escape is not None:
                    time.sleep(0.25)
                    continue
                # The intended boss is excluded; projectiles and secondary
                # hostiles still run through the canonical defense policy.
                if defend_or_flee(
                    client,
                    allow_safe_recovery_movement=True,
                    observed_snapshot={
                        "player": state,
                        "entities": observed,
                        "skipped_count": skipped_count,
                    },
                ):
                    time.sleep(0.25)
                    continue
                if crystal is not None:
                    consumed, attacked = _attack_crystal(client, crystal, state)
                    crystal_id = int(crystal.get("id", -1))
                    if consumed:
                        crystal_operations[crystal_id] = (
                            crystal_operations.get(crystal_id, 0) + 1
                        )
                else:
                    attacked = (
                        _attack_dragon(client, dragon, state)
                        if dragon is not None
                        else False
                    )
            time.sleep(0.25 if attacked else 0.75)
        return False
    finally:
        if owns_encounter and recorder.encounter_id is not None:
            recorder.end_encounter(
                outcome="interrupted",
                reason="dragon_fight_timeout",
            )


__all__ = [
    "dragon_action_state",
    "dragon_kit_provision_requirements",
    "dragon_kit_ready",
    "fight_ender_dragon",
]
