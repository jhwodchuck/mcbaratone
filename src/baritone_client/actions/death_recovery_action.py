"""
Death recovery action implementation.
"""

import time
from typing import Dict, Optional, Tuple
from ..core.interfaces import ActionContext, ActionResult
from ..core.exceptions import TransportError
from ..actions.base import BaseAction
from ..common.nether import find_nearest_portal
from ..common import goto
from ..common.combat import secure_recovery_area
from ..common.inventory import get_inventory, reset_inventory_cache
from ..automator.state_manager import Phase
from .death_recovery_state import handle_alive_pending_recovery


_CRITICAL_RECOVERY_IDS = {
    "minecraft:bucket",
    "minecraft:water_bucket",
    "minecraft:lava_bucket",
    "minecraft:crafting_table",
    "minecraft:furnace",
    "minecraft:blast_furnace",
    "minecraft:chest",
    "minecraft:flint_and_steel",
    "minecraft:obsidian",
    "minecraft:ender_pearl",
    "minecraft:ender_eye",
    "minecraft:blaze_rod",
    "minecraft:blaze_powder",
}
_CRITICAL_RECOVERY_TOKENS = (
    "diamond",
    "netherite",
    "raw_iron",
    "iron_ingot",
    "raw_gold",
    "gold_ingot",
    "pickaxe",
    "_axe",
    "sword",
    "shovel",
    "_hoe",
    "helmet",
    "chestplate",
    "leggings",
    "boots",
)


def _critical_inventory(inventory: Dict[str, int]) -> Dict[str, int]:
    """Keep the pre-death counts whose loss must stop autonomous play."""
    return {
        item_id: count
        for item_id, count in inventory.items()
        if count > 0
        and (
            item_id in _CRITICAL_RECOVERY_IDS
            or any(token in item_id for token in _CRITICAL_RECOVERY_TOKENS)
        )
    }


def _drop_priority(entity: Dict) -> Tuple[int, float]:
    name = entity.get("name", "").lower().replace(" ", "_")
    if any(token in name for token in ("pickaxe", "axe", "sword", "shovel", "hoe")):
        rank = 0
    elif any(token in name for token in ("crafting_table", "furnace", "chest", "bed")):
        rank = 1
    elif any(token in name for token in ("iron", "diamond", "gold", "obsidian")):
        rank = 2
    else:
        rank = 3
    return rank, float(entity.get("distance", 999))


def _death_drops(client, death_coords: Tuple[int, int, int], radius: float = 8.0):
    """Return only item entities close enough to belong to this death pile."""
    response = client.transport.dispatch("get_entities", {"radius": 32})
    drops = []
    dx, dy, dz = death_coords
    for entity in response.get("entities", []):
        if entity.get("type") != "minecraft:item":
            continue
        pos = entity.get("position", {})
        if not all(axis in pos for axis in ("x", "y", "z")):
            continue
        distance = (
            (float(pos["x"]) - dx) ** 2
            + (float(pos["y"]) - dy) ** 2
            + (float(pos["z"]) - dz) ** 2
        ) ** 0.5
        if distance <= radius:
            drops.append(entity)
    return sorted(drops, key=_drop_priority)


def _sweep_death_drops(client, death_coords: Tuple[int, int, int]) -> bool:
    """Walk across the bounded death pile and prove that it is empty."""
    for _pass in range(3):
        drops = _death_drops(client, death_coords)
        if not drops:
            return True
        for drop in drops:
            pos = drop.get("position", {})
            print(f"Recovery sweep: {drop.get('name', 'item')} at {pos}")
            goto(
                client,
                int(round(pos["x"])),
                int(round(pos["y"])),
                int(round(pos["z"])),
                timeout=12,
                tolerance=1.25,
            )
            time.sleep(0.5)
    return not _death_drops(client, death_coords)


def _recovery_shortfall(client, expected: Dict[str, int]) -> Dict[str, int]:
    recovered = get_inventory(client)
    return {
        item_id: count - recovered.get(item_id, 0)
        for item_id, count in expected.items()
        if recovered.get(item_id, 0) < count
    }


def _player_alive(client) -> bool:
    """Require a fresh live postcondition before recovery may resume a phase."""
    try:
        state = client.transport.dispatch("get_state", {})
    except Exception:
        return False
    return not state.get("is_dead", False) and float(state.get("health", 0) or 0) > 0


