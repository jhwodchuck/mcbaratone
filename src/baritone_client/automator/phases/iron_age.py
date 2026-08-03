"""
Phase 2: Iron & Diamond Phase
"""

import time
from typing import Optional, Tuple

from ..phase_executor import PhaseHandler
from ..resource_manager import ResourceManager
from ..state_manager import StateManager
from ...common import TaskResult, SequentialTask, ActionTask
from ...common.tasks import SurvivalRecoveryRequired
from ...common.resources import (
    _craft_with_table,
    _read_state_with_retry,
    _safe_close_screen,
    ensure_supplies,
    equip_best_pickaxe,
    gather_ores,
    gather_stone,
    gather_wood,
    go_to_y_level,
    manage_inventory,
    remaining_pickaxe_durability,
    resume_active_furnace,
    _smelt_requirement_shortfall,
)
from ...common.inventory import (
    count_item,
    craft,
    deposit_excess_to_chest,
    deposit_progression_to_chest,
    equip_best_armor,
    free_inventory_slots,
    has_full_armor,
    resolve_storage_location,
    withdraw_required_from_catalog,
    withdraw_required_from_chest,
    _ensure_raw_planks,
)
from ...common.navigation import find_nearby_block, goto, staged_goto
from ...common import harness_ops
from ...common import base as house_utils
from ...common.combat import acquire_emergency_food, eat_until_hunger, recover_health
from ...common.farming import harvest_wheat_farm
from ...common.husbandry import (
    KNOWN_HERD_WAYPOINTS,
    discover_herd,
    visit_known_herd_for_loot,
)
from .iron_age_food import persisted_food_source, remember_food_source

