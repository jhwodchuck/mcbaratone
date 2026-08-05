"""Terrain-screened, displacement-verified combat escape."""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

from . import combat_telemetry
from .defense import (
    AttackStyle,
    EscapeCandidate,
    ThreatAssessment,
    assess_threats,
    plan_escape_candidates,
)

_HAZARDS = (
    "lava",
    "fire",
    "cactus",
    "magma_block",
    "campfire",
    "pointed_dripstone",
    "sweet_berry_bush",
)
_NON_GROUND = ("air", "water", "lava", "cave_air", "void_air")
_PASSABLE = ("air", "grass", "fern", "flower", "snow", "vine")


def _block_id(client: Any, x: int, y: int, z: int) -> Optional[str]:
    try:
        result = client.transport.dispatch(
            "get_block", {"x": x, "y": y, "z": z}
        )
    except Exception:
        return None
    if not isinstance(result, dict):
        return None
    block = result.get("id", result.get("block"))
    return str(block).lower() if block else None


def destination_safe(client: Any, x: int, y: int, z: int) -> bool:
    """Reject hazards, blocked headroom, and unsupported endpoints."""
    feet = _block_id(client, x, y, z)
    head = _block_id(client, x, y + 1, z)
    below = _block_id(client, x, y - 1, z)
    known = tuple(value for value in (feet, head, below) if value)
    if any(token in block for block in known for token in _HAZARDS):
        return False
    if feet and not any(token in feet for token in _PASSABLE):
        return False
    if head and not any(token in head for token in _PASSABLE):
        return False
    if below and any(token in below for token in _NON_GROUND):
        return False
    return True


def surface_adjusted_candidates(
    client: Any,
    planned: list[EscapeCandidate],
    *,
    radius: int = 32,
) -> list[EscapeCandidate]:
    """Project flee directions onto nearby exposed natural terrain."""
    try:
        response = client.transport.dispatch(
            "find_blocks",
            {
                "blocks": [
                    "minecraft:grass_block",
                    "minecraft:dirt",
                    "minecraft:coarse_dirt",
                    "minecraft:podzol",
                ],
                "radius": min(16, max(4, int(radius))),
                "limit": 512,
            },
            timeout=2.0,
        )
    except Exception:
        return []
    ranked = []
    for block in response.get("found", []):
        position = (
            int(block["x"]),
            int(block["y"]) + 1,
            int(block["z"]),
        )
        distance = min(
            (position[0] - candidate.x) ** 2
            + (position[2] - candidate.z) ** 2
            + (position[1] - candidate.y) ** 2
            for candidate in planned
        )
        nearest_direction = max(
            planned,
            key=lambda candidate: candidate.direction_score
            - 0.01
            * (
                (position[0] - candidate.x) ** 2
                + (position[2] - candidate.z) ** 2
            ),
        )
        ranked.append(
            (
                distance,
                EscapeCandidate(
                    *position,
                    direction_score=nearest_direction.direction_score,
                ),
            )
        )
    ranked.sort(key=lambda item: item[0] - item[1].direction_score * 10)
    safe = []
    seen = set()
    for _, candidate in ranked[:64]:
        position = (candidate.x, candidate.y, candidate.z)
        if position in seen:
            continue
        seen.add(position)
        if destination_safe(client, *position):
            safe.append(candidate)
            if len(safe) >= 8:
                break
    return safe


def separation_from(entity: Dict, player_position: Dict) -> float:
    """Measure horizontal separation from a threat."""
    from . import combat as api

    position = api.entity_position(entity)
    if position is None:
        return float(entity.get("distance", 0) or 0)
    try:
        return (
            (float(position[0]) - float(player_position.get("x", 0))) ** 2
            + (float(position[2]) - float(player_position.get("z", 0))) ** 2
        ) ** 0.5
    except (TypeError, ValueError):
        return float(entity.get("distance", 0) or 0)


def verify_escape(
    client: Any,
    threat_id: Optional[int],
    initial_distance: float,
    *,
    timeout: float,
    minimum_gain: float,
) -> bool:
    """Confirm that an escape command really increases separation."""
    from . import combat as api

    deadline = time.monotonic() + max(0.5, timeout)
    while time.monotonic() < deadline:
        api.ensure_alive(client)
        try:
            entities = api.get_nearby_entities(
                client, 40, raise_on_error=True
            )
        except api.EntityQueryError:
            time.sleep(0.5)
            continue
        target = next(
            (entity for entity in entities if entity.get("id") == threat_id),
            None,
        )
        if target is None:
            return True
        state = client.transport.dispatch("get_state", {})
        position = state.get("block_position", state.get("position", {})) or {}
        if separation_from(target, position) >= max(
            14.0, initial_distance + minimum_gain
        ):
            return True
        time.sleep(0.5)
    return False


