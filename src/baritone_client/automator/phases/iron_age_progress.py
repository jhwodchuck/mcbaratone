"""Durable task-boundary recovery for the iron and diamond phase."""

from __future__ import annotations

import time
from typing import Any, Callable, Mapping, Optional

from ..resource_manager import ResourceManager
from ..state_manager import StateManager
from ...common import ActionTask, SequentialTask
from ...common import base as house_utils
from ...common.inventory import count_item, craft
from ...common.resources import remaining_pickaxe_durability


STAGE_KEY = "food_and_iron_stage"
DEEP_MINING_STAGE = "deep_mining"
DEEP_RESUME_PICK_DURABILITY = 96
INITIAL_IRON_TRANSITION_Y = 15

StateReader = Callable[[Any, str], Optional[dict]]


def stage_is_deep_mining(state: Optional[StateManager]) -> bool:
    """Return whether the durable phase marker has entered the descent."""
    if state is None:
        return False
    custom_data = getattr(state, "custom_data", {})
    if not isinstance(custom_data, Mapping):
        return False
    marker = custom_data.get(STAGE_KEY)
    if isinstance(marker, Mapping):
        marker = marker.get("stage")
    return marker == DEEP_MINING_STAGE


def mark_deep_mining_stage(
    client: Any,
    resources: ResourceManager,
    state: StateManager,
    *,
    read_state: StateReader,
    live_state: Optional[dict] = None,
    inferred: bool = False,
) -> bool:
    """Persist the task boundary so retries do not replay surface setup."""
    snapshot = live_state or read_state(client, "Mark deep-mining stage") or {}
    position = snapshot.get("block_position", snapshot.get("position", {}))
    if position:
        state.update_position(
            float(position.get("x", 0)),
            float(position.get("y", 64)),
            float(position.get("z", 0)),
        )
    state.custom_data[STAGE_KEY] = {
        "stage": DEEP_MINING_STAGE,
        "recorded_at": time.time(),
        "inferred_from_live_descent": bool(inferred),
    }
    state.save_checkpoint(resources.refresh_inventory())
    return True


def resume_deep_mining_in_place(
    client: Any,
    resources: ResourceManager,
    state: StateManager,
    *,
    read_state: StateReader,
) -> bool:
    """Recognize a retry that already crossed the pre-descent boundary.

    Older checkpoints did not persist individual FOOD_AND_IRON tasks. A
    restart at mining depth therefore replayed furnace, storage, food, and
    workstation preparation from the bottom of the shaft. A carried bucket
    plus a usable iron-or-better pick is concrete evidence that the starter-kit
    boundary was crossed; a low Y additionally migrates old checkpoints.
    """
    if not hasattr(getattr(client, "transport", None), "dispatch"):
        return False
    if not isinstance(getattr(state, "custom_data", None), dict):
        return False

    live = read_state(client, "Deep-mining resume check") or {}
    position = live.get("block_position", live.get("position", {}))
    try:
        current_y = int(position.get("y", 64))
    except (TypeError, ValueError):
        current_y = 64
    mining_pickaxes = [
        "minecraft:iron_pickaxe",
        "minecraft:diamond_pickaxe",
        "minecraft:netherite_pickaxe",
    ]
    kit_ready = (
        count_item(client, "minecraft:bucket") >= 1
        and remaining_pickaxe_durability(client, mining_pickaxes)
        >= DEEP_RESUME_PICK_DURABILITY
    )
    if not kit_ready:
        return False

    marked = stage_is_deep_mining(state)
    inferred = current_y <= INITIAL_IRON_TRANSITION_Y
    if not marked and not inferred:
        return False
    if not marked:
        mark_deep_mining_stage(
            client,
            resources,
            state,
            read_state=read_state,
            live_state=live,
            inferred=True,
        )
    print(
        "  Resuming the persisted deep-mining leg in place; skipping "
        "completed surface preparation."
    )
    return True