def _bootstrap_starter_pickaxe(client) -> bool:
    """Re-craft a minimum wooden pickaxe after a lost grave.

    A death drops the whole toolkit.  When the grave despawns (5-minute timer)
    or is unreachable, returning ``fail`` here leaves the bot permanently naked
    -- no pickaxe means it cannot mine, so its phase loops forever re-attempting
    stone gathering with nothing in hand.  The wooden-tool craft chain already
    gathers wood -> planks -> sticks from scratch, so driving it restores the
    one tool progression cannot proceed without.  Returns True if a pickaxe is
    now carried (already-had counts as success).
    """
    from ..common.inventory import count_item
    from ..common.resources import PICKAXE_ITEMS, ensure_supplies

    try:
        if any(count_item(client, item_id) > 0 for item_id in PICKAXE_ITEMS):
            return True
        print("RECOVERY: grave lost; bootstrapping a wooden pickaxe from scratch...")
        result = ensure_supplies(
            client, {"minecraft:wooden_pickaxe": 1}, timeout=300
        )
        succeeded = bool(getattr(result, "success", False)) and any(
            count_item(client, item_id) > 0 for item_id in PICKAXE_ITEMS
        )
        print(
            "RECOVERY: starter pickaxe bootstrap "
            f"{'succeeded' if succeeded else 'failed'}"
        )
        return succeeded
    except Exception as exc:  # never let recovery raise out of a fallback
        print(f"RECOVERY: starter pickaxe bootstrap error: {exc}")
        return False


def _checkpointed_retreat(state) -> Optional[Tuple[int, int, int]]:
    """Return the best persisted home coordinate without bridge probing."""
    custom_data = getattr(state, "custom_data", {})
    # Live catalog evidence is stronger than an old structure sketch. Bot10's
    # house chest was air while the location catalog held a verified chest;
    # prioritizing the stale house caused a 250-block naked retreat.
    locations = custom_data.get("locations", {}).get("chest", [])
    for location in reversed(locations):
        if not isinstance(location, dict):
            continue
        data = location.get("data", location)
        try:
            return (int(data["x"]), int(data["y"]), int(data["z"]))
        except (KeyError, TypeError, ValueError):
            continue

    structures = custom_data.get("structures", {})
    house = structures.get("starter_house", {})
    for key in ("supply_chest", "origin"):
        value = house.get(key)
        if isinstance(value, (list, tuple)) and len(value) == 3:
            return tuple(int(axis) for axis in value)

    value = custom_data.get("base_location")
    if isinstance(value, (list, tuple)) and len(value) == 3:
        return tuple(int(axis) for axis in value)
    return None


def _reach_overworld_grave(
    client,
    death_coords: Tuple[int, int, int],
    *,
    attempts: int = 3,
) -> bool:
    """Reach a grave across repeated naked deaths without losing its target."""
    for attempt in range(1, max(1, int(attempts)) + 1):
        state = client.transport.dispatch("get_state", {})
        if state.get("is_dead", False) or float(state.get("health", 20) or 0) <= 0:
            print(
                f"RECOVERY: respawning for grave approach {attempt}/{attempts}"
            )
            client.transport.dispatch("respawn", {})
            reset_inventory_cache()  # dropped-on-death items must not linger in cache
            time.sleep(2.0)
        reached = goto(
            client,
            death_coords[0],
            death_coords[1],
            death_coords[2],
            timeout=120,
        )
        final_state = client.transport.dispatch("get_state", {})
        alive = not final_state.get("is_dead", False) and float(
            final_state.get("health", 20) or 0
        ) > 0
        if reached and alive:
            return True
        print(
            f"RECOVERY: grave approach {attempt}/{attempts} did not finish alive"
        )
    return False


def _record_unsafe_recovery(state, death_coords: Tuple[int, int, int]) -> int:
    """Persist a same-location failure count for recovery circuit diagnosis."""
    custom_data = getattr(state, "custom_data", {})
    recovery = custom_data.setdefault("death_recovery", {})
    location = list(death_coords)
    failures = int(recovery.get("unsafe_failures", 0))
    if recovery.get("location") != location:
        failures = 0
    failures += 1
    recovery.update({"location": location, "unsafe_failures": failures})
    return failures


def _clear_unsafe_recovery(state) -> None:
    custom_data = getattr(state, "custom_data", {})
    custom_data.pop("death_recovery", None)


def _persist_pending_recovery(state, expected: Dict[str, int]) -> None:
    """Best-effort checkpoint of a grave target before the controller can exit."""
    save = getattr(state, "save_checkpoint", None)
    if not callable(save):
        return
    try:
        save(expected)
    except Exception as exc:
        print(f"RECOVERY: pending grave checkpoint deferred ({exc})")


