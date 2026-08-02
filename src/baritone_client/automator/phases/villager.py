"""Phase 8: verified villager breeding infrastructure."""

from ...common import TaskResult
from ...common.combat import entity_position, get_nearby_entities
from ...common.villager import build_villager_breeder, start_villager_multiplication
from ..phase_executor import PhaseHandler
from ..resource_manager import ResourceManager
from ..state_manager import Phase, StateManager


class VillagerInfraHandler(PhaseHandler):
    """Establish bed capacity and prove the population can reproduce."""

    def get_name(self) -> str:
        return "Villager Pipeline"

    def execute(
        self, client, resources: ResourceManager, state: StateManager
    ) -> TaskResult:
        missing = resources.check_phase_requirements(Phase.VILLAGER_INFRA)
        if missing:
            return TaskResult.fail(
                "Villager breeding supplies are missing",
                missing_requirements=missing,
            )

        try:
            villagers = [
                entity
                for entity in get_nearby_entities(client, 32, raise_on_error=True)
                if entity.get("type") == "minecraft:villager"
            ]
        except Exception as exc:
            return TaskResult.fail("Could not observe nearby villagers", error=str(exc))

        adults = [entity for entity in villagers if not bool(entity.get("is_baby"))]
        positions = [entity_position(entity) for entity in adults]
        positions = [position for position in positions if position is not None]
        if len(adults) < 2 or len(positions) < 2:
            return TaskResult.fail(
                "Two nearby adult villagers are required; the current bridge cannot safely prove villager transport",
                observed_adults=len(adults),
                capability_blocker="villager_transport",
            )

        anchor = tuple(
            round(sum(position[index] for position in positions) / len(positions))
            for index in range(3)
        )
        breeder = build_villager_breeder(
            client,
            *anchor,
            required_beds=len(villagers) + 1,
        )
        if not breeder or not breeder.get("verified"):
            return TaskResult.fail("Villager bed-capacity postcondition not observed")

        breeding = start_villager_multiplication(client)
        if not breeding or not breeding.get("offspring_observed"):
            return TaskResult.fail("New villager offspring was not observed")

        payload = {
            "breeder": breeder,
            "breeding": breeding,
            "adult_count_before": len(adults),
            "phase": Phase.VILLAGER_INFRA.name,
        }
        state.custom_data.setdefault("structures", {})["villager_breeder"] = breeder
        state.add_location(
            "villager_breeder", *anchor, dimension="overworld", tags=["verified"]
        )
        state.record_phase_payload(Phase.VILLAGER_INFRA, payload)
        state.save_checkpoint(resources.get_summary()["inventory"])
        return TaskResult.ok("Villager breeder verified by new offspring", **payload)