def carry_deep_mining_workstation(client: Any) -> bool:
    """Carry one table so a broken pick can be replaced underground."""
    if count_item(client, "minecraft:crafting_table") >= 1:
        return True
    print("  Preparing a carried crafting table for deep-mining repairs...")
    return craft(client, "minecraft:crafting_table", 1)


def build_phase_tasks(
    handler: Any,
    resources: ResourceManager,
    state: StateManager,
    *,
    resuming_deep_mining: bool,
) -> list[ActionTask]:
    """Compose surface preparation and resumable deep-mining work."""
    tasks = [
        ActionTask(
            "Opportunistically establish respawn anchor",
            lambda client: bool(
                house_utils.try_establish_respawn_anchor_now(client, state)
            )
            or True,
        ),
        ActionTask("Stabilize hunger", handler._stabilize_hunger),
    ]
    if not resuming_deep_mining:
        tasks.extend(
            [
                ActionTask(
                    "Ensure initial mining pickaxe",
                    handler._ensure_initial_mining_pickaxe,
                ),
                ActionTask("Mine initial iron (15)", handler._mine_initial_iron),
                ActionTask(
                    "Return to base for protected smelting",
                    lambda client: handler._return_to_base_for_initial_smelting(
                        client, state
                    ),
                ),
                # Craft in the player grid before opening storage. Container
                # opens are asynchronous and can otherwise race the close.
                ActionTask(
                    "Carry deep-mining workstation",
                    carry_deep_mining_workstation,
                ),
                ActionTask(
                    "Deposit bulky excess before smelting",
                    lambda client: handler._deposit_excess_at_home(client, state),
                ),
                ActionTask("Smelt iron ingots", handler._smelt_iron),
                ActionTask(
                    "Prepare deep-mining tools + bucket",
                    handler._craft_essential_iron,
                ),
                ActionTask(
                    "Bank starter iron before deep expedition",
                    lambda client: handler._bank_progression_at_home(client, state),
                ),
                ActionTask(
                    "Establish renewable food source",
                    handler._ensure_durable_food,
                ),
                ActionTask(
                    "Equip affordable armor before deep descent",
                    handler._equip_affordable_pre_descent_armor,
                ),
                ActionTask(
                    "Restore expedition pickaxe",
                    handler._ensure_expedition_pickaxe,
                ),
                ActionTask(
                    "Checkpoint deep-mining task boundary",
                    lambda client: mark_deep_mining_stage(
                        client,
                        resources,
                        state,
                        read_state=handler._read_state,
                    ),
                ),
            ]
        )

    tasks.extend(
        [
            ActionTask("Dig to diamond level Y-58", handler._dig_staircase),
            ActionTask("Mine diamonds & remaining iron", handler._bulk_mine),
            ActionTask(
                "Return to base with mined valuables",
                lambda client: handler._return_to_base(client, state),
            ),
            ActionTask(
                "Withdraw banked iron for equipment crafting",
                lambda client: handler._withdraw_banked_iron(client, state),
            ),
            ActionTask(
                "Smelt mined iron",
                lambda client: handler._smelt_iron(client, force=True),
            ),
            ActionTask("Craft full iron armor", handler._craft_iron_armor),
            ActionTask("Equip and verify iron armor", handler._equip_iron_armor),
            ActionTask("Craft iron tools", handler._craft_iron_tools),
            ActionTask(
                "Bank progression loot at home",
                lambda client: handler._bank_progression_at_home(client, state),
            ),
        ]
    )
    return tasks


def run_phase(
    handler: Any,
    client: Any,
    resources: ResourceManager,
    state: StateManager,
):
    """Run the phase from its durable pre-descent or deep-mining boundary."""
    resuming = resume_deep_mining_in_place(
        client,
        resources,
        state,
        read_state=handler._read_state,
    )
    tasks = build_phase_tasks(
        handler,
        resources,
        state,
        resuming_deep_mining=resuming,
    )
    return SequentialTask("Iron & Diamond", tasks).run(client)
