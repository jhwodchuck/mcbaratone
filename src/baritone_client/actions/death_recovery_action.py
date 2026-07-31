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
from ..common.combat import (
    defend_or_flee,
    scan_for_threats,
    secure_recovery_area,
)
from ..common.inventory import get_inventory, reset_inventory_cache
from ..common.surface_recovery import position_is_aquatic
from ..automator.state_manager import Phase
from .death_recovery_state import (
    abandon_repeated_unsafe_pending_recovery,
    abandon_unrecoverable_grave,
    handle_alive_pending_recovery,
)

# How many times a single grave may come up short on critical items before it
# is written off. Items despawn after five minutes and anything dropped in
# water washes away, so past this point the shortfall is permanent and
# retrying is an infinite relaunch loop rather than a recovery.
_MAX_INCOMPLETE_GRAVE_ATTEMPTS = 2


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


def _wait_for_clear_death_area(
    client,
    *,
    attempts: int = 15,
    clear_polls: int = 2,
    poll_interval: float = 2.0,
) -> bool:
    """Keep the player dead until its nearby killer pack has dispersed."""
    clear_count = 0
    for attempt in range(1, max(1, int(attempts)) + 1):
        threats = scan_for_threats(client, radius=16)
        if threats:
            clear_count = 0
            nearest = threats[0]
            print(
                "RECOVERY: deferring respawn while "
                f"{nearest.get('type', 'hostiles')} remains nearby "
                f"({attempt}/{attempts})"
            )
        else:
            clear_count += 1
            if clear_count >= max(1, int(clear_polls)):
                print("RECOVERY: death area remained clear; respawning")
                return True
        time.sleep(max(0.0, poll_interval))
    print("RECOVERY: death area stayed hostile; leaving player safely unrespawned")
    return False


def _respawn_after_death_area_clears(client) -> Optional[ActionResult]:
    """Respawn only after consecutive clear observations of the death area."""
    if not _wait_for_clear_death_area(client):
        return ActionResult.fail(
            "Respawn deferred until the death area clears",
            respawn_deferred=True,
        )
    client.transport.dispatch("respawn", {})
    reset_inventory_cache()
    time.sleep(2.0)
    return None


def _death_was_aquatic(
    client,
    death_position: Dict,
    death_dimension: str,
) -> bool:
    """Classify a loaded death block before respawn can unload its chunk."""
    if not death_position or "nether" in death_dimension.lower():
        return False
    try:
        return position_is_aquatic(
            client,
            (
                int(death_position.get("x", 0)),
                int(death_position.get("y", 0)),
                int(death_position.get("z", 0)),
            ),
        )
    except Exception:
        # Existing in-route survival checks protect graves whose block probe
        # is unavailable.
        return False


def _death_details(client, state: Dict) -> Tuple[Dict, str, bool]:
    """Return death position, dimension, and pre-respawn aquatic evidence."""
    position = state.get("block_position", state.get("position", {}))
    dimension = state.get("dimension", "minecraft:overworld")
    return position, dimension, _death_was_aquatic(client, position, dimension)


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


def _guard_grave_approach(client) -> None:
    """Abort a naked grave route as soon as survival needs intervention."""
    state = client.transport.dispatch("get_state", {})
    health = float(state.get("health", 20) or 0)
    food = int(state.get("food_level", 20) or 0)
    if health < 12.0 or food < 6:
        print(
            "RECOVERY: abandoning grave approach at unsafe survival margin "
            f"(health={health:.1f}, food={food})"
        )
    elif not defend_or_flee(client):
        return
    else:
        print(
            "RECOVERY: abandoning grave approach after combat-defense "
            "intervention"
        )
    client._last_navigation_survival_abort = True
    client.transport.dispatch("cancel", {})
    raise RuntimeError("unsafe grave approach interrupted")


