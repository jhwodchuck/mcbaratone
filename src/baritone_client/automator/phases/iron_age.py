"""
Phase 2: Iron & Diamond Phase
"""

import time
from typing import Optional, Tuple

from ..phase_executor import PhaseHandler
from ..resource_manager import ResourceManager
from ..state_manager import StateManager
from ...common import TaskResult, SequentialTask, ActionTask
from ...common.resources import (
    _craft_with_table,
    ensure_supplies,
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
    equip_best_armor,
    free_inventory_slots,
    has_full_armor,
    resolve_storage_location,
    withdraw_required_from_chest,
)
from ...common.navigation import find_nearby_block, goto
from ...common import harness_ops
from ...common.combat import acquire_emergency_food, eat_until_hunger, recover_health

class FoodAndIronHandler(PhaseHandler):
    """Phase 2: Iron & Diamond mining - Hour 1-2."""
    _INITIAL_IRON_TARGET = 15
    _INITIAL_IRON_TRANSITION_Y = -58

    def __init__(self) -> None:
        self.state: Optional[StateManager] = None
        self._initial_iron_supplies_withdrawn = False
        self._initial_iron_transitioned = False
    
    def get_name(self) -> str:
        return "Iron & Diamond (Hour 1-2)"
    
    def execute(self, client, resources: ResourceManager, state: StateManager) -> TaskResult:
        self.state = state
        tasks = [
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
            
            # Phase 2b: Now mine deep diamonds with iron tools
            ActionTask("Dig to diamond level Y-58", self._dig_staircase),
            ActionTask("Mine diamonds & remaining iron", self._bulk_mine),
            ActionTask(
                "Return to base with mined valuables",
                lambda c: self._return_to_base(c, state),
            ),
            ActionTask("Smelt mined iron", lambda c: self._smelt_iron(c, force=True)),
            ActionTask("Craft full iron armor", self._craft_iron_armor),
            ActionTask("Equip and verify iron armor", self._equip_iron_armor),
            ActionTask("Craft iron tools", self._craft_iron_tools),
            ActionTask(
                "Deposit excess loot at home",
                lambda c: self._deposit_excess_at_home(c, state),
            ),
        ]
        
        executor = SequentialTask("Iron & Diamond", tasks)
        return executor.run(client)

    def _stabilize_hunger(self, client) -> bool:
        state = client.transport.dispatch("get_state", {})
        health = float(state.get("health", 20) or 0)
        if health < 12.0:
            print(f"  Health critical ({health:.1f}/20) - recovering before mining...")
            if not recover_health(client, minimum_health=12.0, timeout=10.0):
                if not acquire_emergency_food(
                    client,
                    minimum_health=12.0,
                    timeout=300.0,
                ):
                    return False
            state = client.transport.dispatch("get_state", {})
        food_level = int(state.get("food_level", state.get("food", 20)))
        if food_level >= 14:
            return True
        print(f"  Hunger low ({food_level}/20) - eating before mining...")
        return eat_until_hunger(client, minimum_food=14)

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

        result = ensure_supplies(
            client,
            {"minecraft:stone_pickaxe": 1},
            timeout=120,
        )
        if result.success:
            return True

        print("  Pickaxe craft lacked usable dependencies; gathering a wood reserve...")
        if not gather_wood(client, count=4, timeout=180):
            return False
        return ensure_supplies(
            client,
            {"minecraft:stone_pickaxe": 1},
            timeout=120,
        ).success

    def _dig_staircase(self, client) -> bool:
        """Dig a proper staircase down to Y-58."""
        if self._deep_mining_objectives_complete(client):
            print("  Deep-mining objectives already complete; skipping descent.")
            return True
        print("Digging staircase to Y-58...")
        return go_to_y_level(client, -58)

    def _deep_mining_objectives_complete(self, client) -> bool:
        """Recognize either an unspent haul or gear made from that haul.

        A resumed run may have already turned its iron into armor and tools.
        Looking only at remaining raw iron and ingots made the phase excavate a
        second staircase through the starter-house floor even though every
        progression output was already present.
        """
        if count_item(client, "minecraft:diamond") < 5:
            return False

        unspent_iron = (
            count_item(client, "minecraft:raw_iron")
            + count_item(client, "minecraft:iron_ingot")
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
        deep_haul_reached = count_item(client, "minecraft:diamond") >= 5
        if essentials_ready or deep_haul_reached:
            print(
                "  Deep-mining kit/haul already reached; skipping the "
                "protected-smelting return so the descent can resume in place."
            )
            return True
        return self._return_to_base(client, state)

    def _return_to_base(self, client, state: StateManager) -> bool:
        """Return to the checkpointed starter-house interior."""
        structures = state.custom_data.get("structures", {})
        house = structures.get("starter_house", {})
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
        current = client.transport.dispatch("get_state", {})
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
            return False

        arrived = client.transport.dispatch("get_state", {})
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
        """Make the checkpointed supply chest a required phase boundary."""
        structures = state.custom_data.get("structures", {})
        house = structures.get("starter_house", {})
        chest_pos = house.get("supply_chest")
        if not isinstance(chest_pos, (list, tuple)) or len(chest_pos) != 3:
            print("  Starter-house supply chest is not checkpointed.")
            return False
        deposited = deposit_excess_to_chest(
            client, tuple(chest_pos), state=self.state
        )
        return deposited >= 0

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

        chest_pos = self._resolve_initial_iron_supply_chest(client)
        self._initial_iron_supplies_withdrawn = True
        if chest_pos is None:
            return

        carried_ingots = count_item(client, "minecraft:iron_ingot")
        carried_raw = count_item(client, "minecraft:raw_iron")
        shortfall = max(0, self._INITIAL_IRON_TARGET - (carried_ingots + carried_raw))
        if shortfall > 0:
            withdraw_required_from_chest(
                client,
                chest_pos,
                {"minecraft:raw_iron": shortfall},
            )
            carried_raw = count_item(client, "minecraft:raw_iron")
            carried_ingots = count_item(client, "minecraft:iron_ingot")
            remaining = max(0, self._INITIAL_IRON_TARGET - (carried_raw + carried_ingots))
            if remaining > 0:
                withdraw_required_from_chest(
                    client,
                    chest_pos,
                    {"minecraft:iron_ingot": remaining},
                )

        if count_item(client, "minecraft:bucket") < 1:
            withdraw_required_from_chest(
                client,
                chest_pos,
                {"minecraft:bucket": 1},
            )

        if not any(
            count_item(client, pickaxe) > 0
            for pickaxe in (
                "minecraft:wooden_pickaxe",
                "minecraft:stone_pickaxe",
                "minecraft:iron_pickaxe",
            )
        ):
            withdraw_required_from_chest(
                client,
                chest_pos,
                {"minecraft:stone_pickaxe": 1},
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
        ):
            current_ingots = count_item(client, "minecraft:iron_ingot")
            raw_iron = count_item(client, "minecraft:raw_iron")
        essentials_ready = (
            count_item(client, "minecraft:iron_pickaxe") >= 1
            and count_item(client, "minecraft:bucket") >= 1
        )
        if essentials_ready and raw_iron > 0 and not force:
            print(
                f"  Deferring {raw_iron} raw iron until the bulk-mining "
                "fuel pass is complete."
            )
            return True
        if raw_iron == 0:
            if current_ingots >= 15 or essentials_ready:
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
        return ensure_supplies(
            client,
            {"minecraft:iron_ingot": current_ingots + raw_iron},
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
                client.transport.dispatch("close_screen", {})
                return True
            print(f"  Nearby crafting table at {nearby} is unreachable; replacing it locally.")
            nearby = None

        if count_item(client, "minecraft:crafting_table") < 1:
            print("  Crafting a local deep-mining workstation...")
            if not craft(client, "minecraft:crafting_table", 1):
                return False

        if not harness_ops.ensure_crafting_table_open(client):
            return False
        client.transport.dispatch("close_screen", {})
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
        """Mine remaining resources with iron pickaxe."""
        if self._deep_mining_objectives_complete(client):
            print("  Deep-mining objectives already complete; skipping ore search.")
            return True

        finished_ingots = count_item(client, "minecraft:iron_ingot")
        targets = [
            ("diamond", 5),
        ]
        raw_iron_target = max(0, 30 - finished_ingots)
        if raw_iron_target > 0:
            targets.append(("iron", raw_iron_target))
        for ore_type, count in targets:
            print(f"  Mining {ore_type} (target: {count})...")
            if not gather_ores(client, ore_type, count=count, timeout=600):
                print(f"  {ore_type} mining failed; stopping phase before next task.")
                try:
                    client.transport.dispatch("cancel", {})
                except Exception:
                    pass
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
