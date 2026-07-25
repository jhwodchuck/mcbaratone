"""
Phase 10: Megabase Logic
"""

from ..phase_executor import PhaseHandler
from ..resource_manager import ResourceManager
from ..state_manager import Phase, StateManager
from ...common import ActionTask, SequentialTask, TaskResult, harness_ops
from ...common.inventory import count_item
from ...common.navigation import goto


SHULKER_BOX_IDS = ("minecraft:shulker_box",) + tuple(
    f"minecraft:{color}_shulker_box"
    for color in (
        "white", "orange", "magenta", "light_blue", "yellow", "lime",
        "pink", "gray", "light_gray", "cyan", "purple", "blue", "brown",
        "green", "red", "black",
    )
)
BEACON_BASE_BLOCKS = (
    "minecraft:iron_block",
    "minecraft:gold_block",
    "minecraft:emerald_block",
    "minecraft:diamond_block",
    "minecraft:netherite_block",
)

class MegabaseInitHandler(PhaseHandler):
    """Phase 10: Megabase initialization - Hour 9-10."""
    
    def get_name(self) -> str:
        return "Megabase Init (Hour 9-10)"
    
    def execute(self, client, resources: ResourceManager, state: StateManager) -> TaskResult:
        tasks = [
            ActionTask("Verify post-End shulker loadout", self._pack_shulkers),
            ActionTask(
                "Select and persist megabase site",
                lambda c: self._select_location(c, state),
            ),
            ActionTask(
                "Persist resumable terraform plan",
                lambda c: self._begin_excavation(c, state),
            ),
            ActionTask(
                "Place and verify beacon",
                lambda c: self._place_beacon(c, state),
            ),
        ]
        result = SequentialTask("Megabase Init", tasks).run(client)
        if not result.success:
            return result
        resources.refresh_inventory()
        state.save_checkpoint(resources.get_summary()["inventory"])
        return TaskResult.ok(
            "Megabase handoff verified",
            megabase_location=state.custom_data.get("megabase_location"),
            terraform_plan=state.custom_data.get("terraform_plan"),
        )

    def _pack_shulkers(self, client) -> bool:
        """Organize and pack all resources into shulker boxes."""
        print("Packing resources into shulker boxes...")
        return sum(count_item(client, item_id) for item_id in SHULKER_BOX_IDS) >= 5

    def _select_location(self, client, state: StateManager) -> bool:
        """Scout and select final megabase location."""
        print("Selecting megabase location...")
        existing = state.custom_data.get("megabase_location")
        if isinstance(existing, (list, tuple)) and len(existing) == 3:
            return True
        snapshot = client.transport.dispatch("get_state", {})
        position = snapshot.get("block_position", snapshot.get("position", {}))
        if not all(axis in position for axis in ("x", "y", "z")):
            return False
        # Chunk-center alignment makes the terraform ring deterministic and
        # avoids a plan whose footprint shifts after resume.
        x = (int(position["x"]) // 16) * 16 + 8
        z = (int(position["z"]) // 16) * 16 + 8
        y = int(position["y"])
        state.custom_data["megabase_location"] = [x, y, z]
        state.add_location(
            "megabase", x, y, z, dimension="overworld", tags=["terraform_origin"]
        )
        return True

    @staticmethod
    def _block_id(client, position) -> str:
        response = client.transport.dispatch(
            "get_block",
            {"x": int(position[0]), "y": int(position[1]), "z": int(position[2])},
        )
        return str(response.get("id") or response.get("block") or "")

    def _place_beacon(self, client, state: StateManager) -> bool:
        """Place beacon foundation (iron/diamond/emerald/gold blocks)."""
        print("Placing beacon foundation...")
        location = state.custom_data.get("megabase_location")
        if not isinstance(location, (list, tuple)) or len(location) != 3:
            return False
        x, y, z = (int(value) for value in location)
        beacon_pos = (x, y + 1, z)
        base_positions = [
            (x + dx, y, z + dz)
            for dx in (-1, 0, 1)
            for dz in (-1, 0, 1)
        ]
        if self._block_id(client, beacon_pos) == "minecraft:beacon" and all(
            self._block_id(client, position) in BEACON_BASE_BLOCKS
            for position in base_positions
        ):
            state.custom_data.setdefault("structures", {})["beacon"] = {
                "location": list(beacon_pos),
                "verified": True,
            }
            return True
        if count_item(client, "minecraft:beacon") < 1:
            return False
        if sum(count_item(client, item_id) for item_id in BEACON_BASE_BLOCKS) < 9:
            return False
        if not goto(client, x, y, z, timeout=300, tolerance=4.0):
            return False
        for position in base_positions:
            if self._block_id(client, position) in BEACON_BASE_BLOCKS:
                continue
            block_id = next(
                (
                    item_id for item_id in BEACON_BASE_BLOCKS
                    if count_item(client, item_id) > 0
                ),
                None,
            )
            if block_id is None or not harness_ops.place_block_exact(
                client, *position, block_id, allow_break=True
            ):
                return False
        if not harness_ops.place_block_exact(
            client, *beacon_pos, "minecraft:beacon", allow_break=True
        ):
            return False
        if self._block_id(client, beacon_pos) != "minecraft:beacon" or any(
            self._block_id(client, position) not in BEACON_BASE_BLOCKS
            for position in base_positions
        ):
            return False
        state.custom_data.setdefault("structures", {})["beacon"] = {
            "location": list(beacon_pos),
            "verified": True,
        }
        return True

    def _begin_excavation(self, client, state: StateManager) -> bool:
        """Begin vertical excavation shaft for megabase."""
        print("Beginning excavation protocol...")
        location = state.custom_data.get("megabase_location")
        if not isinstance(location, (list, tuple)) or len(location) != 3:
            return False
        x, y, z = (int(value) for value in location)
        plan = {
            "version": 1,
            "kind": "terraform_rings",
            "center": [x, z],
            "target_y": y,
            "radius_chunks": 4,
            "next_ring": 0,
            "completed_chunks": [],
            "preserve": ["beacon", "end_portal", "nether_portal", "storage"],
        }
        state.custom_data["terraform_plan"] = plan
        state.custom_data.setdefault("terraform", {})["plan"] = plan
        payload = state.get_phase_payload(Phase.MEGABASE_INIT)
        payload["terraform_plan"] = plan
        state.record_phase_payload(Phase.MEGABASE_INIT, payload)
        return True