def _reach_overworld_grave(
    client,
    death_coords: Tuple[int, int, int],
    *,
    attempts: int = 2,
    known_aquatic: bool = False,
) -> bool:
    """Reach a grave across repeated naked deaths without losing its target."""
    if known_aquatic:
        print("RECOVERY: abandoning known aquatic grave before navigation")
        client._last_navigation_survival_abort = True
        return False
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
            on_tick=lambda: _guard_grave_approach(client),
        )
        if getattr(client, "_last_navigation_survival_abort", False):
            print(
                "RECOVERY: abandoning unsafe grave approach after "
                "survival intervention"
            )
            return False
        final_state = client.transport.dispatch("get_state", {})
        alive = not final_state.get("is_dead", False) and float(
            final_state.get("health", 20) or 0
        ) > 0
        if reached and alive:
            return True
        print(
            f"RECOVERY: grave approach {attempt}/{attempts} did not finish alive"
        )
        # Replaying the identical naked route after it just killed the player
        # compounds the loss without adding information. Persist the failure
        # in execute() and let the circuit/bootstrap path choose a new action.
        if not alive:
            return False
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


def _remember_death(
    context: ActionContext,
    recovery_state: Dict,
    death_coords: Tuple[int, int, int],
    death_dimension: str,
    expected_critical: Dict[str, int],
) -> None:
    """Persist one canonical death target before attempting recovery."""
    context.state.custom_data["last_death_location"] = {
        "x": death_coords[0],
        "y": death_coords[1],
        "z": death_coords[2],
        "dimension": death_dimension,
    }
    recovery_state.update(
        {
            "pending_location": list(death_coords),
            "pending_dimension": death_dimension,
            "expected_critical": expected_critical,
        }
    )
    _persist_pending_recovery(context.state, expected_critical)


def _recover_nether_death(
    context: ActionContext,
    death_coords: Tuple[int, int, int],
    current_phase: Phase,
) -> ActionResult:
    """Recover in the Nether or return through a verified portal."""
    client = context.client
    if current_phase in (Phase.NETHER_AND_BLAZE, Phase.WORLD_UNLOCK):
        print("Recovering items in Nether...")
        if goto(client, *death_coords, timeout=600):
            time.sleep(2.0)
            return ActionResult.ok(
                "Recovery completed in Nether", recovered_in_nether=True
            )
        return ActionResult.fail("Failed to reach Nether death location")

    print("Recovering items in Nether before returning to Overworld...")
    if goto(client, *death_coords, timeout=600):
        time.sleep(2.0)
    portal = find_nearest_portal(client, "nether")
    if portal is None:
        return ActionResult.fail("Could not find Nether portal")
    print(f"Found Nether portal at {portal}")
    if not goto(client, *portal, timeout=300):
        return ActionResult.fail("Failed to reach Nether portal")
    from ..common import enter_nether_portal

    if not enter_nether_portal(
        client,
        timeout=60,
        target_dimension="minecraft:overworld",
        portal=portal,
    ):
        return ActionResult.fail("Failed to enter portal back to Overworld")
    context.state.set_phase(Phase.BOOT_SEQUENCE)
    return ActionResult.ok(
        "Reset to bootstrap phase after Nether recovery", reset_phase=True
    )


def _rebuild_after_lethal_grave(context: ActionContext, abandoned: Dict) -> ActionResult:
    """Respawn and rebuild after opening a repeated grave-route circuit."""
    print(
        "RECOVERY_CIRCUIT: refusing to replay lethal grave route "
        f"{tuple(abandoned['location'])}; respawning to rebuild"
    )
    context.client.transport.dispatch("respawn", {})
    reset_inventory_cache()
    time.sleep(2.0)
    if _bootstrap_starter_pickaxe(context.client):
        return ActionResult.ok(
            "Abandoned lethal grave route and rebuilt starter tools",
            recovered=False,
            grave_abandoned=True,
            bootstrapped_tools=True,
            reset_phase=False,
        )
    return ActionResult.fail(
        "Lethal grave route abandoned but starter rebuild failed",
        grave_abandoned=True,
    )


