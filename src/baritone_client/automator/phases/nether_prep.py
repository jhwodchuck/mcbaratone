"""
Nether Prep Phase - Obsidian and portal materials.
"""

import time

from ..phase_executor import PhaseHandler
from ..resource_manager import ResourceManager
from ..state_manager import Phase, StateManager
from ...common import build_nether_portal, craft
from ...common.tasks import TaskResult, SequentialTask, ActionTask
from ...common.inventory import count_item


def mine_obsidian(client, count: int = 14, timeout: int = 300) -> bool:
    """Mine obsidian until count reached."""
    try:
        client.transport.dispatch("mine", {"blocks": ["minecraft:obsidian"], "quantity": count + 2})
        start = time.time()
        while time.time() - start < timeout:
            total = count_item(client, "minecraft:obsidian")
            if total >= count:
                client.transport.dispatch("cancel", {})
                return True
            time.sleep(5)
        client.transport.dispatch("cancel", {})
        return False
    except Exception as exc:
        print(f"Obsidian mining error: {exc}")
        return False


def craft_flint_steel(client) -> bool:
    """Craft flint and steel."""
    return craft(client, "minecraft:flint_and_steel", 1)


def build_portal(client) -> bool:
    """Build nether portal."""
    snapshot = client.transport.dispatch("get_state", {})
    pos = snapshot.get("block_position", snapshot.get("position", {}))
    x = int(pos.get("x", snapshot.get("x", 0))) + 5
    y = int(pos.get("y", snapshot.get("y", 64)))
    z = int(pos.get("z", snapshot.get("z", 0)))
    success = build_nether_portal(client, x, y, z)
    if success:
        # Store portal position in client or state if needed, but for now just return success
        pass
    return success


class NetherPrepHandler(PhaseHandler):
    """Handler for nether preparation phase."""
    
    def get_name(self) -> str:
        return "Nether Preparation"
    
    def execute(self, client, resources: ResourceManager, state: StateManager) -> TaskResult:
        """
        1. Ensure we have obsidian (14+)
        2. Craft flint and steel
        3. Build nether portal
        """
        ready = resources.phase_ready_result(Phase.NETHER_PREP, "Nether prep already satisfied")
        if ready:
            return ready

        # Define subtasks
        tasks = [
            ActionTask("Mine obsidian", mine_obsidian, count=14),
            ActionTask("Craft flint and steel", craft_flint_steel),
            ActionTask("Build nether portal", build_portal),
        ]

        # Execute tasks sequentially
        sequential_task = SequentialTask("Nether Prep", tasks)
        result = sequential_task.run(client)

        if result.success:
            resources.refresh_inventory()
            summary = resources.get_summary()
            return TaskResult.ok("Nether prep complete", inventory=summary["inventory"])
        else:
            missing = resources.check_phase_requirements(Phase.NETHER_PREP)
            return TaskResult.fail(f"Nether prep failed: {result.reason}", missing=missing)
