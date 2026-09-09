"""Phase 8: verified villager breeding infrastructure."""

from ...common import TaskResult
from ...common.combat import entity_position, get_nearby_entities
from ...common.farming import harvest_wheat_farm
from ...common.inventory import count_item, withdraw_required_from_catalog
from ...common.navigation import goto, staged_goto
from ...common.resources import ensure_supplies
from ...common.villager import (
    build_villager_breeder,
    locate_village,
    start_villager_multiplication,
)
from ..food_recovery_state import checkpointed_wheat_farm_origin
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
        if missing.get("minecraft:bread", 0) and self._provision_breeding_bread(
            client, state
        ):
            missing.pop("minecraft:bread", None)
        if missing:
            return TaskResult.fail(
                "Villager breeding supplies are missing",
                missing_requirements=missing,
            )

        try:
            villagers, adults, positions = self._observe_nearby_adult_villagers(client)
        except Exception as exc:
            return TaskResult.fail("Could not observe nearby villagers", error=str(exc))

        if len(adults) < 2 or len(positions) < 2:
            if not self._travel_to_a_located_village(client, state):
                return TaskResult.fail(
                    "Two nearby adult villagers are required and no village could be located",
                    observed_adults=len(adults),
                    capability_blocker="villager_transport",
                )
            try:
                villagers, adults, positions = self._observe_nearby_adult_villagers(client)
            except Exception as exc:
                return TaskResult.fail(
                    "Could not observe nearby villagers after reaching a village",
                    error=str(exc),
                )
            if len(adults) < 2 or len(positions) < 2:
                return TaskResult.fail(
                    "Village travel did not surface two adult villagers; "
                    "the current bridge cannot safely prove villager transport"
                    " to relocate more",
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

    @staticmethod
    def _observe_nearby_adult_villagers(client):
        """Return (all_villagers, adults, positions) observed within 32 blocks."""
        villagers = [
            entity
            for entity in get_nearby_entities(client, 32, raise_on_error=True)
            if entity.get("type") == "minecraft:villager"
        ]
        adults = [entity for entity in villagers if not bool(entity.get("is_baby"))]
        positions = [entity_position(entity) for entity in adults]
        positions = [position for position in positions if position is not None]
        return villagers, adults, positions

    @staticmethod
    def _travel_to_a_located_village(client, state: StateManager) -> bool:
        """Reach a village with two adult villagers, without ever moving them.

        This never captures or transports a villager -- capture_villager
        fails closed because the bridge cannot yet prove boat/minecart
        passenger capture is safe. Instead it brings the bot to villagers
        that are already where the world generator put them, which is
        exactly the population VillagerInfraHandler already knows how to
        breed once they are within its 32-block observation radius.

        Previously used only ``known[0]`` -- the first village ever
        recorded -- and never looked past it, even after arriving to find
        its population had wandered out of observation range or otherwise
        come up short. Every later attempt then retried that exact same
        stale coordinate forever, no matter how many other villages sat
        within easy reach. Tries every known village, nearest first, before
        spending time on a fresh wandering scan.
        """
        try:
            live = client.transport.dispatch("get_state", {})
        except Exception:
            live = {}
        position = live.get("block_position", live.get("position", {})) if isinstance(live, dict) else {}
        try:
            current = (
                int(position.get("x", 0)),
                int(position.get("y", 64)),
                int(position.get("z", 0)),
            )
        except (TypeError, ValueError):
            current = (0, 64, 0)

        def distance_sq(entry) -> float:
            try:
                return (int(entry["x"]) - current[0]) ** 2 + (
                    int(entry["z"]) - current[2]
                ) ** 2
            except (KeyError, TypeError, ValueError):
                return float("inf")

        known = state.get_locations("village").get("village", [])
        candidates = []
        for entry in sorted(known, key=distance_sq):
            try:
                candidates.append(
                    (int(entry["x"]), int(entry["y"]), int(entry["z"]))
                )
            except (KeyError, TypeError, ValueError):
                continue

        def try_candidate(target) -> bool:
            nonlocal current
            horizontal = (
                (current[0] - target[0]) ** 2 + (current[2] - target[2]) ** 2
            ) ** 0.5
            reached = (
                staged_goto(client, target, current)
                if horizontal > 48.0
                else goto(client, *target, timeout=120, tolerance=8.0)
            )
            if not reached:
                return False
            try:
                _, adults, positions = (
                    VillagerInfraHandler._observe_nearby_adult_villagers(client)
                )
            except Exception:
                return False
            if len(adults) < 2 or len(positions) < 2:
                try:
                    fresh_state = client.transport.dispatch("get_state", {})
                    fresh_pos = fresh_state.get(
                        "block_position", fresh_state.get("position", {})
                    )
                    current = (
                        int(fresh_pos.get("x", target[0])),
                        int(fresh_pos.get("y", target[1])),
                        int(fresh_pos.get("z", target[2])),
                    )
                except Exception:
                    current = target
                return False
            return True

        for target in candidates:
            if try_candidate(target):
                return True

        target = locate_village(client)
        if target is None:
            return False
        state.add_location(
            "village", *target, dimension="overworld", tags=["verified"]
        )
        return try_candidate(target)

    @staticmethod
    def _provision_breeding_bread(client, state: StateManager) -> bool:
        """Withdraw or harvest enough wheat, then craft six verified bread."""
        target = 6
        try:
            withdraw_required_from_catalog(
                client,
                {
                    "minecraft:bread": target,
                    "minecraft:wheat": target * 3,
                },
                state=state,
                max_travel_distance=96.0,
                max_vertical_distance=32.0,
            )
        except Exception:
            pass
        if count_item(client, "minecraft:bread") >= target:
            return True

        wheat_target = target * 3
        if count_item(client, "minecraft:wheat") < wheat_target:
            origin = checkpointed_wheat_farm_origin(state)
            if origin is None:
                print("  No checkpointed wheat farm can supply villager bread.")
                return False
            farm_position = origin
            live = client.transport.dispatch("get_state", {})
            position = live.get("block_position", live.get("position", {}))
            current = (
                int(position.get("x", 0)),
                int(position.get("y", 64)),
                int(position.get("z", 0)),
            )
            horizontal = (
                (current[0] - farm_position[0]) ** 2
                + (current[2] - farm_position[2]) ** 2
            ) ** 0.5
            if horizontal > 48.0 and not staged_goto(
                client, farm_position, current
            ):
                print("  Could not reach the checkpointed wheat farm.")
                return False
            harvest_wheat_farm(client, *farm_position)
        if count_item(client, "minecraft:wheat") < wheat_target:
            print(
                "  Wheat farm is not yet mature enough for six breeding bread."
            )
            return False
        crafted = ensure_supplies(
            client, {"minecraft:bread": target}, timeout=180
        )
        return crafted.success and count_item(client, "minecraft:bread") >= target