def _abandon_or_defer_repeated_grave(
    context: ActionContext,
    recovery_state: Dict,
) -> Optional[ActionResult]:
    """Open a repeated-grave circuit only after its killer pack disperses."""
    if int(recovery_state.get("unsafe_failures", 0)) < 1:
        return None
    if not _wait_for_clear_death_area(context.client):
        return ActionResult.fail(
            "Respawn deferred until the repeated grave area clears",
            respawn_deferred=True,
        )
    abandoned = abandon_repeated_unsafe_pending_recovery(context.state, {})
    if abandoned is None:
        return None
    return _rebuild_after_lethal_grave(context, abandoned)


class DeathRecoveryAction(BaseAction):
    """
    Action to handle player death and recovery.

    Extracts death detection and recovery logic into a modular action.
    """

    def execute(self, context: ActionContext) -> ActionResult:
        """Recover a death or return a bounded failure."""
        try:
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
            death_position, death_dimension, death_was_aquatic = _death_details(
                context.client, state
            )
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
                repeated_result = _abandon_or_defer_repeated_grave(
                    context, recovery_state
                )
                if repeated_result is not None:
                    return repeated_result
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
            respawn_failure = _respawn_after_death_area_clears(context.client)
            if respawn_failure is not None:
                return respawn_failure
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
                death_coords = (
                    int(death_x),
                    int(death_y),
                    int(death_z),
                )
                _remember_death(
                    context,
                    recovery_state,
                    death_coords,
                    death_dim,
                    expected_critical,
                )
                print(f"Death location: ({death_x}, {death_y}, {death_z}) in {death_dim}")
                current_phase = context.state.get_current_phase()

                if "nether" in death_dim:
                    return _recover_nether_death(
                        context, death_coords, current_phase
                    )

                else:
                    # Preserve completed phases after Overworld recovery.
                    success = _reach_overworld_grave(
                        context.client,
                        death_coords,
                        known_aquatic=death_was_aquatic,
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
                            # Count the attempt so this path is bounded. It
                            # previously returned fail without recording
                            # anything, which left unsafe_failures at 0 --
                            # and abandon_exhausted_pending_recovery only
                            # fires at >=1, so the grave was never written
                            # off and every relaunch resumed the same
                            # unsatisfiable target forever.
                            failures = _record_unsafe_recovery(
                                context.state, death_coords
                            )
                            print(
                                "RECOVERY_CIRCUIT: incomplete critical recovery "
                                f"{failures}/{_MAX_INCOMPLETE_GRAVE_ATTEMPTS} at "
                                f"{death_coords}; missing {shortfall}"
                            )
                            if failures < _MAX_INCOMPLETE_GRAVE_ATTEMPTS:
                                # Early attempts are worth retrying: a mob may
                                # have interrupted the sweep while the drops
                                # are still on the ground.
                                return ActionResult.fail(
                                    "Critical inventory recovery is incomplete",
                                    missing=shortfall,
                                )
                            print(
                                "RECOVERY: writing off unrecoverable grave at "
                                f"{death_coords}; continuing without its contents"
                            )
                            abandon_unrecoverable_grave(
                                context.state,
                                death_coords,
                                shortfall,
                                get_inventory(context.client),
                                failures,
                            )
                            _bootstrap_starter_pickaxe(context.client)
                            return ActionResult.ok(
                                "Abandoned unrecoverable grave and continued",
                                recovered=False,
                                grave_abandoned=True,
                                missing=shortfall,
                                reset_phase=False,
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
                    failures = _record_unsafe_recovery(
                        context.state, death_coords
                    )
                    _persist_pending_recovery(
                        context.state, expected_critical
                    )
                    print(
                        "RECOVERY_CIRCUIT: unreachable or lethal grave "
                        f"failure {failures} at {death_coords}"
                    )
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
