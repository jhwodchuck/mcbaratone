"""
Phase 4: Nether Logic
"""

from ..phase_executor import PhaseHandler
from ..resource_manager import ResourceManager
from ..state_manager import Phase, StateManager
from ...common import TaskResult, SequentialTask, ActionTask
from ...common.resources import ensure_supplies, _read_state_with_retry
from ...common.inventory import _ensure_raw_planks
from ...common.inventory import count_item
from ...common.nether import (
    build_nether_portal,
    enter_portal,
    find_nearest_portal,
    find_nether_fortress,
    hunt_blazes,
    ignite_portal,
    verify_portal,
)

class NetherAndBlazeHandler(PhaseHandler):
    """Phase 4: Nether exploration - Hour 3-4."""
    
    def get_name(self) -> str:
        return "Nether Phase (Hour 3-4)"
    
    def execute(self, client, resources: ResourceManager, state: StateManager) -> TaskResult:
        tasks = [
            ActionTask(
                "Build, ignite, and verify Nether portal",
                lambda c: self._prepare_portal(c, state),
            ),
            ActionTask("Enter Nether", lambda c: self._enter_nether(c, state)),
            ActionTask(
                "Locate and persist fortress",
                lambda c: self._locate_fortress(c, state),
            ),
            ActionTask("Kill blazes -> 6+ rods", self._hunt_blazes),
            ActionTask(
                "Return to Overworld",
                lambda c: self._return_to_overworld(c, state),
            ),
        ]

        executor = SequentialTask("Nether Phase", tasks)
        result = executor.run(client)
        if not result.success:
            return result
        rods = count_item(client, "minecraft:blaze_rod")
        payload = {
            "blaze_rods": rods,
            "portal_pair": state.get_locations("nether_portal").get(
                "nether_portal", []
            ),
            "fortress": state.get_locations("nether_fortress").get(
                "nether_fortress", []
            ),
        }
        state.record_phase_payload(Phase.NETHER_AND_BLAZE, payload)
        return TaskResult.ok("Nether portal and blaze supply verified", **payload)

    @staticmethod
    def _current_dimension(client) -> str:
        snapshot = client.transport.dispatch("get_state", {})
        return str(snapshot.get("dimension", "")).lower()

    @staticmethod
    def _persisted_portal(state: StateManager, dimension: str):
        locations = state.get_locations("nether_portal").get("nether_portal", [])
        for location in locations:
            if dimension in str(location.get("dimension", "")).lower():
                return (
                    int(location["x"]),
                    int(location["y"]),
                    int(location["z"]),
                )
        return None

    def _prepare_portal(self, client, state: StateManager) -> bool:
        """Build, ignite, verify, and persist the Overworld portal."""
        if "nether" in self._current_dimension(client):
            return True
        persisted = self._persisted_portal(state, "overworld")
        if persisted and verify_portal(client, persisted, require_active=True):
            return True

        if not _ensure_raw_planks(client, 4):
            print(
                "  Could not prepare planks for portal support; proceeding with "
                "minimal-material fallback."
            )
        materials_ready = ensure_supplies(
            client,
            {"minecraft:obsidian": 14, "minecraft:flint_and_steel": 1},
            timeout=180,
        )
        if not materials_ready.success:
            print("  Could not gather portal materials; cannot build nether portal.")
            return False

        # Get current position
        snapshot, _ = _read_state_with_retry(
            client, retries=2, label="Portal build state read"
        )
        if snapshot is None:
            print(
                "  Portal position read timed out; using last-known safe origin "
                "(0, 64, 0) for this attempt."
            )
            snapshot = {"block_position": {"x": 0, "y": 64, "z": 0}}
        pos = snapshot.get("block_position", snapshot.get("position", {}))
        px = int(pos.get("x", 0))
        py = int(pos.get("y", 64))
        pz = int(pos.get("z", 0))

        # Try nearby offsets if the first construction attempt is blocked.
        candidate_offsets = (
            (3, 0),
            (3, 2),
            (0, 3),
            (-3, 0),
            (0, -2),
            (0, 2),
        )
        for offset_x, offset_z in candidate_offsets:
            x = px + int(offset_x)
            z = pz + int(offset_z)
            print(f"  Attempting portal frame at ({x}, {py}, {z})")
            portal = (x, py, z)
            if build_nether_portal(client, *portal) and ignite_portal(client, portal):
                state.add_location(
                    "nether_portal",
                    x,
                    py,
                    z,
                    dimension="overworld",
                    tags=["active", "entry"],
                )
                return True
        print("  All portal placement attempts failed; will retry later.")
        return False

    def _enter_nether(self, client, state: StateManager) -> bool:
        if "nether" in self._current_dimension(client):
            return True
        portal = self._persisted_portal(state, "overworld")
        if portal is None:
            return False
        if not enter_portal(
            client,
            portal,
            target_dimension="minecraft:the_nether",
            timeout=60,
        ):
            return False
        nether_portal = find_nearest_portal(client, "the_nether")
        if nether_portal is None:
            return False
        state.add_location(
            "nether_portal",
            *nether_portal,
            dimension="the_nether",
            tags=["active", "return"],
        )
        return True

    def _locate_fortress(self, client, state: StateManager) -> bool:
        known = state.get_locations("nether_fortress").get("nether_fortress", [])
        if known:
            fortress = known[0]
            client.transport.dispatch(
                "goto",
                {
                    "x": int(fortress["x"]),
                    "y": int(fortress["y"]),
                    "z": int(fortress["z"]),
                    "radius": 8,
                },
            )
            return True
        fortress = find_nether_fortress(client)
        if fortress is None:
            return False
        state.add_location(
            "nether_fortress",
            *fortress,
            dimension="the_nether",
            tags=["verified", "blaze_source"],
        )
        return True

    @staticmethod
    def _hunt_blazes(client) -> bool:
        return hunt_blazes(client, target_count=6) >= 6

    def _return_to_overworld(self, client, state: StateManager) -> bool:
        """Return through portal to Overworld."""
        if "overworld" in self._current_dimension(client):
            return True
        portal = self._persisted_portal(state, "the_nether")
        portal = portal or find_nearest_portal(client, "the_nether")
        if portal is None:
            return False
        return enter_portal(
            client,
            portal,
            target_dimension="minecraft:overworld",
            timeout=60,
        )
