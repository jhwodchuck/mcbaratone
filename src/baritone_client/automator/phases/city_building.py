"""Build a bounded, checkpointed city over the verified terraform area."""

from ..phase_executor import PhaseHandler
from ..resource_manager import ResourceManager
from ..state_manager import Phase, StateManager
from ...common import TaskResult
from ...common.city import build_ring


class CityBuildingHandler(PhaseHandler):
    """Builds a bounded number of district rings around the player/base."""

    def __init__(
        self,
        rings: int = 2,
        flatten: bool = False,
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
        terraform = state.get_phase_payload(Phase.TERRAFORM)
        center = terraform.get("center")
        if not isinstance(center, (list, tuple)) or len(center) != 2:
            return TaskResult.fail("Verified terraform center is missing")
        try:
            center_x, center_z = (int(value) for value in center)
            target_y = int(terraform["target_y"])
        except (KeyError, TypeError, ValueError) as exc:
            return TaskResult.fail(f"Terraform handoff is invalid: {exc}")

        city_progress = state.custom_data.setdefault("city_progress", {})
        # Ring zero contains the megabase beacon and storage handoff.  The
        # city expands around that protected civic core.
        start_ring = int(city_progress.get("ring", 1) or 1)
        districts_built = int(city_progress.get("districts_completed", 0) or 0)

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
            if not result.success:
                return result
            # A resumed ring reports only work performed in this invocation;
            # once it reaches the boundary, credit the ring's full size.
            districts_built += int(result.data.get("districts_total", 0) or 0)

            city_progress["ring"] = ring + 1
            city_progress["ring_progress"] = {}
            city_progress["districts_completed"] = districts_built

        if int(city_progress.get("ring", 0) or 0) != self.rings + 1:
            return TaskResult.fail("City build did not reach its bounded ring target")
        payload = {
            "verified_operations": True,
            "progress_complete": True,
            "rings_completed": self.rings + 1,
            "districts_completed": districts_built,
            "center": [center_x, center_z],
            "target_y": target_y,
        }
        state.record_phase_payload(Phase.CITY_BUILD, payload)
        return TaskResult.ok(
            f"City build-out complete through ring {self.rings}",
            **payload,
        )
