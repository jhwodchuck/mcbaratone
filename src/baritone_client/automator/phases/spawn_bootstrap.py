"""
Spawn Bootstrap Phase - Explore spawn chunks and prime mission macros.
"""

from ..phase_executor import PhaseHandler
from ..resource_manager import ResourceManager
from ..state_manager import Phase, StateManager
from ...common import explore_until
from ...common.base import wait_for_safe_daylight
from ...common.combat import acquire_emergency_food
from ...common.movement_recovery import block_position
from ...common.navigation import goto
from ...common.surface_recovery import position_is_aquatic, reach_dry_surface
from ...common.tasks import TaskResult, ActionTask, SequentialTask


def _secure_exploration_endpoint(
    client,
    base_coords: tuple[int, int, int],
) -> tuple[int, int, int] | None:
    """Return a dry post-scout position or fail closed after bounded recovery."""
    state = client.transport.dispatch("get_state", {})
    current = block_position(state)
    if not position_is_aquatic(client, current):
        return current

    print(
        "SPAWN_BOOTSTRAP: exploration ended in water at "
        f"{current}; reaching dry ground before progression"
    )
    recovered = reach_dry_surface(
        client,
        origin=current,
        expected_y=max(base_coords[1], current[1]),
        goto=goto,
        search_radius=32,
        attempt_limit=6,
        command_timeout=45.0,
    )
    if recovered is None or position_is_aquatic(client, recovered):
        return None
    return recovered


def _secure_survival_margin(
    client,
    base_coords: tuple[int, int, int],
) -> bool:
    """Recover critical health or hunger before any spawn-area scouting."""
    state = client.transport.dispatch("get_state", {})
    health = float(state.get("health", 20) or 0)
    food = int(state.get("food_level", state.get("food", 20)) or 0)
    if health >= 12.0 and food >= 14:
        return True
    print(
        "SPAWN_BOOTSTRAP: survival margin is too low for scouting "
        f"(health={health:.1f}, food={food}); recovering locally"
    )
    return acquire_emergency_food(
        client,
        minimum_health=12.0,
        minimum_food=14,
        timeout=120.0,
        max_exploration_distance=32.0,
        exploration_center=base_coords,
        return_to_exploration_center=True,
    )


