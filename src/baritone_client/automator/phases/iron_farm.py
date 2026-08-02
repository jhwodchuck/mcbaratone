"""Phase 7: verify an honestly constructed survival iron farm."""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Tuple

from ..phase_executor import PhaseHandler
from ..resource_manager import ResourceManager
from ..state_manager import Phase, StateManager
from ...common import TaskResult
from ...common.iron_farm import (
    add_zombie_to_farm,
    adult_villagers_near,
    build_iron_farm,
    construct_iron_farm,
    detect_farm_entities,
    detect_farm_structure,
    entities_near,
    get_nearby_entities,
    get_player_farm_position,
    move_villagers_to_farm,
    start_iron_production,
    transport_entity_to_farm,
)


def _position(value: Any) -> Tuple[int, int, int] | None:
    try:
        if isinstance(value, Mapping):
            return int(value["x"]), int(value["y"]), int(value["z"])
        if isinstance(value, (list, tuple)) and len(value) >= 3:
            return int(value[0]), int(value[1]), int(value[2])
    except (KeyError, TypeError, ValueError):
        return None
    return None


def _valid_payload(payload: Dict[str, Any]) -> bool:
    return (
        payload.get("verification_version") == 1
        and _position(payload.get("farm_location")) is not None
        and isinstance(payload.get("adult_villager_count"), int)
        and all(
            isinstance(payload.get(key), bool)
            for key in (
                "villagers_verified",
                "zombie_verified",
                "structure_verified",
                "production_verified",
            )
        )
    )


def _transport_population(
    client: Any,
    anchor: Tuple[int, int, int],
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Physically deliver three villagers and one zombie to the farm."""
    all_entities = get_nearby_entities(client, radius=64)
    present_villagers = [
        entity
        for entity in entities_near(all_entities, anchor, "minecraft:villager", 8)
        if not bool(entity.get("is_baby", False))
    ]
    present_zombies = entities_near(all_entities, anchor, "minecraft:zombie", 8)
    villager_sources = [
        entity
        for entity in all_entities
        if entity.get("type") == "minecraft:villager"
        and not bool(entity.get("is_baby", False))
        and entity not in present_villagers
    ]
    zombie_sources = [
        entity
        for entity in all_entities
        if entity.get("type") == "minecraft:zombie" and entity not in present_zombies
    ]
    villager_jobs = [
        entity
        for entity in villager_sources[: max(0, 3 - len(present_villagers))]
    ]
    jobs: List[Tuple[Dict[str, Any], bool]] = [
        (entity, True) for entity in villager_jobs
    ]
    if not present_zombies and zombie_sources:
        jobs.append((zombie_sources[0], False))
    missing_villagers = max(0, 3 - len(present_villagers) - len(villager_jobs))
    blockers = []
    if missing_villagers:
        blockers.append(f"{missing_villagers} adult villager transport source(s) unavailable")
    if not present_zombies and not zombie_sources:
        blockers.append("zombie transport source unavailable")

    evidence: List[Dict[str, Any]] = []
    destination = (anchor[0], anchor[1] + 1, anchor[2])
    for entity, release_at_destination in jobs:
        success, details, reason = transport_entity_to_farm(
            client,
            entity,
            destination,
            vehicle_type="boat",
            release_at_destination=release_at_destination,
        )
        details["success"] = success
        details["reason"] = reason
        evidence.append(details)
        if not success:
            blockers.append(reason)
    return evidence, blockers


class IronFarmHandler(PhaseHandler):
    """Verify live farm evidence without administrative entity/world mutations."""

    def get_name(self) -> str:
        return "Iron Farm (Hour 6-7)"

    def execute(
        self,
        client: Any,
        resources: ResourceManager,
        state: StateManager,
    ) -> TaskResult:
        # Inventory counts cannot establish that an iron farm exists, so this
        # phase intentionally does not use ResourceManager.phase_ready_result.
        previous = state.get_phase_payload(Phase.IRON_FARM)
        anchor = _position(previous.get("farm_location"))
        if anchor is None:
            try:
                anchor = get_player_farm_position(client)
            except (TypeError, ValueError, KeyError) as exc:
                return TaskResult.fail(f"Could not establish iron-farm anchor: {exc}")

        witnesses = previous.get("structure_witnesses")
        if not isinstance(witnesses, Mapping):
            witnesses = {}

        try:
            structure_verified = detect_farm_structure(client, anchor, witnesses)
            construction_reason = "existing farm structure verified"
            if not structure_verified:
                structure_verified, built_witnesses, construction_reason = construct_iron_farm(
                    client,
                    anchor,
                )
                witnesses = built_witnesses
            transport_evidence, transport_blockers = _transport_population(client, anchor)
            villagers = adult_villagers_near(client, anchor)
            entities = detect_farm_entities(client, anchor, limit=16)
        except Exception as exc:
            return TaskResult.fail(f"Could not capture iron-farm evidence: {exc}")

        payload: Dict[str, Any] = {
            "verification_version": 1,
            "farm_location": list(anchor),
            "structure_witnesses": dict(witnesses),
            "construction_reason": construction_reason,
            "entity_transports": transport_evidence,
            "adult_villager_count": len(villagers),
            "entity_counts": entities,
            "villagers_verified": len(villagers) >= 3,
            "zombie_verified": entities.get("minecraft:zombie", 0) >= 1,
            "structure_verified": bool(structure_verified),
            "production_verified": entities.get("minecraft:iron_golem", 0) >= 1,
        }
        if not _valid_payload(payload):
            return TaskResult.fail("Iron-farm evidence payload validation failed")

        missing = []
        missing.extend(transport_blockers)
        if not payload["villagers_verified"]:
            missing.append(
                "three transported adult villagers were not independently observed at the farm"
            )
        if not payload["zombie_verified"]:
            missing.append(
                "the transported zombie was not independently observed at the farm"
            )
        if not payload["structure_verified"]:
            missing.append(
                construction_reason
            )
        if not payload["production_verified"]:
            missing.append("no farm-adjacent iron golem is visible as production evidence")

        if missing:
            payload["implementation_blocker"] = "; ".join(missing)
            state.record_phase_payload(Phase.IRON_FARM, payload)
            return TaskResult.fail(payload["implementation_blocker"], **payload)

        payload["implementation_blocker"] = None
        state.record_phase_payload(Phase.IRON_FARM, payload)
        return TaskResult.ok("Existing survival iron farm verified", **payload)

    # Compatibility hooks remain observational and fail closed.  Older callers
    # may still invoke them, but they never issue invented bridge commands.
    def _move_villagers(self, client: Any, farm_location: Tuple[int, int, int]) -> bool:
        return move_villagers_to_farm(client, [], farm_location)

    def _add_zombie(self, client: Any, farm_location: Tuple[int, int, int]) -> bool:
        return add_zombie_to_farm(client, farm_location)

    def _build_iron_farm(self, client: Any, farm_location: Tuple[int, int, int]) -> bool:
        return build_iron_farm(client, *farm_location)

    def _start_production(self, client: Any, farm_location: Tuple[int, int, int]) -> bool:
        return start_iron_production(client, farm_location)