class DeathRecoveryAction(BaseAction):
    """
    Action to handle player death and recovery.

    Extracts death detection and recovery logic into a modular action.
    """

    def execute(self, context: ActionContext) -> ActionResult:
        """
        Execute death recovery sequence.

        Returns True if recovery was needed and performed.
        """
        try:
            # Check for death
            try:
                state = context.client.transport.dispatch("get_state", {})
            except TransportError as exc:
                print(
                    f"Warning: Death recovery get_state timed out ({exc}); "
                    "deferring this cycle"
                )
                return ActionResult.ok("Death recovery deferred due transport timeout")
            if not state.get("is_dead", False) and state.get("health", 20) > 0:
                return handle_alive_pending_recovery(context, state, get_inventory)

            # The player position remains available on the death screen.  Save
            # it before respawning because some bridge versions clear their
            # get_death_location cache as soon as respawn completes.
            death_position = state.get("block_position", state.get("position", {}))
            death_dimension = state.get("dimension", "minecraft:overworld")
            # The death screen still exposes the inventory.  Capture valuable
            # counts before respawn so reaching the coordinates alone can
            # never be mistaken for a complete item recovery.
            expected_critical = _critical_inventory(get_inventory(context.client))

            recovery_state = context.state.custom_data.setdefault(
                "death_recovery", {}
            )
            pending_location = recovery_state.get("pending_location")
            resuming_pending = (
                isinstance(pending_location, (list, tuple))
                and len(pending_location) == 3
            )
            if resuming_pending:
                stored_expected = recovery_state.get("expected_critical", {})
                if isinstance(stored_expected, dict):
                    expected_critical = {
                        item_id: max(
                            int(stored_expected.get(item_id, 0)),
                            int(expected_critical.get(item_id, 0)),
                        )
                        for item_id in set(stored_expected) | set(expected_critical)
                    }
                print(
                    "RECOVERY: resuming persisted grave target "
                    f"{tuple(int(value) for value in pending_location)}"
                )
            elif death_position:
                pending_location = [
                    int(death_position.get("x", 0)),
                    int(death_position.get("y", 0)),
                    int(death_position.get("z", 0)),
                ]
                recovery_state.update(
                    {
                        "pending_location": pending_location,
                        "pending_dimension": death_dimension,
                        "expected_critical": expected_critical,
                    }
                )
                _persist_pending_recovery(context.state, expected_critical)

            print("\n!!! PLAYER DIED !!!")
            print("Starting recovery sequence...")

            # Respawn
            context.client.transport.dispatch("respawn", {})
            reset_inventory_cache()  # dropped-on-death items must not linger in cache
            time.sleep(2.0)

            # Get death location and recover items
            try:
                response = context.client.transport.dispatch("get_death_location", {})
            except TransportError as exc:
                print(
                    f"Warning: Death recovery could not fetch death location ({exc}); "
                    "trying fallback state value"
                )
                response = {"data": state.get("death_location", {})}
            data = response.get("data", response)
            death_x, death_y, death_z = data.get("x"), data.get("y"), data.get("z")
            death_dim = data.get("dimension", death_dimension).lower()
            if resuming_pending:
                death_x, death_y, death_z = pending_location
                death_dim = str(
                    recovery_state.get("pending_dimension", death_dim)
                ).lower()
            if death_x is None and death_position:
                death_x = death_position.get("x")
                death_y = death_position.get("y")
                death_z = death_position.get("z")

            if death_x is not None:
                context.state.custom_data["last_death_location"] = {
                    "x": int(death_x),
                    "y": int(death_y),
                    "z": int(death_z),
                    "dimension": death_dim,
                }
                recovery_state.update(
                    {
                        "pending_location": [
                            int(death_x),
                            int(death_y),
                            int(death_z),
                        ],
                        "pending_dimension": death_dim,
                        "expected_critical": expected_critical,
                    }
                )
                _persist_pending_recovery(context.state, expected_critical)

                print(f"Death location: ({death_x}, {death_y}, {death_z}) in {death_dim}")

                # Dimension-aware recovery logic
                current_phase = context.state.get_current_phase()

                if "nether" in death_dim:
                        # Died in Nether - decide whether to recover in Nether or return to Overworld
                        nether_phases = [Phase.NETHER_AND_BLAZE, Phase.WORLD_UNLOCK]

                        if current_phase in nether_phases:
                            # Can continue in Nether - recover items here
                            print("Recovering items in Nether...")
                            success = goto(context.client, int(death_x), int(death_y), int(death_z), timeout=600)
                            if success:
                                print("Recovered items from Nether death location")
                                time.sleep(2.0)
                                return ActionResult.ok("Recovery completed in Nether", recovered_in_nether=True)
                            else:
                                print("Failed to reach Nether death location")
                                return ActionResult.fail("Failed to reach Nether death location")
                        else:
                            # Need to return to Overworld - recover items in Nether first, then traverse
                            print("Recovering items in Nether before returning to Overworld...")
                            success = goto(context.client, int(death_x), int(death_y), int(death_z), timeout=600)
                            if success:
                                print("Recovered items from Nether death location")
                                time.sleep(2.0)

                            # Now find portal and return to Overworld
                            portal_coords = find_nearest_portal(context.client, "nether")
                            if portal_coords:
                                print(f"Found Nether portal at {portal_coords}")
                                success = goto(context.client, portal_coords[0], portal_coords[1], portal_coords[2], timeout=300)
                                if success:
                                    # Enter portal to return to Overworld
                                    from ..common import enter_nether_portal
                                    if enter_nether_portal(
                                        context.client,
                                        timeout=60,
                                        target_dimension="minecraft:overworld",
                                        portal=portal_coords,
                                    ):
                                        print("Returned to Overworld via portal")
                                        # Reset to bootstrap since we're back at spawn area
                                        context.state.set_phase(Phase.BOOT_SEQUENCE)
                                        return ActionResult.ok("Reset to bootstrap phase after Nether recovery", reset_phase=True)
                                    else:
                                        print("Failed to enter portal back to Overworld")
                                        return ActionResult.fail("Failed to enter portal back to Overworld")
                                else:
                                    print("Failed to reach Nether portal")
                                    return ActionResult.fail("Failed to reach Nether portal")
                            else:
                                print("Could not find Nether portal for return trip")
                                return ActionResult.fail("Could not find Nether portal")

                else:
                    # Died in Overworld - standard recovery.  Resume the
                    # interrupted phase after pickup; completed earlier phases
                    # remain valid and should not be replayed.
                    death_coords = (int(death_x), int(death_y), int(death_z))
                    success = _reach_overworld_grave(
                        context.client,
                        death_coords,
                    )
                    if success:
                        print("Recovered items from death location")
                        if not _sweep_death_drops(context.client, death_coords):
                            return ActionResult.fail(
                                "Death-pile item entities remain after recovery sweep"
                            )
                        shortfall = _recovery_shortfall(
                            context.client, expected_critical
                        )
                        if shortfall:
                            return ActionResult.fail(
                                "Critical inventory recovery is incomplete",
                                missing=shortfall,
                            )
                        retreat = _checkpointed_retreat(context.state)
                        if retreat is not None and retreat != death_coords:
                            retreat_distance = (
                                (retreat[0] - death_coords[0]) ** 2
                                + (retreat[2] - death_coords[2]) ** 2
                            ) ** 0.5
                            if retreat_distance <= 96.0:
                                print(
                                    "RECOVERY: retreating recovered inventory "
                                    f"to {retreat}"
                                )
                                if goto(
                                    context.client,
                                    retreat[0],
                                    retreat[1],
                                    retreat[2],
                                    timeout=180,
                                    tolerance=2.0,
                                ):
                                    if not _player_alive(context.client):
                                        return ActionResult.fail(
                                            "Player died during post-recovery retreat"
                                        )
                                    _clear_unsafe_recovery(context.state)
                                    return ActionResult.ok(
                                        "Recovery completed in Overworld after retreat",
                                        recovered=True,
                                        retreated=True,
                                        reset_phase=False,
                                    )
                            else:
                                print(
                                    "RECOVERY: verified storage is "
                                    f"{retreat_distance:.1f}m away; securing the "
                                    "grave locally instead of risking a naked commute"
                                )
                        if not secure_recovery_area(context.client):
                            failures = _record_unsafe_recovery(
                                context.state, death_coords
                            )
                            print(
                                "RECOVERY_CIRCUIT: unsafe recovery failure "
                                f"{failures} at {death_coords}"
                            )
                            return ActionResult.fail(
                                "Recovered items but could not retreat or secure the area",
                                unsafe_recovery_failures=failures,
                            )
                        if not _player_alive(context.client):
                            return ActionResult.fail(
                                "Player died while securing the recovered grave"
                            )
                        _clear_unsafe_recovery(context.state)
                        return ActionResult.ok(
                            "Recovery completed in Overworld",
                            recovered=True,
                            reset_phase=False,
                        )
                    print("Failed to reach death location")
                    # Grave unreachable/despawned: bootstrap starter tools so a
                    # single bad death does not wedge the bot toolless forever.
                    if _bootstrap_starter_pickaxe(context.client):
                        _clear_unsafe_recovery(context.state)
                        return ActionResult.ok(
                            "Grave unreachable; bootstrapped starter tools instead",
                            recovered=False,
                            bootstrapped_tools=True,
                            reset_phase=False,
                        )
                    return ActionResult.fail("Failed to reach death location")

            print("Death location unavailable; refusing to discard the active phase")
            if _bootstrap_starter_pickaxe(context.client):
                _clear_unsafe_recovery(context.state)
                return ActionResult.ok(
                    "Death location unavailable; bootstrapped starter tools instead",
                    recovered=False,
                    bootstrapped_tools=True,
                    reset_phase=False,
                )
            return ActionResult.fail("Death location unavailable for item recovery")

        except Exception as e:
            print(f"Warning: Failed to handle death recovery: {e}")
            return ActionResult.fail(f"Death recovery failed: {e}")