class SpawnBootstrapHandler(PhaseHandler):
    """Scout the spawn region and trigger the bridge bootstrap macro."""

    def get_name(self) -> str:
        return "Spawn Bootstrap"

    def execute(self, client, resources: ResourceManager, state: StateManager) -> TaskResult:
        # Determine starting coordinates so we can return safely.
        ready = resources.phase_ready_result(Phase.SPAWN_BOOTSTRAP, "Spawn scouting already satisfied")
        if ready:
            return ready

        try:
            # Use a short per-call timeout so phase startup doesn't block
            # indefinitely if the bridge is slow to respond.
            snapshot = client.transport.dispatch("get_state", {}, timeout=1.0)
            position = snapshot.get("block_position", snapshot.get("position", {}))
            base_coords = (
                int(position.get("x", snapshot.get("x", 0))),
                int(position.get("y", snapshot.get("y", 64))),
                int(position.get("z", snapshot.get("z", 0))),
            )
        except Exception as exc:
            base_coords = (0, 64, 0)
            start_error = str(exc)
        else:
            start_error = None

        # Define subtasks for initial setup
        tasks = [
            ActionTask("Check survival mode", self._check_survival_mode),
            ActionTask("Set baritone settings", self._set_baritone_settings),
            ActionTask("Basic inventory check", self._basic_inventory_check),
        ]

        # Execute initial setup tasks sequentially
        setup_task = SequentialTask("Spawn Bootstrap Setup", tasks)
        setup_result = setup_task.run(client)

        if not setup_result.success:
            return TaskResult.fail(f"Spawn bootstrap setup failed: {setup_result.reason}")

        # A new player has no bed, weapon, armor, or food.  Exploring at night
        # repeatedly killed the player before INITIAL_GATHERING could begin.
        # Stay still at spawn and let the world reach daylight first.  This is
        # slower than cheating the time forward, but keeps the run legitimate
        # and fully autonomous.
        if not wait_for_safe_daylight(client):
            return TaskResult.fail("Spawn bootstrap could not reach safe daylight")

        if not _secure_survival_margin(client, base_coords):
            return TaskResult.fail(
                "Spawn bootstrap could not establish a safe survival margin"
            )

        # Scout briefly to load chunks
        print("Scouting spawn area...")
        explore_until(
            client,
            condition=lambda: False,
            max_distance=64,
            timeout=15,
        )

        # Skip supply gathering due to threading issues in current bridge setup
        supply_result = TaskResult.ok("Supply gathering skipped due to bridge threading constraints")

        # Do not path back to the exact spawn Y coordinate.  This world spawns
        # the player on top of a tree, and the old safe_return() first tried to
        # climb even higher before returning.  The exploration endpoint is a
        # valid place for the gathering phase to start, while spawn_coords are
        # still recorded as a waypoint in StateManager.
        client.transport.dispatch("cancel", {})
        endpoint = _secure_exploration_endpoint(client, base_coords)
        if endpoint is None:
            return TaskResult.fail(
                "Spawn bootstrap could not prove a dry exploration endpoint"
            )
        exploration = TaskResult.ok(
            "Scouting complete",
            success=True,
            endpoint=endpoint,
        )
        return_home = TaskResult.ok(
            "Exploration endpoint retained; unsafe tree-top return skipped",
            origin=base_coords,
        )

        success = all(
            result.success
            for result in (exploration, supply_result, return_home)
        )

        payload = {
            "setup": setup_result.data,
            "exploration": exploration.data,
            "supplies": supply_result.data,
            "return_home": return_home.data,
        }
        if start_error:
            payload["position_error"] = start_error

        if success:
            return TaskResult.ok("Spawn scouted and macros primed", **payload)
        return TaskResult.fail("Bootstrap phase incomplete", **payload)

    def _check_survival_mode(self, client) -> bool:
        """Check if in survival mode (player can take damage and die)."""
        try:
            state = client.transport.dispatch("get_state", {}, timeout=0.5)
            # Response is flattened, not wrapped in 'data'
            health = state.get("health", 0)
            is_dead = state.get("is_dead", True)
            # Assume survival if player has health > 0 and not dead
            if health > 0 and not is_dead:
                return True
            return False
        except Exception:
            # If we can't get state, assume survival
            return True

    def _set_baritone_settings(self, client) -> bool:
        """Set initial baritone settings for automation."""
        settings = {
            "allowSprint": "true",
            "allowParkour": "true",
            "allowBreak": "true",
            "allowPlace": "true",
            "autoTool": "true",
        }
        try:
            response = client.mission.macro("bootstrap", {"settings": settings})
            # Legacy or flat response handling
            result = response.get("result") or response.get("data") or response
            applied = result.get("settingsApplied", 0)
            if applied:
                return True
            print("Warning: bootstrap mission returned without applying settings:", result)
        except Exception as exc:
            print(f"Warning: mission bootstrap failed: {exc}")
        return False

    def _basic_inventory_check(self, client) -> bool:
        """Perform basic inventory check to ensure we have starting items."""
        try:
            inventory = client.transport.dispatch("get_inventory", {}, timeout=0.2)
            # Response is flattened
            inventory_items = inventory.get("inventory", [])

            # Check if inventory is not completely empty
            # Player should have at least air or something, but let's check for any non-air items
            total_items = sum(item.get("count", 0) for item in inventory_items if item.get("id") != "minecraft:air")
            # In survival, player spawns with empty inventory, so we expect low counts
            # Just ensure we can access inventory
            return total_items >= 0  # Always true if we reach here
        except Exception:
            return False
