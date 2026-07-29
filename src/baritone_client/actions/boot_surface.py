"""Surface gating for boot activities that deliberately explore."""

from __future__ import annotations

from ..common.build_site_recovery import excavate_surface_egress
from ..common.movement_recovery import block_position
from ..common.navigation import goto
from ..common.surface_recovery import reach_dry_surface
from ..core.interfaces import ActionContext, ActionResult
from .base import BaseAction
from .boot_readiness import require_survival_margin


MINIMUM_EXPLORATION_Y = 55
EXPECTED_SURFACE_Y = 63


class BootSurfaceSafetyAction(BaseAction):
    """Reach dry surface terrain before optional sheep or scouting searches."""

    def execute(self, context: ActionContext) -> ActionResult:
        state = self.run_command(context, "get_state", {})
        position = block_position(state)
        dimension = state.get("dimension", "minecraft:overworld")
        if dimension != "minecraft:overworld" or position[1] >= MINIMUM_EXPLORATION_Y:
            return ActionResult.ok("Boot exploration already has surface access")

        require_survival_margin(context.client)
        print(
            "BOOT SURFACE: underground exploration is unsafe; "
            f"ascending from y={position[1]}"
        )
        recovered = reach_dry_surface(
            context.client,
            origin=position,
            expected_y=EXPECTED_SURFACE_Y,
            goto=goto,
        )
        if recovered is None:
            recovered = excavate_surface_egress(
                context.client,
                origin=position,
                expected_y=EXPECTED_SURFACE_Y,
            )
        if recovered is None or recovered[1] < MINIMUM_EXPLORATION_Y:
            return ActionResult.fail(
                "Could not reach safe surface terrain before boot exploration"
            )

        require_survival_margin(context.client)
        return ActionResult.ok(f"Reached boot exploration surface at {recovered}")
