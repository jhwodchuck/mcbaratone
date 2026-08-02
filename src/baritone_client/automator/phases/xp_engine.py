"""Phase 9: verified spawner-backed XP engine."""

from ...common import TaskResult
from ...common.mob_farm import find_spawner, grind_xp_at_location
from ..phase_executor import PhaseHandler
from ..resource_manager import ResourceManager
from ..state_manager import Phase, StateManager


class XpEngineHandler(PhaseHandler):
    """Locate a real spawner and prove it can support level-30 recovery."""

    def get_name(self) -> str:
        return "XP Engine"

    def execute(
        self, client, resources: ResourceManager, state: StateManager
    ) -> TaskResult:
        missing = resources.check_phase_requirements(Phase.XP_ENGINE)
        if missing:
            return TaskResult.fail(
                "XP combat supplies are missing",
                missing_requirements=missing,
            )

        source = find_spawner(client, max_distance=128)
        if source is None:
            return TaskResult.fail(
                "No loaded spawner was found; the current bridge cannot safely build and verify a generic mob farm",
                capability_blocker="generic_mob_farm_construction",
            )

        grind = grind_xp_at_location(client, *source, target_level=30)
        if (
            not grind
            or int(grind.get("achieved_level", 0) or 0) < 30
            or int(grind.get("encounters", 0) or 0) < 1
            or int(grind.get("xp_gained", 0) or 0) < 1
        ):
            return TaskResult.fail(
                "Spawner XP postcondition did not reach level 30",
                spawner_location=list(source),
            )

        farm = {
            "location": list(source),
            "source": "minecraft:spawner",
            "verified": True,
        }
        payload = {"farm": farm, "grind": grind, "phase": Phase.XP_ENGINE.name}
        state.custom_data.setdefault("structures", {})["xp_engine"] = farm
        state.add_location(
            "xp_engine",
            *source,
            dimension="overworld",
            tags=["spawner", "verified"],
        )
        state.record_phase_payload(Phase.XP_ENGINE, payload)
        state.save_checkpoint(resources.get_summary()["inventory"])
        return TaskResult.ok("Spawner XP engine verified at level 30", **payload)
