"""
Terraforming Phase - Flatten and reshape the world after the main mission.

NOT YET WIRED IN: this handler follows the same PhaseHandler contract as the
others in this package (see phase_executor.PhaseHandler), but it is
deliberately not registered in `phases/__init__.py`, `state_manager.Phase`,
or `automator.EndGameAutomator.register_default_handlers`. Those three files
are shared/hot during active bridge/live-bot work, so wiring this in was left
as a small, explicit step rather than done silently here:

    1. Add `TERRAFORM = auto()` to the `Phase` enum in state_manager.py
       (right before `COMPLETE`).
    2. Export `TerraformingHandler` from `phases/__init__.py` (same pattern as
       the other handlers).
    3. In `EndGameAutomator.register_default_handlers`, add:
       `self.register_handler(Phase.TERRAFORM, TerraformingHandler())`

Until then, this can be used standalone (see terraform_forever.py at the repo
root), which does the same job as an unattended, ever-expanding job that
doesn't require touching the Phase enum at all - the more natural fit, since
"terraform the world" has no real finish line.
"""

from ..phase_executor import PhaseHandler
from ..resource_manager import ResourceManager
from ..state_manager import StateManager
from ...common import TaskResult
from ...common.terraform import terraform_area


class TerraformingHandler(PhaseHandler):
    """Flattens a bounded area around the player/base to a target height."""

    def __init__(self, radius_chunks: int = 12, fill_block: str = "minecraft:stone"):
        self.radius_chunks = radius_chunks
        self.fill_block = fill_block

    def get_name(self) -> str:
        return "Terraforming"

    def execute(self, client, resources: ResourceManager, state: StateManager) -> TaskResult:
        try:
            world_state = client.transport.dispatch("get_state", {})
            pos = world_state.get("block_position", world_state.get("position", {}))
            center_x = int(pos.get("x", 0))
            center_z = int(pos.get("z", 0))
            target_y = int(pos.get("y", 64))
        except Exception as exc:
            return TaskResult.fail(f"Unable to read player position: {exc}")

        progress = state.custom_data.setdefault("terraform_progress", {})

        def on_chunk_done(done, total, chunk_coord):
            print(f"  [Terraforming] chunk {done}/{total} -> {chunk_coord}")

        result = terraform_area(
            client,
            center_x, center_z, target_y,
            radius_chunks=self.radius_chunks,
            fill_block=self.fill_block,
            progress=progress,
            on_chunk_done=on_chunk_done,
        )
        return result
