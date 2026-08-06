"""
Phase 9: End Game Logic
"""

from ..phase_executor import PhaseHandler
from ..resource_manager import ResourceManager
from ..state_manager import Phase, StateManager
from ...common import TaskResult, SequentialTask, ActionTask
from ...common.resources import ensure_supplies
from ...common.inventory import (
    count_item,
    withdraw_required_from_catalog,
)
from ...common.navigation import goto
from ...common.enderman_hunt import hunt_endermen
from ...common.end import (
    acquire_elytra,
    acquire_shulker_boxes,
    activate_end_portal,
    enter_end_portal,
    fight_ender_dragon,
    find_end_city,
    find_end_portal,
    return_from_end,
    traverse_end_gateway,
    triangulate_stronghold,
)

class WorldUnlockHandler(PhaseHandler):
    """Phase 9: End dimension access - Hour 8-9."""
    
    def get_name(self) -> str:
        return "End Unlock (Hour 8-9)"
    
    def execute(self, client, resources: ResourceManager, state: StateManager) -> TaskResult:
        tasks = [
            ActionTask("Craft Eyes of Ender", lambda c: self._craft_eyes(c, state, resources)),
            ActionTask("Locate stronghold", lambda c: self._locate_stronghold(c, state, resources)),
            ActionTask("Find and activate End portal", lambda c: self._activate_portal(c, state, resources)),
            ActionTask("Enter The End", lambda c: self._enter_end(c, state, resources)),
            ActionTask("Kill Ender Dragon (one-cycle)", lambda c: self._kill_dragon(c, state, resources)),
            ActionTask("Loot End City", lambda c: self._loot_end_city(c, state, resources)),
            ActionTask("Acquire 5+ shulker boxes", lambda c: self._acquire_shulkers(c, state, resources)),
            ActionTask("Grab Elytra", lambda c: self._grab_elytra(c, state, resources)),
            ActionTask("Return to Overworld", lambda c: self._return_overworld(c, state, resources)),
        ]
        
        executor = SequentialTask("End Unlock", tasks)
        return executor.run(client)

    @staticmethod
    def _checkpoint(state: StateManager, resources: ResourceManager) -> None:
        resources.refresh_inventory()
        state.save_checkpoint(resources.get_summary()["inventory"])

    def _craft_eyes(
        self, client, state: StateManager, resources: ResourceManager
    ) -> bool:
        """Acquire missing pearls, then craft 12 Eyes of Ender.

        The old phase asked the generic recipe loop to craft Eyes for ten
        minutes even when no pearls existed.  Pearl acquisition was stranded
        in an unregistered legacy handler, so the production objective graph
        had no executable path from blaze rods to the End.
        """
        eyes = count_item(client, "minecraft:ender_eye")
        if eyes >= 12:
            return self._record_eyes_ready(state, resources, 12)

        needed = 12 - eyes
        bank_requirements = {"minecraft:ender_pearl": needed}
        carried_powder = count_item(client, "minecraft:blaze_powder")
        rods_needed = max(0, (needed - carried_powder + 1) // 2)
        if rods_needed:
            bank_requirements["minecraft:blaze_rod"] = rods_needed
        withdraw_required_from_catalog(
            client,
            bank_requirements,
            state=state,
            max_containers=3,
            max_travel_distance=96.0,
            max_vertical_distance=32.0,
        )
        pearls = count_item(client, "minecraft:ender_pearl")
        if pearls < needed:
            # Rearm BEFORE hunting, never after dying. A completed earlier
            # phase proves historical progress, not the gear the bot is
            # actually wearing after a death, and the hunt itself now refuses
            # to provoke an Enderman while naked. Without this the refusal
            # would simply stall the phase at 0 pearls forever.
            if not self._ensure_combat_readiness(client, state):
                print(
                    "  Pearl hunting deferred until armor, food, and health "
                    "are restored."
                )
                return False
            print(f"  Acquiring Ender pearls for Eyes ({pearls}/{needed}).")
            pearls = hunt_endermen(client, target_count=needed, timeout=1200)
        if pearls < needed:
            print(f"  Eye crafting blocked: Ender pearls remain {pearls}/{needed}.")
            return False

        powder = count_item(client, "minecraft:blaze_powder")
        rods = count_item(client, "minecraft:blaze_rod")
        if powder + rods * 2 < needed:
            print(
                "  Eye crafting blocked: blaze supply cannot produce "
                f"{needed} powder ({powder} powder, {rods} rods)."
            )
            return False
        if not ensure_supplies(
            client,
            {"minecraft:blaze_powder": needed},
            timeout=120,
        ).success:
            return False
        if not ensure_supplies(
            client,
            {"minecraft:ender_eye": 12},
            timeout=120,
        ).success:
            return False
        return self._record_eyes_ready(state, resources, 12)

    @staticmethod
    def _ensure_combat_readiness(client, state: StateManager) -> bool:
        """Restore armor, weapon, food, and health before hostile overworld work.

        ``NETHER_AND_BLAZE`` already refuses to enter a fortress naked. The
        same protection never existed for ``WORLD_UNLOCK``, so a bot that died
        and respawned with 0/4 armor walked straight back into the cave
        hostiles that had just killed it. Reuse the proven rearm rather than
        writing a second one; its only Nether-specific branch is a no-op in
        the Overworld.
        """
        from .nether_prep import NetherAndBlazeHandler

        try:
            return bool(
                NetherAndBlazeHandler()._ensure_nether_readiness(client, state)
            )
        except Exception as exc:  # pragma: no cover - defensive
            print(f"  Combat readiness check failed: {exc}")
            return False

    def _record_eyes_ready(
        self,
        state: StateManager,
        resources: ResourceManager,
        count: int,
    ) -> bool:
        payload = state.get_phase_payload(Phase.WORLD_UNLOCK)
        payload["eyes_ready"] = int(count)
        state.record_phase_payload(Phase.WORLD_UNLOCK, payload)
        self._checkpoint(state, resources)
        return True

    def _locate_stronghold(
        self, client, state: StateManager, resources: ResourceManager
    ) -> bool:
        """Triangulate and durably persist the stronghold estimate."""
        existing = state.custom_data.get("stronghold_coords")
        if isinstance(existing, (list, tuple)) and len(existing) == 2:
            return True
        coords = triangulate_stronghold(client)
        if coords is None:
            return False
        x, z = (int(value) for value in coords)
        snapshot = client.transport.dispatch("get_state", {})
        position = snapshot.get("block_position", snapshot.get("position", {}))
        y = int(position.get("y", 64))
        state.custom_data["stronghold_coords"] = [x, z]
        state.add_location(
            "stronghold", x, y, z, dimension="overworld", tags=["triangulated"]
        )
        payload = state.get_phase_payload(Phase.WORLD_UNLOCK)
        payload["stronghold_coords"] = [x, z]
        state.record_phase_payload(Phase.WORLD_UNLOCK, payload)
        self._checkpoint(state, resources)
        return True

    def _activate_portal(
        self, client, state: StateManager, resources: ResourceManager
    ) -> bool:
        """Find and activate the End portal."""
        stronghold = state.custom_data.get("stronghold_coords")
        if isinstance(stronghold, (list, tuple)) and len(stronghold) == 2:
            snapshot = client.transport.dispatch("get_state", {})
            position = snapshot.get("block_position", snapshot.get("position", {}))
            if not goto(
                client,
                int(stronghold[0]),
                int(position.get("y", 64)),
                int(stronghold[1]),
                timeout=900,
                tolerance=16.0,
            ):
                return False
        # Let Baritone's structure goal refine the triangulated surface
        # estimate. Completion is still decided solely by the live frame scan.
        try:
            client.mission.macro("locate_stronghold", {})
        except Exception as exc:
            print(f"  Stronghold macro unavailable; continuing local scan: {exc}")
        portal = find_end_portal(client)
        if portal is None:
            return False
        px, py, pz = (int(value) for value in portal)
        state.custom_data["end_portal"] = [px, py, pz]
        state.add_location(
            "end_portal", px, py, pz, dimension="overworld", tags=["portal_room"]
        )
        if not activate_end_portal(client):
            return False
        self._checkpoint(state, resources)
        return True

    def _enter_end(
        self, client, state: StateManager, resources: ResourceManager
    ) -> bool:
        portal = state.custom_data.get("end_portal")
        if not enter_end_portal(client, portal=portal):
            return False
        state.custom_data.setdefault("milestones", {})["end_entered"] = True
        payload = state.get_phase_payload(Phase.WORLD_UNLOCK)
        payload["end_entered"] = True
        state.record_phase_payload(Phase.WORLD_UNLOCK, payload)
        self._checkpoint(state, resources)
        return True

    def _kill_dragon(
        self, client, state: StateManager, resources: ResourceManager
    ) -> bool:
        """Kill the Ender Dragon, attempting one-cycle strategy."""
        print("Fighting Ender Dragon...")
        if not fight_ender_dragon(client):
            return False
        state.custom_data.setdefault("milestones", {})["dragon_defeated"] = True
        payload = state.get_phase_payload(Phase.WORLD_UNLOCK)
        payload["dragon_defeated"] = True
        state.record_phase_payload(Phase.WORLD_UNLOCK, payload)
        self._checkpoint(state, resources)
        return True

    def _loot_end_city(
        self, client, state: StateManager, resources: ResourceManager
    ) -> bool:
        """Find and loot an End City."""
        print("Searching for End City...")
        existing = state.custom_data.get("end_city")
        if isinstance(existing, (list, tuple)) and len(existing) == 3:
            return goto(client, *existing, timeout=900, tolerance=24.0)
        gateway = traverse_end_gateway(client)
        if gateway is None:
            return False
        arrival_state = client.transport.dispatch("get_state", {})
        arrival = arrival_state.get(
            "block_position", arrival_state.get("position", {})
        )
        if not all(axis in arrival for axis in ("x", "y", "z")):
            return False
        arrival_coords = [
            int(arrival["x"]), int(arrival["y"]), int(arrival["z"])
        ]
        state.custom_data["outer_end_gateway"] = arrival_coords
        state.add_location(
            "end_gateway",
            *arrival_coords,
            dimension="the_end",
            tags=["outer_islands", "return"],
        )
        city = find_end_city(client)
        if city is None:
            return False
        state.custom_data["end_city"] = list(city)
        state.add_location(
            "end_city", *city, dimension="the_end", tags=["purpur_verified"]
        )
        self._checkpoint(state, resources)
        return goto(client, *city, timeout=900, tolerance=24.0)

    def _acquire_shulkers(
        self, client, state: StateManager, resources: ResourceManager
    ) -> bool:
        """Kill shulkers and craft 5+ shulker boxes."""
        print("Acquiring shulker boxes...")
        if not acquire_shulker_boxes(client, target=5):
            return False
        self._checkpoint(state, resources)
        return True

    def _grab_elytra(
        self, client, state: StateManager, resources: ResourceManager
    ) -> bool:
        """Find and grab Elytra from End Ship."""
        print("Searching for Elytra...")
        if not acquire_elytra(client):
            return False
        self._checkpoint(state, resources)
        return True

    def _return_overworld(
        self, client, state: StateManager, resources: ResourceManager
    ) -> bool:
        """Return to Overworld via End gateway or portal."""
        print("Returning to Overworld...")
        snapshot = client.transport.dispatch("get_state", {})
        if "overworld" in str(snapshot.get("dimension", "")).lower():
            return True
        arrival = state.custom_data.get("outer_end_gateway")
        if not isinstance(arrival, (list, tuple)) or len(arrival) != 3:
            return False
        if not goto(client, *arrival, timeout=1200, tolerance=12.0):
            return False
        if traverse_end_gateway(client) is None:
            return False
        if not return_from_end(client):
            return False
        state.custom_data.setdefault("milestones", {})[
            "returned_from_end"
        ] = True
        self._checkpoint(state, resources)
        return True
