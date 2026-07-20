"""
City Building Phase - Build out the terraformed world into a megabase city.

NOT YET WIRED IN (same rationale as phases/terraforming.py): this handler
follows the PhaseHandler contract but is deliberately not registered in
`phases/__init__.py`, `state_manager.Phase`, or
`automator.EndGameAutomator.register_default_handlers`, because those three
files are shared/hot during active bridge and live-bot work.

To integrate later:
    1. Add `CITY_BUILD = auto()` to the `Phase` enum in state_manager.py
       (after TERRAFORM, before `COMPLETE`).
    2. Export `CityBuildingHandler` from `phases/__init__.py`.
    3. In `EndGameAutomator.register_default_handlers`, add:
       `self.register_handler(Phase.CITY_BUILD, CityBuildingHandler())`

Until then, use the standalone runner `city_builder_forever.py`, which does
the same job unbounded and resumably - the better fit, since "build out the
world into cities" has no natural end state.

See plans/CITY_BUILD_PLAN.md for the full design.
"""

from ..phase_executor import PhaseHandler
from ..resource_manager import ResourceManager
from ..state_manager import StateManager
from ...common import TaskResult
from ...common.city import build_ring


class CityBuildingHandler(PhaseHandler):
    """Builds a bounded number of district rings around the player/base."""

    def __init__(
        self,
        rings: int = 2,
        flatten: bool = True,
        with_roads: bool = True,
        with_rail: bool = False,
    ):
        self.rings = rings
        self.flatten = flatten
        self.with_roads = with_roads
        self.with_rail = with_rail

    def get_name(self) -> str:
        return "City Build-Out"

    def execute(self, client, resources: ResourceManager, state: StateManager) -> TaskResult:
        try:
            world_state = client.transport.dispatch("get_state", {})
            pos = world_state.get("block_position", world_state.get("position", {}))
            center_x = int(pos.get("x", 0))
            center_z = int(pos.get("z", 0))
            target_y = int(pos.get("y", 64))
        except Exception as exc:
            return TaskResult.fail(f"Unable to read player position: {exc}")

        city_progress = state.custom_data.setdefault("city_progress", {})
        start_ring = city_progress.get("ring", 0)
        districts_built = 0

        for ring in range(start_ring, self.rings + 1):
            ring_progress = city_progress.setdefault("ring_progress", {})

            def on_district_done(done, total, data):
                print(f"  [City] ring {ring}: district {done}/{total} "
                      f"role={data.get('role')} origin={data.get('origin')}")

            result = build_ring(
                client,
                center_x, center_z, target_y,
                ring=ring,
                flatten=self.flatten,
                with_roads=self.with_roads,
                with_rail=self.with_rail,
                progress=ring_progress,
                on_district_done=on_district_done,
            )
            districts_built += result.data.get("districts", 0)

            city_progress["ring"] = ring + 1
            city_progress["ring_progress"] = {}

        return TaskResult.ok(
            f"City build-out complete through ring {self.rings}",
            rings=self.rings,
            districts=districts_built,
            center=(center_x, target_y, center_z),
        )