def relocate_away_from(
    client: Any,
    threat: Dict,
    *,
    distance: int = 28,
    timeout: int = 30,
) -> bool:
    """Walk away from a non-combat threat and verify separation gained."""
    from . import combat as api

    try:
        state = client.transport.dispatch("get_state", {})
    except Exception:
        return False
    position = state.get("block_position", state.get("position", {})) or {}
    threat_position = api.entity_position(threat)
    if threat_position is None or not position:
        return False
    px = float(position.get("x", 0) or 0)
    pz = float(position.get("z", 0) or 0)
    threat_x = float(threat_position[0])
    threat_z = float(threat_position[2])
    dx = px - threat_x
    dz = pz - threat_z
    initial_separation = (dx * dx + dz * dz) ** 0.5
    if initial_separation < 0.5:
        return False

    target_x = int(px + dx / initial_separation * distance)
    target_z = int(pz + dz / initial_separation * distance)
    print(
        f"DEFENSE: relocating {distance} blocks away from "
        f"{threat.get('type')} toward ({target_x}, {target_z})"
    )
    client.transport.dispatch(
        "chat", {"message": f"#goto {target_x} {target_z}"}
    )
    required_gain = max(8.0, distance * 0.5)
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(1.0)
        try:
            current = client.transport.dispatch("get_state", {})
        except Exception:
            break
        here = current.get(
            "block_position", current.get("position", {})
        ) or {}
        if not here:
            continue
        separation = (
            (float(here.get("x", px) or px) - threat_x) ** 2
            + (float(here.get("z", pz) or pz) - threat_z) ** 2
        ) ** 0.5
        if separation - initial_separation >= required_gain:
            api._stop_for_defense(client)
            print(
                f"DEFENSE: relocated to {separation:.1f}m from the threat"
            )
            return True
    api._stop_for_defense(client)
    return False


@combat_telemetry.trace_escape
def run_away(
    client: Any,
    threat: Dict,
    *,
    timeout: float = 6.0,
    minimum_gain: float = 5.0,
) -> bool:
    """Choose a safe endpoint and prove movement away from a threat."""
    from . import combat as api

    client._last_escape_failure_reason = None
    try:
        state = client.transport.dispatch("get_state", {})
        position = state.get("block_position", state.get("position", {})) or {}
        try:
            nearby = api.scan_for_threats(
                client,
                radius=16,
                raise_on_error=True,
                player_state=state,
            )
        except api.EntityQueryError:
            nearby = [threat]
        if not any(item.get("id") == threat.get("id") for item in nearby):
            nearby.append(threat)
        assessments = assess_threats(nearby, state) or [
            ThreatAssessment(
                entity=threat,
                entity_type=str(threat.get("type", "unknown")),
                distance=float(threat.get("distance", 0) or 0),
                closing_speed=0.0,
                score=1.0,
                style=AttackStyle.MELEE,
                always_evade=True,
            )
        ]
        initial = separation_from(threat, position)
        candidates = plan_escape_candidates(position, assessments)
        safe = [
            candidate
            for candidate in candidates[:8]
            if destination_safe(client, candidate.x, candidate.y, candidate.z)
        ]
        if not safe:
            safe = surface_adjusted_candidates(client, candidates)
        combat_telemetry.record_combat_action(
            client,
            "escape_plan",
            outcome="ready" if safe else "no_safe_endpoint",
            candidate_count=len(candidates),
            safe_candidate_count=len(safe),
        )
        if not safe:
            print("FLEE: no terrain-safe escape endpoint found")
            client._last_escape_failure_reason = "no_safe_endpoint"
            return False
        per_candidate = max(1.5, timeout / len(safe))
        for candidate in safe:
            destination = {
                "x": candidate.x,
                "y": candidate.y,
                "z": candidate.z,
            }
            combat_telemetry.record_combat_action(
                client,
                "escape_route",
                outcome="attempted",
                destination=destination,
            )
            print(
                f"FLEE: pathing to {candidate.x},{candidate.y},{candidate.z}; "
                f"initial separation {initial:.1f}m"
            )
            client.transport.dispatch(
                "goal",
                {"x": candidate.x, "y": candidate.y, "z": candidate.z},
            )
            client.transport.dispatch("chat", {"message": "#path"})
            if verify_escape(
                client,
                threat.get("id"),
                initial,
                timeout=per_candidate,
                minimum_gain=minimum_gain,
            ):
                combat_telemetry.record_combat_action(
                    client,
                    "escape_route",
                    outcome="verified",
                    destination=destination,
                )
                print("FLEE: separation verified")
                return True
            client.transport.dispatch("cancel", {})
            combat_telemetry.record_combat_action(
                client,
                "escape_route",
                outcome="failed",
                destination=destination,
            )
        print("FLEE: candidate routes did not increase separation")
        client._last_escape_failure_reason = "no_separation_gain"
        return False
    except api.PlayerDeathDetected:
        raise
    except Exception as exc:
        client._last_escape_failure_reason = "error"
        print(f"Run away failed: {exc}")
        return False