class FoodAndIronHandler(PhaseHandler):
    """Phase 2: Iron & Diamond mining - Hour 1-2."""
    _SHARED_STAIRCASE_KEY = "shared_mining_staircase"
    _INITIAL_IRON_TARGET = 15
    _IRON_BANK_TARGET = 30
    _IRON_BANK_BATCH = 8
    _INITIAL_IRON_TRANSITION_Y = 15

    def __init__(self) -> None:
        self.state: Optional[StateManager] = None
        self._initial_iron_supplies_withdrawn = False
        self._initial_iron_transitioned = False
        self._stabilize_hunger_failures = 0
        self._cached_state: Optional[dict] = None

    def _read_state(self, client, label: str):
        state, attempts = _read_state_with_retry(
            client,
            retries=3,
            label=label,
        )
        if state is not None:
            self._cached_state = state
        elif self._cached_state is not None:
            print(
                f"{label}: using cached state after {attempts} failed read "
                "attempt(s)."
            )
            return self._cached_state
        if state is None:
            print(f"{label}: state read failed after {attempts} attempt(s).")
        return state
    
    def get_name(self) -> str:
        return "Iron & Diamond (Hour 1-2)"
    
    def execute(self, client, resources: ResourceManager, state: StateManager) -> TaskResult:
        self.state = state
        # Each phase attempt must re-read durable storage. Keeping this flag
        # across retries hid iron and tools banked by the previous attempt.
        self._initial_iron_supplies_withdrawn = False
        tasks = [
            # Best-effort, non-blocking: if a bed exists and it happens to be
            # night right now, sleep to anchor the respawn point near base.
            # A checkpoint resumed here (already past BASE_CONSTRUCTION,
            # which normally establishes this) may never have gotten the
            # chance -- deep mining routinely strands the player too far
            # underground from the surface bed to reach it before dawn, so
            # without this a death sends the player back to world spawn
            # instead of near base. Always reports success; establishing the
            # anchor is a bonus, not a requirement to keep mining.
            ActionTask(
                "Opportunistically establish respawn anchor",
                lambda c: bool(house_utils.try_establish_respawn_anchor_now(c, state)) or True,
            ),
            # Do not begin a long mining phase on an empty hunger bar.  This
            # uses carried food (including emergency rotten flesh) and fails
            # closed if the bot has no edible reserve.
            ActionTask("Stabilize hunger", self._stabilize_hunger),
            # Phase 2a: Get initial iron (Baritone will dig to reach it)
            ActionTask("Ensure initial mining pickaxe", self._ensure_initial_mining_pickaxe),
            ActionTask("Mine initial iron (15)", self._mine_initial_iron),  # Creates tunnels naturally!
            ActionTask(
                "Return to base for protected smelting",
                lambda c: self._return_to_base_for_initial_smelting(c, state),
            ),
            # Smelting and table crafting both need output slots.  Bank the
            # conservative excess allowlist while the bot is already beside
            # its checkpointed home chest instead of dropping stacks later.
            ActionTask(
                "Deposit bulky excess before smelting",
                lambda c: self._deposit_excess_at_home(c, state),
            ),
            ActionTask("Smelt iron ingots", self._smelt_iron),
            ActionTask("Prepare deep-mining tools + bucket", self._craft_essential_iron),
            ActionTask(
                "Bank starter iron before deep expedition",
                lambda c: self._bank_progression_at_home(c, state),
            ),
            ActionTask("Establish renewable food source", self._ensure_durable_food),
            ActionTask(
                "Equip affordable armor before deep descent",
                self._equip_affordable_pre_descent_armor,
            ),
            ActionTask(
                "Restore expedition pickaxe",
                self._ensure_expedition_pickaxe,
            ),
            
            # Phase 2b: Now mine deep diamonds with iron tools
            ActionTask("Dig to diamond level Y-58", self._dig_staircase),
            ActionTask("Mine diamonds & remaining iron", self._bulk_mine),
            ActionTask(
                "Return to base with mined valuables",
                lambda c: self._return_to_base(c, state),
            ),
            ActionTask(
                "Withdraw banked iron for equipment crafting",
                lambda c: self._withdraw_banked_iron(c, state),
            ),
            ActionTask("Smelt mined iron", lambda c: self._smelt_iron(c, force=True)),
            ActionTask("Craft full iron armor", self._craft_iron_armor),
            ActionTask("Equip and verify iron armor", self._equip_iron_armor),
            ActionTask("Craft iron tools", self._craft_iron_tools),
            ActionTask(
                "Bank progression loot at home",
                lambda c: self._bank_progression_at_home(c, state),
            ),
        ]
        
        executor = SequentialTask("Iron & Diamond", tasks)
        return executor.run(client)

    def _stabilize_hunger(self, client) -> bool:
        state = self._read_state(client, "Stabilize hunger") or {}
        health = float(state.get("health", 20) or 0)
        if health < 12.0:
            print(f"  Health critical ({health:.1f}/20) - recovering before mining...")
            recovered = recover_health(client, minimum_health=12.0, timeout=10.0)
            if not recovered:
                # Armored workers can safely travel to a durable supply before
                # blind local hunting. Unarmored workers try the local bounded
                # recovery first, then a known source.
                if has_full_armor(client, minimum_material="iron"):
                    recovered = self._recover_food_from_known_sources(client)
                    if not recovered:
                        recovered = acquire_emergency_food(
                            client,
                            minimum_health=12.0,
                            minimum_food=14,
                            timeout=300.0,
                        )
                else:
                    recovered = acquire_emergency_food(
                        client,
                        minimum_health=12.0,
                        minimum_food=14,
                        timeout=300.0,
                    )
                    if not recovered:
                        recovered = self._recover_food_from_known_sources(client)
            recovery_state = self._read_state(client, "Stabilize hunger recovery") or {}
            health = float(recovery_state.get("health", 20) or 0)
            if health < 12.0:
                print(
                    "  Emergency recovery did not restore safe health; "
                    "holding before mining."
                )
                raise SurvivalRecoveryRequired(
                    f"health remains {health:.1f}/20 before iron mining"
                )
            state = self._read_state(client, "Stabilize hunger post-feed") or {}

        food_level = int(state.get("food_level", state.get("food", 20)))
        # Gathering helpers stop at food <= 10 and health regeneration starts
        # at 18. Twelve is the minimum useful expedition handoff: below it the
        # next action immediately aborts or cannot heal.
        if food_level >= 12:
            self._stabilize_hunger_failures = 0
            return True
        print(f"  Hunger low ({food_level}/20) - eating before mining...")
        if eat_until_hunger(client, minimum_food=12):
            self._stabilize_hunger_failures = 0
            return True
        print("  No carried food restored hunger; starting emergency food search...")
        if acquire_emergency_food(
            client,
            minimum_health=12.0,
            minimum_food=12,
            timeout=300.0,
        ):
            self._stabilize_hunger_failures = 0
            return True

        # The local bounded search above can never succeed in an
        # animal-sparse biome -- it will retry the same empty area forever.
        # Try the farm/herd fallback before settling for a degraded floor or
        # giving up outright. Confirmed live: Bot09 stuck in a
        # "no passive food source loaded" loop with nothing nearby to hunt.
        if self._recover_food_from_known_sources(client):
            self._stabilize_hunger_failures = 0
            return True

        self._stabilize_hunger_failures += 1
        print(
            "  Emergency food search failed "
            f"(failures={self._stabilize_hunger_failures})."
        )
        raise SurvivalRecoveryRequired(
            f"food remains {food_level}/20 before iron mining"
        )

    def _recover_food_from_known_sources(self, client) -> bool:
        """Harvest the established wheat farm, or hunt the operator-known
        distant herd, then eat -- the fallback for a biome with nothing
        huntable near base. Cheapest option (the nearby farm) first; the
        herd trip is a genuine expedition and only worth it once the farm
        can't (or doesn't yet) supply enough.
        """
        if self.state is not None:
            moved = withdraw_required_from_catalog(
                client,
                {
                    "minecraft:bread": 8,
                    "minecraft:cooked_beef": 8,
                    "minecraft:cooked_porkchop": 8,
                    "minecraft:cooked_chicken": 8,
                    "minecraft:baked_potato": 8,
                },
                state=self.state,
                max_travel_distance=96.0,
            )
            if moved > 0 and eat_until_hunger(client, minimum_food=12):
                return True

        farm = (self.state.custom_data.get("wheat_farm") if self.state else None) or {}
        origin = farm.get("origin")
        if isinstance(origin, (list, tuple)) and len(origin) == 3:
            fx, fy, fz = (int(v) for v in origin)
            if harvest_wheat_farm(client, fx, fy, fz) and eat_until_hunger(
                client, minimum_food=12
            ):
                return True

        source = persisted_food_source(self.state)
        if not source:
            return False
        kwargs = {"preserve_breeding_pair": True}
        animal_type = str(source.get("animal_type", "cow"))
        kwargs["location"] = source["location"]
        return visit_known_herd_for_loot(
            client,
            {"minecraft:beef": 3},
            animal_type,
            **kwargs,
        ) and eat_until_hunger(client, minimum_food=12)

    def _remember_renewable_food_source(
        self,
        animal_type: str,
        location: Tuple[int, int, int],
    ) -> None:
        """Record a herd only after it has been observed and verified."""
        remember_food_source(self.state, animal_type, location)

    def _ensure_durable_food(self, client) -> bool:
        """Prove a renewable source instead of crediting carried food alone."""
        if self.state is None:
            return False
        if self.state.custom_data.get("wheat_farm", {}).get("origin"):
            return True
        if persisted_food_source(self.state):
            return True

        # This world's starter biome is known to be animal-sparse. Give local
        # discovery one short chance to observe a closer herd, then use the
        # operator-verified renewable waypoint instead of spending five
        # minutes in an explore process that may be pathing without moving.
        location = discover_herd(
            client,
            "cow",
            timeout=45.0,
            stall_timeout=18.0,
        )
        if location is None:
            location = KNOWN_HERD_WAYPOINTS.get("cow")
        if location is None:
            return False
        verified = visit_known_herd_for_loot(
            client,
            {},
            "cow",
            preserve_breeding_pair=True,
            location=location,
        )
        if not verified:
            return False
        self._remember_renewable_food_source("cow", location)
        return True

    def _initial_iron_target_satisfied(self, client) -> bool:
        return (
            count_item(client, "minecraft:iron_ingot")
            + count_item(client, "minecraft:raw_iron")
            >= self._INITIAL_IRON_TARGET
        )

    def _ensure_initial_mining_pickaxe(self, client) -> bool:
        """Acquire a pickaxe only when the initial iron still needs mining.

        Resumed bots can already carry the full iron target after their prior
        pickaxe broke. Requiring another stone pickaxe before recognizing that
        completed objective trapped them in a ten-minute craft loop. Prefer a
        stored tool, then make one with a bounded attempt; if crafting exposes
        a genuine wood shortfall, gather a small reserve before one retry.
        """
        if self._initial_iron_target_satisfied(client):
            print("  Initial iron target already satisfied; no mining pickaxe needed.")
            return True

        self._withdraw_initial_iron_supplies(client)
        if self._initial_iron_target_satisfied(client):
            print("  Initial iron target restored from storage; no mining pickaxe needed.")
            return True

        mining_pickaxes = (
            "minecraft:stone_pickaxe",
            "minecraft:iron_pickaxe",
            "minecraft:diamond_pickaxe",
            "minecraft:netherite_pickaxe",
        )
        if remaining_pickaxe_durability(client, mining_pickaxes) > 0:
            print("  Usable stone-or-better pickaxe already carried; continuing.")
            return True

        result = ensure_supplies(
            client,
            {"minecraft:stone_pickaxe": 1},
            timeout=30,
        )
        if result.success:
            return True

        if self.state is not None:
            withdraw_required_from_catalog(
                client,
                {
                    "minecraft:stone_pickaxe": 1,
                    "minecraft:wooden_pickaxe": 1,
                    "minecraft:stick": 4,
                    "minecraft:oak_planks": 8,
                    "minecraft:spruce_planks": 8,
                    "minecraft:birch_planks": 8,
                    "minecraft:oak_log": 2,
                    "minecraft:spruce_log": 2,
                    "minecraft:birch_log": 2,
                    "minecraft:cobblestone": 3,
                },
                state=self.state,
            )
            result = ensure_supplies(
                client,
                {"minecraft:stone_pickaxe": 1},
                timeout=30,
            )
            if result.success:
                return True

        print("  Pickaxe craft lacked usable dependencies; gathering a wood reserve...")
        if not gather_wood(client, count=2, timeout=90):
            return False
        return ensure_supplies(
            client,
            {"minecraft:stone_pickaxe": 1},
            timeout=60,
        ).success

    def _dig_staircase(self, client) -> bool:
        """Dig a proper staircase down to Y-58."""
        if self._deep_mining_objectives_complete(client):
            print("  Deep-mining objectives already complete; skipping descent.")
            return True

        shared = self._shared_staircase()
        if shared is not None:
            entrance, bottom = shared
            live = self._read_state(client, "Shared staircase join")
            if live is None:
                return False
            position = live.get("block_position", live.get("position", {}))
            current_y = int(position.get("y", entrance[1]))
            vertical_span = max(1, entrance[1] - bottom[1])
            descended = max(0, min(vertical_span, entrance[1] - current_y))

            def staircase_point(step: int):
                ratio = step / vertical_span
                return tuple(
                    round(start + (finish - start) * ratio)
                    for start, finish in zip(entrance, bottom)
                )

            join = staircase_point(descended)
            print(
                f"  Joining shared mining staircase at {join}; "
                f"bottom is {bottom}."
            )
            if not goto(
                client,
                join[0],
                join[1],
                join[2],
                timeout=300,
                check_interval=0.5,
                tolerance=2.0,
            ):
                return False

            # A single bottom goal lets Baritone choose any route and can put
            # the worker directly above the chamber instead of in the stairs.
            # Pin traversal to short points on the configured centerline.
            next_step = descended + 8
            while next_step < vertical_span:
                waypoint = staircase_point(next_step)
                if not goto(
                    client,
                    waypoint[0],
                    waypoint[1],
                    waypoint[2],
                    timeout=120,
                    check_interval=0.5,
                    tolerance=2.0,
                ):
                    return False
                next_step += 8
            return goto(
                client,
                bottom[0],
                bottom[1],
                bottom[2],
                timeout=180,
                check_interval=0.5,
                tolerance=2.0,
            )

        print("Digging staircase to Y-58...")
        return go_to_y_level(client, -58)

    def _shared_staircase(self):
        """Return one fleet-configured staircase, or preserve legacy descent."""
        if self.state is None:
            return None
        raw = self.state.custom_data.get(self._SHARED_STAIRCASE_KEY)
        if not isinstance(raw, dict):
            return None
        entrance = self._normalize_position(raw.get("entrance"))
        bottom = self._normalize_position(raw.get("bottom"))
        if entrance is None or bottom is None or bottom[1] >= entrance[1]:
            return None
        return entrance, bottom

    def _deep_mining_objectives_complete(self, client) -> bool:
        """Recognize either an unspent haul or gear made from that haul.

        A resumed run may have already turned its iron into armor and tools.
        Looking only at remaining raw iron and ingots made the phase excavate a
        second staircase through the starter-house floor even though every
        progression output was already present.
        """
        if self._total_owned(client, "minecraft:diamond") < 5:
            return False

        unspent_iron = (
            self._total_owned(client, "minecraft:raw_iron")
            + self._total_owned(client, "minecraft:iron_ingot")
        )
        if unspent_iron >= 30:
            return True

        required_gear = (
            "minecraft:iron_pickaxe",
            "minecraft:iron_sword",
            "minecraft:iron_axe",
            "minecraft:iron_shovel",
            "minecraft:bucket",
        )
        return has_full_armor(client, minimum_material="iron") and all(
            count_item(client, item_id) >= 1 for item_id in required_gear
        )

    def _return_to_base_for_initial_smelting(self, client, state: StateManager) -> bool:
        """Return to base to smelt the FIRST iron into the pickaxe + bucket.

        This step exists only to reach the furnace and forge the starter kit.
        Once that kit exists, a later failure (most often the Y-58 descent)
        must NOT re-run this step and drag the bot all the way back to the
        surface, restarting the descent from scratch. It is skipped in that
        case so the phase resumes deep mining from wherever the bot already is.
        Any iron mined afterwards is smelted by the later "Smelt mined iron"
        step at the base, not by dragging the bot home mid-descent.
        """
        essentials_ready = (
            count_item(client, "minecraft:iron_pickaxe") >= 1
            and count_item(client, "minecraft:bucket") >= 1
        )
        # A pick can legitimately break during the diamond haul.  On the next
        # phase retry that must not make the controller replay the *initial*
        # smelting return forever.  Diamonds are durable evidence that the
        # starter kit existed and the deep-mining leg already ran.
        deep_haul_reached = self._total_owned(client, "minecraft:diamond") >= 5
        if essentials_ready or deep_haul_reached:
            print(
                "  Deep-mining kit/haul already reached; skipping the "
                "protected-smelting return so the descent can resume in place."
            )
            return True
        return self._return_to_base(client, state)

    _INTERIOR_PASSABLE = ("air", "cave_air", "grass", "snow", "torch")

    def _block_is_passable(self, client, x: int, y: int, z: int) -> bool:
        try:
            block = client.transport.dispatch("get_block", {"x": x, "y": y, "z": z})
        except Exception:
            return False
        return any(
            token in str(block.get("id", "")) for token in self._INTERIOR_PASSABLE
        )

    def _clear_doorway_entry(self, client, entry: tuple) -> bool:
        """Dig out the block just inside the door when it is sealed shut.

        The doorway is the only opening in the starter house, and the wall row
        leaves no diagonal around the block behind it -- so if that block is
        solid the interior is simply unreachable, no matter which interior
        target is chosen. Live: Bot10's (87, 64, 164) was dirt, and "Return to
        base for protected smelting" failed 500+ times while the bot stood at
        its own open front door. ``allowBreak`` is disabled for the approach
        (so Baritone will not tunnel through walls), which also stops it
        clearing this block, so break it directly via dig_block instead.
        """
        x, y, z = entry
        for offset in (0, 1):  # body slot, then head slot
            target_y = y + offset
            if self._block_is_passable(client, x, target_y, z):
                continue
            print(f"  Doorway entry {(x, target_y, z)} is sealed; clearing it...")
            try:
                client.transport.dispatch(
                    "look_at", {"x": x + 0.5, "y": target_y + 0.5, "z": z + 0.5}
                )
                client.transport.dispatch(
                    "dig_block",
                    {"x": x, "y": target_y, "z": z, "max_ticks": 160},
                )
            except Exception as exc:
                print(f"  Could not clear doorway entry: {exc}")
                return False
            deadline = time.time() + 10
            while time.time() < deadline:
                time.sleep(0.4)
                if self._block_is_passable(client, x, target_y, z):
                    break
            else:
                print(f"  Doorway entry {(x, target_y, z)} did not clear.")
                return False
        return True

    def _return_to_base(self, client, state: StateManager) -> bool:
        """Return to verified operational storage, then the house if needed."""
        structures = state.custom_data.get("structures", {})
        house = structures.get("starter_house", {})
        if house.get("supply_chest") is not None:
            try:
                storage = self._resolve_initial_iron_supply_chest(client, state)
            except Exception:
                storage = None
            if storage is not None:
                try:
                    current = self._read_state(
                        client, "Return to storage current state"
                    )
                except Exception:
                    current = None
                position = (
                    current.get("block_position", current.get("position", {}))
                    if current
                    else {}
                )
                distance = (
                    (float(position.get("x", storage[0])) - storage[0]) ** 2
                    + (float(position.get("y", storage[1])) - storage[1]) ** 2
                    + (float(position.get("z", storage[2])) - storage[2]) ** 2
                ) ** 0.5
                if current and distance <= 4.5:
                    try:
                        block = client.transport.dispatch(
                            "get_block",
                            {"x": storage[0], "y": storage[1], "z": storage[2]},
                        )
                    except Exception:
                        block = {}
                    if "chest" in str(block.get("id", "")):
                        print(f"  Already beside verified storage at {storage}.")
                        return True
                reached_storage = goto(
                    client,
                    storage[0],
                    storage[1],
                    storage[2],
                    timeout=300,
                    check_interval=1.0,
                    tolerance=4.5,
                )
                if reached_storage:
                    transport = getattr(client, "transport", None)
                    if transport is None:
                        # Lightweight callers can supply an already-verified
                        # resolver without a live bridge.
                        return True
                    try:
                        block = transport.dispatch(
                            "get_block",
                            {"x": storage[0], "y": storage[1], "z": storage[2]},
                        )
                    except Exception:
                        block = {}
                    if "chest" in str(block.get("id", "")):
                        print(f"  Returned to verified storage at {storage}.")
                        return True
                    print(
                        f"  Storage target {storage} is not operational; "
                        "falling back to the starter house."
                    )

        origin = house.get("origin") or state.custom_data.get("base_location")
        if not isinstance(origin, (list, tuple)) or len(origin) != 3:
            print("  No checkpointed starter-house location; refusing blind waypoint travel.")
            return False

        x, y, z = (int(value) for value in origin)
        target = (x + 3, y + 1, z + 3)
        interior_bounds = (
            range(x + 1, x + 6),
            range(z + 1, z + 6),
        )
        current = self._read_state(client, "Return to base current state")
        if current is None:
            return False
        position = current.get("block_position", current.get("position", {}))
        if (
            int(position.get("x", 0)) in interior_bounds[0]
            and int(position.get("y", 0)) == y + 1
            and int(position.get("z", 0)) in interior_bounds[1]
        ):
            print(f"  Already inside starter house near {target}.")
            return True

        # First approach the outside of the known doorway.  A single direct
        # underground-to-interior goal made Baritone tunnel through the floor
        # and exterior wall instead of using the door.
        door = house.get("door") or [x + 3, y + 1, z]
        door = tuple(int(value) for value in door)
        outside = (door[0], door[1], door[2] - 2)
        inside = (door[0], door[1], door[2] + 1)
        print(f"  Returning to starter-house doorway via {outside}...")
        if not goto(
            client,
            outside[0],
            outside[1],
            outside[2],
            timeout=300,
            check_interval=1.0,
            tolerance=2.0,
        ):
            # Exact distant GoalBlocks can be rejected while their chunk and
            # terrain are still unknown. Approach in short horizontal legs at
            # the bot's current standing height, then retry the exact doorway
            # once its chunk is loaded. Live 2026-08-03: Bot17 spent an hour at
            # (-145,66,-286) retrying the same unloaded y=104 doorway goal.
            origin_position = (
                int(position.get("x", 0)),
                int(position.get("y", 64)),
                int(position.get("z", 0)),
            )
            if not staged_goto(
                client,
                outside,
                origin_position,
                navigate=goto,
            ):
                return False

        client.transport.dispatch("cancel", {})
        client.transport.dispatch("chat", {"message": "#set allowBreak false"})
        time.sleep(0.25)
        entered = False
        try:
            door_block = client.transport.dispatch(
                "get_block", {"x": door[0], "y": door[1], "z": door[2]}
            )
            door_present = "door" in door_block.get("id", "")
            if not door_present:
                # A missing door is an OPEN doorway, not a blocker. Walk
                # straight in to smelt instead of failing the whole return: a
                # griefed or never-completed door previously dead-ended
                # FOOD_AND_IRON permanently ("no runnable objective remains").
                print(f"  No door at {door}; entering through the open doorway.")
            elif str(door_block.get("state", {}).get("open", "false")).lower() != "true":
                client.transport.dispatch(
                    "look_at",
                    {"x": door[0] + 0.5, "y": door[1] + 0.5, "z": door[2] + 0.5},
                )
                client.transport.dispatch(
                    "interact_block",
                    {"x": door[0], "y": door[1], "z": door[2]},
                )
                time.sleep(0.25)
            # The door can be open and the house still be sealed: the block
            # behind it may be solid, and the wall row leaves no diagonal
            # around it. Clear it before pathing, or the goal is unreachable.
            self._clear_doorway_entry(client, inside)
            entered = goto(
                client,
                inside[0],
                inside[1],
                inside[2],
                timeout=30,
                check_interval=0.5,
                # A tolerance above one block accepts standing in the open
                # doorway, which leaves the player exposed and prevents the
                # door from closing.  Require the interior block itself.
                tolerance=0.25,
            )
        finally:
            client.transport.dispatch("cancel", {})
            client.transport.dispatch("chat", {"message": "#set allowBreak true"})

        if not entered:
            # A 0.25 tolerance demands one exact block, but Baritone routinely
            # parks a block off inside the same room. The interior-bounds test
            # below is the real success criterion, and it already excludes the
            # doorway itself -- so the "don't stop in the open door" concern
            # that motivated the tight tolerance still holds. Hard-failing here
            # instead discarded genuine arrivals.
            print("  Exact interior block not reached; checking interior bounds...")

        arrived = self._read_state(client, "Return to base arrival check")
        if arrived is None:
            return False
        position = arrived.get("block_position", arrived.get("position", {}))
        safely_inside = (
            int(position.get("x", 0)) in interior_bounds[0]
            and int(position.get("y", 0)) == y + 1
            and int(position.get("z", 0)) in interior_bounds[1]
        )
        if safely_inside:
            door_block = client.transport.dispatch(
                "get_block", {"x": door[0], "y": door[1], "z": door[2]}
            )
            if str(door_block.get("state", {}).get("open", "false")).lower() == "true":
                client.transport.dispatch(
                    "interact_block",
                    {"x": door[0], "y": door[1], "z": door[2]},
                )
        return safely_inside

    def _deposit_excess_at_home(self, client, state: StateManager) -> bool:
        """Bank excess before smelting, recovering a missing supply chest.

        Depositing is an inventory-pressure optimization, not a hard
        precondition for descending -- but a persisted chest coordinate can be
        stale (chest never really placed, or destroyed), and treating that as
        a fatal phase failure deadlocks FOOD_AND_IRON forever. Confirmed live:
        Bot08 failed "Deposit bulky excess before smelting" 368 times while
        standing one block from an empty (-114,88,-35) that its checkpoint
        insisted held a chest. So: deposit; if that fails, re-establish the
        chest and retry; and if a deposit still isn't possible, continue
        carrying the excess rather than bricking the whole progression.
        """
        structures = state.custom_data.get("structures", {})
        house = structures.get("starter_house", {})
        chest_pos = house.get("supply_chest")
        if not isinstance(chest_pos, (list, tuple)) or len(chest_pos) != 3:
            print("  Starter-house supply chest is not checkpointed.")
            return True
        chest_pos = tuple(chest_pos)

        if deposit_excess_to_chest(client, chest_pos, state=self.state) >= 0:
            return True

        # Deposit failed. Re-establish the chest at its checkpointed spot and
        # retry once before giving up on this (optional) banking step.
        if self._reestablish_supply_chest(client, chest_pos):
            if deposit_excess_to_chest(client, chest_pos, state=self.state) >= 0:
                return True

        print(
            "  Could not deposit excess (chest unrecoverable); carrying it and "
            "continuing to the descent instead of failing the phase."
        )
        return True

    def _banked_count(self, client, item_id: str) -> int:
        if self.state is None:
            return 0
        try:
            from ...common.storage_catalog import catalog_for

            return catalog_for(client, self.state).item_count(item_id)
        except Exception as exc:
            print(f"  Storage count unavailable for {item_id}: {exc}")
            return 0

    def _total_owned(self, client, item_id: str) -> int:
        return count_item(client, item_id) + self._banked_count(client, item_id)

    def _bank_progression_at_home(
        self,
        client,
        state: StateManager,
        *,
        deposit_items=None,
        retain_counts=None,
    ) -> bool:
        """Require a verified deposit before another risky expedition."""
        if not self._return_to_base(client, state):
            return False
        house = state.custom_data.get("structures", {}).get("starter_house", {})
        chest = house.get("supply_chest")
        if not isinstance(chest, (list, tuple)) or len(chest) != 3:
            print("  Cannot bank progression: no checkpointed supply chest.")
            return False
        chest_pos = tuple(int(value) for value in chest)
        deposited = deposit_progression_to_chest(
            client,
            chest_pos,
            state=state,
            retain_counts=retain_counts,
            deposit_items=deposit_items,
        )
        if deposited >= 0:
            return True
        if not self._reestablish_supply_chest(client, chest_pos):
            print("  Progression banking blocked: supply chest could not be restored.")
            return False
        return deposit_progression_to_chest(
            client,
            chest_pos,
            state=state,
            retain_counts=retain_counts,
            deposit_items=deposit_items,
        ) >= 0

    def _withdraw_banked_iron(self, client, state: StateManager) -> bool:
        chest_pos = self._resolve_initial_iron_supply_chest(client, state)
        if chest_pos is None:
            return False
        result = withdraw_required_from_chest(
            client,
            chest_pos,
            {
                "minecraft:iron_ingot": self._IRON_BANK_TARGET,
                "minecraft:raw_iron": self._IRON_BANK_TARGET,
            },
            state=state,
        )
        return result >= 0

    def _bank_mining_progression(
        self,
        client,
        state: StateManager,
        *,
        deposit_items,
        retain_counts,
    ) -> bool:
        """Bank a mining haul nearby when the home commute is excessive."""
        home = self._resolve_initial_iron_supply_chest(client, state)
        snapshot = client.transport.dispatch("get_state", {})
        position = snapshot.get("block_position", {})
        current = (
            int(position.get("x", 0)),
            int(position.get("y", 0)),
            int(position.get("z", 0)),
        )

        if home is not None:
            horizontal_distance_sq = (
                (current[0] - home[0]) ** 2 + (current[2] - home[2]) ** 2
            )
            vertical_distance = abs(current[1] - home[1])
            if horizontal_distance_sq <= 48 ** 2 and vertical_distance <= 24:
                return self._bank_progression_at_home(
                    client,
                    state,
                    deposit_items=deposit_items,
                    retain_counts=retain_counts,
                )

        saved = (
            state.custom_data.get("storage", {}).get("mining_outpost_chest")
        )
        chest_pos = None
        if isinstance(saved, (list, tuple)) and len(saved) == 3:
            chest_pos = tuple(int(value) for value in saved)

        if chest_pos is not None:
            client.transport.dispatch(
                "goto",
                {"x": chest_pos[0], "y": chest_pos[1], "z": chest_pos[2]},
            )
            block = client.transport.dispatch(
                "get_block",
                {"x": chest_pos[0], "y": chest_pos[1], "z": chest_pos[2]},
            )
            if "chest" not in str(block.get("id", "")):
                chest_pos = None

        if chest_pos is None:
            from ...common.base import place_chest

            for dx, dz in (
                (1, 0),
                (-1, 0),
                (0, 1),
                (0, -1),
                (2, 0),
                (-2, 0),
            ):
                candidate = (current[0] + dx, current[1], current[2] + dz)
                block = client.transport.dispatch(
                    "get_block",
                    {"x": candidate[0], "y": candidate[1], "z": candidate[2]},
                )
                if "air" not in str(block.get("id", "")):
                    continue
                if place_chest(
                    client,
                    candidate[0],
                    candidate[1],
                    candidate[2],
                    purpose="mining_outpost",
                ):
                    chest_pos = candidate
                    state.custom_data.setdefault("storage", {})[
                        "mining_outpost_chest"
                    ] = list(candidate)
                    break

        if chest_pos is None:
            print("  Could not establish mining outpost storage; returning home.")
            return self._bank_progression_at_home(
                client,
                state,
                deposit_items=deposit_items,
                retain_counts=retain_counts,
            )

        deposited = deposit_progression_to_chest(
            client,
            chest_pos,
            state=state,
            retain_counts=retain_counts,
            deposit_items=deposit_items,
        )
        if deposited < 0:
            print("  Mining outpost deposit failed; refusing to risk the haul.")
            return False
        return True

    def _reestablish_supply_chest(self, client, chest_pos: Tuple[int, int, int]) -> bool:
        """Re-place the supply chest if its checkpointed block is genuinely
        gone. Returns True only when a chest verifiably occupies chest_pos."""
        cx, cy, cz = (int(v) for v in chest_pos)

        block = client.transport.dispatch(
            "get_block", {"x": cx, "y": cy, "z": cz}
        ).get("id", "")
        if "chest" in block:
            return True

        # Get within interaction range so the block read reflects a loaded
        # chunk, not a void_air placeholder, before deciding it's really gone.
        state = self._read_state(client, "Reestablish supply chest distance check")
        if state is None:
            return False
        pos = state.get("block_position", state.get("position", {}))
        near = all(axis in pos for axis in ("x", "y", "z")) and (
            (float(pos["x"]) - cx) ** 2
            + (float(pos["y"]) - cy) ** 2
            + (float(pos["z"]) - cz) ** 2
        ) ** 0.5 <= 4.5
        if not near and not harness_ops.move_near(client, cx, cy, cz, timeout=30.0):
            print("  Could not reach supply-chest location to re-place it.")
            return False

        block = client.transport.dispatch(
            "get_block", {"x": cx, "y": cy, "z": cz}
        ).get("id", "")
        if "chest" in block:
            return True
        if block == "minecraft:void_air":
            # Chunk still not loaded -- do not conclude the chest is gone.
            return False

        if count_item(client, "minecraft:chest") < 1 and not craft(
            client, "minecraft:chest", 1
        ):
            print("  No chest available and could not craft one to re-place.")
            return False

        print(f"  Re-placing missing supply chest at {(cx, cy, cz)}.")
        client.transport.dispatch("chat", {"message": "#set allowBreak false"})
        try:
            harness_ops.place_block_exact(
                client, cx, cy, cz, "minecraft:chest", allow_break=False
            )
        finally:
            client.transport.dispatch("cancel", {})
            client.transport.dispatch("chat", {"message": "#set allowBreak true"})

        block = client.transport.dispatch(
            "get_block", {"x": cx, "y": cy, "z": cz}
        ).get("id", "")
        return "chest" in block

    def _normalize_position(self, value) -> Optional[Tuple[int, int, int]]:
        if not isinstance(value, (list, tuple)) or len(value) != 3:
            return None
        try:
            return (int(value[0]), int(value[1]), int(value[2]))
        except (TypeError, ValueError):
            return None

    def _resolve_initial_iron_supply_chest(
        self,
        client,
        state: Optional[StateManager] = None,
    ) -> Optional[Tuple[int, int, int]]:
        current_state = state or self.state
        if current_state is None:
            return None

        structures = current_state.custom_data.get("structures", {})
        house = structures.get("starter_house", {})
        explicit = self._normalize_position(house.get("supply_chest"))
        if explicit is not None:
            return explicit
        return resolve_storage_location(client, state=current_state, verify=True)

    def _withdraw_initial_iron_supplies(self, client) -> None:
        if self._initial_iron_supplies_withdrawn:
            return

        self._initial_iron_supplies_withdrawn = True
        if self.state is None:
            return

        carried_ingots = count_item(client, "minecraft:iron_ingot")
        carried_raw = count_item(client, "minecraft:raw_iron")
        shortfall = max(0, self._INITIAL_IRON_TARGET - (carried_ingots + carried_raw))
        if shortfall > 0:
            withdraw_required_from_catalog(
                client,
                {"minecraft:raw_iron": shortfall},
                state=self.state,
            )
            carried_raw = count_item(client, "minecraft:raw_iron")
            carried_ingots = count_item(client, "minecraft:iron_ingot")
            remaining = max(0, self._INITIAL_IRON_TARGET - (carried_raw + carried_ingots))
            if remaining > 0:
                withdraw_required_from_catalog(
                    client,
                    {"minecraft:iron_ingot": remaining},
                    state=self.state,
                )

        if count_item(client, "minecraft:bucket") < 1:
            withdraw_required_from_catalog(
                client,
                {"minecraft:bucket": 1},
                state=self.state,
            )

        if not any(
            count_item(client, pickaxe) > 0
            for pickaxe in (
                "minecraft:wooden_pickaxe",
                "minecraft:stone_pickaxe",
                "minecraft:iron_pickaxe",
            )
        ):
            withdraw_required_from_catalog(
                client,
                {"minecraft:stone_pickaxe": 1},
                state=self.state,
            )

    def _transition_to_iron_level_once(self, client) -> bool:
        if self._initial_iron_transitioned:
            return False
        print(
            "  Initial nearby iron search did not succeed; moving to safe "
            f"iron-bearing depth ({self._INITIAL_IRON_TRANSITION_Y}) before retrying"
        )
        if not go_to_y_level(client, self._INITIAL_IRON_TRANSITION_Y):
            return False
        self._initial_iron_transitioned = True
        return True

    def _mine_initial_iron(self, client) -> bool:
        """Mine just enough iron for basic tools (15 = pickaxe + bucket + spare)."""
        self._withdraw_initial_iron_supplies(client)

        ingots = count_item(client, "minecraft:iron_ingot")
        raw_iron = count_item(client, "minecraft:raw_iron")
        shortfall = max(0, self._INITIAL_IRON_TARGET - (ingots + raw_iron))
        if shortfall == 0:
            print(
                f"  Initial iron already satisfied "
                f"({ingots} ingots + {raw_iron} raw)."
            )
            return True
        if not self._reserve_inventory_space(client, minimum_free_slots=3):
            print("  Initial iron mining paused because safe inventory space is unavailable.")
            return False
        print(f"  Mining initial iron (need {shortfall} more raw ore)...")
        if gather_ores(client, "iron", count=shortfall, timeout=300):
            return True

        if not self._transition_to_iron_level_once(client):
            return False

        ingots = count_item(client, "minecraft:iron_ingot")
        raw_iron = count_item(client, "minecraft:raw_iron")
        shortfall = max(0, self._INITIAL_IRON_TARGET - (ingots + raw_iron))
        if shortfall <= 0:
            return True
        return gather_ores(client, "iron", count=shortfall, timeout=300)

    def _smelt_iron(self, client, force: bool = False) -> bool:
        """Smelt raw iron into ingots using furnace."""
        # Uses imports from file header: ensure_supplies, gather_stone, count_item

        # Check before constructing workshop infrastructure.  On resume the
        # previous run may already have collected every finished ingot.
        current_ingots = count_item(client, "minecraft:iron_ingot")
        raw_iron = count_item(client, "minecraft:raw_iron")

        # Recover a batch loaded by an interrupted previous run before using
        # inventory-only counts to decide that smelting is complete.
        nearby_furnace = find_nearby_block(
            client,
            ["minecraft:furnace", "minecraft:blast_furnace"],
            radius=10,
        )
        if nearby_furnace is not None and resume_active_furnace(
            client,
            nearby_furnace,
            "minecraft:raw_iron",
            "minecraft:iron_ingot",
            minimum_output=None if force else 6,
            timeout=600.0 if force else 120.0,
        ):
            current_ingots = count_item(client, "minecraft:iron_ingot")
            raw_iron = count_item(client, "minecraft:raw_iron")
        essentials_ready = (
            count_item(client, "minecraft:iron_pickaxe") >= 1
            and count_item(client, "minecraft:bucket") >= 1
        )
        durable_pickaxe = False
        if essentials_ready and raw_iron > 0 and not force:
            durable_pickaxe = remaining_pickaxe_durability(
                client,
                [
                    "minecraft:iron_pickaxe",
                    "minecraft:diamond_pickaxe",
                    "minecraft:netherite_pickaxe",
                ],
            ) >= 200
        if essentials_ready and durable_pickaxe:
            print(
                f"  Deferring {raw_iron} raw iron until the bulk-mining "
                "fuel pass is complete."
            )
            return True
        if raw_iron == 0:
            if current_ingots >= 6 or essentials_ready:
                print(f"  Iron already smelted ({current_ingots} ingots).")
                return True
            print("  No raw iron to smelt!")
            return False
        
        # First ensure we have a furnace (requires 8 cobblestone)
        nearby_furnace = find_nearby_block(
            client,
            ["minecraft:furnace", "minecraft:blast_furnace"],
            radius=10,
        )
        smelt_strategies = None
        if count_item(client, "minecraft:furnace") == 0 and nearby_furnace is None:
            print("  Need furnace - getting cobblestone...")
            gather_stone(client, count=16, timeout=120)
            ensure_supplies(client, {"minecraft:furnace": 1})
        elif nearby_furnace is not None:
            print(f"  Reusing nearby furnace at {nearby_furnace}.")
            furnace_pos = tuple(nearby_furnace)
            smelt_strategies = {
                "minecraft:iron_ingot": (
                    lambda _client, shortfall, pos=furnace_pos: _smelt_requirement_shortfall(
                        _client,
                        "minecraft:iron_ingot",
                        shortfall,
                        furnace_pos=pos,
                    )
                )
            }
        
        # ensure_supplies uses existing coal when available and otherwise
        # prepares daylight wood fuel.  It never sends an unarmored bot into
        # a cave solely to fuel the furnace.
        print(f"  Smelting {raw_iron} raw iron...")
        output_target = (
            current_ingots + raw_iron
            if force
            else max(current_ingots, 6)
        )
        return ensure_supplies(
            client,
            {"minecraft:iron_ingot": output_target},
            strategies=smelt_strategies,
        ).success

    def _craft_essential_iron(self, client) -> bool:
        """Carry enough iron-pick durability for descent and bulk mining."""
        mining_pickaxes = [
            "minecraft:stone_pickaxe",
            "minecraft:iron_pickaxe",
            "minecraft:diamond_pickaxe",
            "minecraft:netherite_pickaxe",
        ]
        # A fresh iron pickaxe is 250 durability, so a 350 reserve was
        # unsatisfiable by a single pick and forced crafting a SECOND iron
        # pickaxe every run - which then dead-locked on stick preparation and
        # trapped the phase. One working iron pickaxe (plus the stone backup
        # and the bucket) is enough to begin the descent; the bulk-mining step
        # crafts more picks from mined iron. Require a usable single pick.
        minimum_durability = 200

        def kit_ready() -> tuple[bool, int]:
            high_tier_picks = sum(
                count_item(client, pickaxe)
                for pickaxe in (
                    "minecraft:iron_pickaxe",
                    "minecraft:diamond_pickaxe",
                    "minecraft:netherite_pickaxe",
                )
            )
            durability = remaining_pickaxe_durability(client, mining_pickaxes)
            ready = (
                durability >= minimum_durability
                and count_item(client, "minecraft:bucket") >= 1
                and (
                    high_tier_picks > 0
                    or count_item(client, "minecraft:stone_pickaxe") == 0
                )
            )
            return ready, durability

        # Reconnects often resume beside an old workstation after the bucket
        # and fresh iron pickaxe have already been forged.  Do not make a
        # completed kit reach an inaccessible table merely to prove it is done.
        ready, durability = kit_ready()
        if ready:
            # Measuring total durability does not put the good pickaxe in the
            # bot's hand. Equip the one with the most remaining uses before
            # the descent, so a fresh pick sitting in the main inventory is
            # actually used instead of whatever happens to be selected.
            equip_best_pickaxe(client, mining_pickaxes)
            print(f"  Deep-mining tools ready ({durability} durability).")
            return True

        if not self._ensure_mining_workstation(client):
            return False

        if count_item(client, "minecraft:bucket") < 1:
            if not _craft_with_table(client, "minecraft:bucket", 1):
                return False

        for _attempt in range(3):
            ready, durability = kit_ready()
            if ready:
                print(f"  Deep-mining tools ready ({durability} durability).")
                return True

            print(
                f"  Preparing deep-mining reserve "
                f"({durability}/{minimum_durability} durability)..."
            )
            # ``ensure_supplies`` passes a shortfall to its strategy while
            # ``_craft_with_table`` expects an absolute inventory target.
            # Craft one verified pick at a time here so an existing damaged
            # pick cannot turn the request into a no-op loop.
            if not _craft_with_table(
                client,
                "minecraft:iron_pickaxe",
                count_item(client, "minecraft:iron_pickaxe") + 1,
            ):
                return False

        return kit_ready()[0]

    def _ensure_expedition_pickaxe(self, client) -> bool:
        """Restore a banked iron pick before committing to deep mining."""
        mining_pickaxes = [
            "minecraft:iron_pickaxe",
            "minecraft:diamond_pickaxe",
            "minecraft:netherite_pickaxe",
        ]
        if remaining_pickaxe_durability(client, mining_pickaxes) >= 200:
            return True
        if self.state is None:
            return False
        withdraw_required_from_catalog(
            client,
            {"minecraft:iron_pickaxe": 1},
            state=self.state,
            max_travel_distance=96.0,
        )
        return remaining_pickaxe_durability(client, mining_pickaxes) >= 200

    def _equip_affordable_pre_descent_armor(self, client) -> bool:
        """Turn available iron into protection before the hazardous descent."""
        if has_full_armor(client, minimum_material="iron"):
            return True
        if count_item(client, "minecraft:iron_ingot") < 24:
            return True

        state = self._read_state(client, "Pre-descent armor") or {}
        health = float(state.get("health", 20) or 0)
        if health >= 12.0:
            return self._craft_iron_armor(client) and self._equip_iron_armor(client)

        # At critical health, avoid broad ensure-supplies handlers that may
        # navigate for dependencies. Use a verified local workstation only.
        if not self._ensure_mining_workstation(client):
            return False
        for item_id in (
            "minecraft:iron_helmet",
            "minecraft:iron_chestplate",
            "minecraft:iron_leggings",
            "minecraft:iron_boots",
        ):
            if count_item(client, item_id) < 1 and not _craft_with_table(
                client, item_id, 1
            ):
                return False
        return self._equip_iron_armor(client)

    def _ensure_mining_workstation(self, client) -> bool:
        """Create a nearby survival crafting table without leaving the mine."""
        if not self._reserve_inventory_space(client, minimum_free_slots=2):
            return False
        nearby = find_nearby_block(
            client,
            ["minecraft:crafting_table"],
            radius=8,
        )
        if not harness_ops.available():
            print("  Functional crafting-table harness is unavailable.")
            return False

        if nearby is not None:
            if harness_ops.ensure_crafting_table_open(client, table_pos=nearby):
                _safe_close_screen(client, "nearby crafting table")
                return True
            print(f"  Nearby crafting table at {nearby} is unreachable; replacing it locally.")
            nearby = None

        if count_item(client, "minecraft:crafting_table") < 1:
            print("  Crafting a local deep-mining workstation...")
            if not craft(client, "minecraft:crafting_table", 1):
                return False

        if not harness_ops.ensure_crafting_table_open(client):
            return False
        _safe_close_screen(client, "new crafting table")
        return True

    def _reserve_inventory_space(self, client, minimum_free_slots: int) -> bool:
        """Prefer persistent base storage over throwing stacks at our feet.

        Player-dropped items become collectible again after a short delay. A
        stationary bot in its compact house was picking the discarded stack
        back up while the crafting table opened, making the result slot full
        again.  When the checkpointed supply chest is available, bank excess
        there first and use ground disposal only as a last resort.
        """
        required = max(0, int(minimum_free_slots))
        if free_inventory_slots(client) >= required:
            return True

        chest_pos = self._resolve_initial_iron_supply_chest(client)
        if chest_pos is not None:
            deposited = deposit_excess_to_chest(
                client,
                chest_pos,
                state=self.state,
            )
            if deposited >= 0 and free_inventory_slots(client) >= required:
                print(
                    f"  Reserved {required} crafting slot(s) in home storage."
                )
                return True

        return manage_inventory(client, minimum_free_slots=required)

    def _bulk_mine(self, client) -> bool:
        """Mine the smallest complete deep haul, stopping on first failure."""
        if self._deep_mining_objectives_complete(client):
            print("  Deep-mining objectives already complete; skipping ore search.")
            return True

        objectives = (
            ("diamond", "minecraft:diamond", 5),
            ("iron", "minecraft:raw_iron", self._IRON_BANK_TARGET),
        )
        for ore_type, carried_item, target in objectives:
            if ore_type == "iron":
                owned = (
                    self._total_owned(client, "minecraft:raw_iron")
                    + self._total_owned(client, "minecraft:iron_ingot")
                )
            else:
                owned = self._total_owned(client, carried_item)
            if owned >= target:
                continue

            carried = count_item(client, carried_item)
            absolute_target = carried + (target - owned)
            if not gather_ores(
                client,
                ore_type,
                count=absolute_target,
                timeout=600,
            ):
                client.transport.dispatch("cancel", {})
                return False

            if self.state is None:
                continue
            deposit_items = (
                {"minecraft:raw_iron", "minecraft:iron_ingot"}
                if ore_type == "iron"
                else {"minecraft:diamond"}
            )
            retain_counts = {item_id: 0 for item_id in deposit_items}
            if not self._bank_mining_progression(
                client,
                self.state,
                deposit_items=deposit_items,
                retain_counts=retain_counts,
            ):
                client.transport.dispatch("cancel", {})
                return False
        return True

    def _craft_iron_armor(self, client) -> bool:
        """Craft full iron armor set."""
        if not self._reserve_inventory_space(client, minimum_free_slots=2):
            return False
        return ensure_supplies(client, {
            "minecraft:iron_helmet": 1,
            "minecraft:iron_chestplate": 1,
            "minecraft:iron_leggings": 1,
            "minecraft:iron_boots": 1,
        }).success

    def _equip_iron_armor(self, client) -> bool:
        """Equip the crafted set and fail closed unless bridge state proves it."""
        equip_best_armor(client)
        if has_full_armor(client, minimum_material="iron"):
            print("  Full iron-or-better armor is equipped and verified.")
            return True
        print("  Armor pieces were crafted but are not fully equipped.")
        return False

    def _craft_iron_tools(self, client) -> bool:
        """Craft iron tools."""
        return ensure_supplies(client, {
            "minecraft:iron_pickaxe": 1,
            "minecraft:iron_sword": 1,
            "minecraft:iron_axe": 1,
            "minecraft:iron_shovel": 1,
        }).success
