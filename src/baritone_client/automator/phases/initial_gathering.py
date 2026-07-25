"""
Initial Gathering Phase - Wood, stone, food, basic tools.
"""

import time

from ..phase_executor import PhaseHandler
from ..resource_manager import ResourceManager
from ..state_manager import Phase, StateManager
from ...common import gather_wood, gather_stone, find_item_slot, count_item, equip_best_weapon
from ...common.resources import LOG_BLOCKS, PLANK_ITEMS
from ...common.base import build_emergency_shelter, sleep_through_night, wait_for_safe_daylight
from ...common.combat import (
    acquire_emergency_food,
    eat_until_hunger,
    hunt_passive_mobs,
    hunt_mobs,
    recover_health,
)
from ...common.storage_catalog import catalog_for
from ...common.tasks import (
    ActionTask,
    SequentialTask,
    SurvivalRecoveryRequired,
    TaskResult,
)

# Modular Action Imports
from ...actions import (
    CombatAction,
    CraftingAction,
    InventoryAction,
    MovementAction,
    SequenceAction,
    WoodCollectionPhase,
    ToolProgressionPhase,
    StoneCollectionPhase,
    BedPreparationPhase,
    StorageSetupPhase,
    SurvivalPhase,
)
from ...core.interfaces import ActionContext


def gather_wool(client, timeout: int = 90) -> bool:
    """Gather 3 wool of the SAME color efficiently."""
    print("Action: Gathering wool (Hunting sheep for bed)...")
    from ...common.inventory import count_item
    from ...common.combat import hunt_mobs
    
    # Minecraft beds require 3 wool of the same color.
    wool_colors = [
        "white", "black", "gray", "light_gray", "brown", 
        "red", "orange", "yellow", "lime", "green", 
        "cyan", "light_blue", "blue", "purple", "magenta", "pink"
    ]
    
    current_wool_counts = {}
    best_color = "white"
    max_count = 0
    
    for color in wool_colors:
        id = f"minecraft:{color}_wool"
        # Standardize color name for some items if needed
        # (gray vs grey? Minecraft uses 'gray')
        c = count_item(client, id)
        current_wool_counts[color] = c
        if c >= 3:
            print(f"  Already have 3 {id}, skipping hunt.")
            return True
        if c > max_count:
            max_count = c
            best_color = color

    print(f"  Current best wool: {best_color} ({max_count}/3)")
    
    # Hunt animals for wool. prioritize the best color.
    # We include some alternatives in case the best one is rare nearby.
    result = hunt_mobs(
        client,
        mob_types=["sheep"],
        required_loot={f"minecraft:{best_color}_wool": 3},
        search_radius=120,
        timeout=timeout,
    )

    if not result.success and "Night detected" in result.reason:
        print("  Night detected. Waiting safely for dawn before resuming the sheep hunt...")
        client.transport.dispatch("cancel", {})
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            state = client.transport.dispatch("get_state", {})
            day_time = state.get("world_time", 0) % 24000
            if day_time < 12000:
                print("  Dawn reached. Resuming sheep hunt...")
                result = hunt_mobs(
                    client,
                    mob_types=["sheep"],
                    required_loot={f"minecraft:{best_color}_wool": 3},
                    search_radius=120,
                    timeout=timeout,
                )
                break
            time.sleep(5)
    
    # Re-check all colors in case we got a different set of 3
    for color in wool_colors:
        if count_item(client, f"minecraft:{color}_wool") >= 3:
            print(f"  Wool hunt successful! Found 3 {color}_wool.")
            return True

    print("  Wool hunt failed to get 3 matching wool (No sheep found or split colors?)")
    return False


def gather_leather(client) -> bool:
    """Defer optional leather until the starter shelter is established."""
    print("Action: Checking optional starter leather...")
    from ...common.inventory import count_item

    existing_leather = count_item(client, "minecraft:leather")
    if existing_leather >= 24:
        print(f"  Already have {existing_leather} leather, skipping hunt")
        return True

    # Initial gathering's job is to establish safe storage and shelter. Iron
    # armor replaces leather shortly afterward, while this optional hunt has
    # repeatedly pulled the player away from the new home. Defer it once the
    # survival essentials exist instead of creating another expedition.
    print(f"  Deferring leather armor ({existing_leather}/24 leather); shelter comes first.")
    return True


def craft_leather_armor(client) -> bool:
    """Craft leather armor pieces."""
    from ...common.inventory import count_item

    leather = count_item(client, "minecraft:leather")
    if leather < 24:
        print(f"  Deferring leather armor: only {leather}/24 leather available.")
        return True

    armor_pieces = [
        "minecraft:leather_helmet",
        "minecraft:leather_chestplate",
        "minecraft:leather_leggings",
        "minecraft:leather_boots"
    ]
    for piece in armor_pieces:
        # Keeping craft() usage for simple inventory crafting not requiring table
        # We could use self.crafting.craft if passed context, but this is a standalone function used in task list.
        # ActionTask calls it with client.
        # We'd need to refactor it to use context if we want Actions here.
        # For POC, mixing is fine.
        from ...common.inventory import craft
        if not craft(client, piece, 1):
            print(f"  Failed to craft {piece} (Skipping)")
    return True


class InitialGatheringHandler(PhaseHandler):
    """Handler for initial resource gathering phase using common library functions."""

    def get_name(self) -> str:
        return "Initial Gathering"

    def execute(self, client, resources: ResourceManager, state: StateManager) -> TaskResult:
        """
        Gather initial resources using modular action components.
        """
        self.state = state
        
        # Initialize Actions
        self.crafting = CraftingAction()
        self.combat = CombatAction()
        self.inventory = InventoryAction()
        self.movement = MovementAction()
        
        # Initialize Context
        self.context = ActionContext(client=client, state=state)
        self._storage_deposit_verified = False
        
        print("DEBUG: Checking phase_ready_result...")
        ready = resources.phase_ready_result(Phase.INITIAL_GATHERING, "Initial gathering already satisfied")
        if ready:
            print("DEBUG: Phase already ready, returning early")
            return ready

        # A paused inventory/menu screen prevents time, hunger, and health
        # recovery from advancing in single-player. Clear it before the safety
        # preflight and refuse gathering/building work while one hit from death.
        try:
            client.transport.dispatch("close_screen", {})
        except Exception:
            pass
        if not recover_health(client, minimum_health=12.0):
            if not acquire_emergency_food(client, minimum_health=12.0):
                raise SurvivalRecoveryRequired(
                    "initial gathering health remains below 12 after bounded recovery"
                )
        self._stabilize_gathering_hunger(client)

        print("DEBUG: Checking sleep_through_night...")
        # 0. Safety Check
        if not sleep_through_night(client):
             print("DEBUG: Sleep failed; holding position until safe daylight...")
             if not wait_for_safe_daylight(client):
                 return TaskResult.fail("Initial gathering could not reach safe daylight")

        print("DEBUG: Creating task list (Optimized Progression using Actions)...")

        # Define subtasks with optimized order
        tasks = [
            # 1. Start small: Get just enough wood for a pickaxe (4 logs = 16 planks -> table(4) + sticks(4) + pick(3))
            # Wood is an input to the early tools, not a reason to strand the
            # player indefinitely.  Recovered runs may already have working
            # stone tools and enough planks for a table/chest while no nearby
            # tree is reachable.  In that case continue to storage/base work
            # and let the base phase gather more wood opportunistically.
            ActionTask("Gather minimal wood", self._gather_minimal_wood),
            ActionTask("Craft wooden tools", self._craft_wooden_tools),
            
            # 2. Upgrade ASAP: Get just enough stone for stone pickaxe (3 cobble)
            ActionTask("Mine minimal stone", gather_stone, count=3),
            ActionTask("Craft stone pickaxe", self._craft_stone_pickaxe_only),
            
            # 3. Bulk Gather Stone (Fast with Stone Pick)
            # Thirty-two cobblestone is the concrete phase gate and is enough
            # for the starter shelter.  Mining to 64 here needlessly drove a
            # proven-good run back into a flooded shaft after the gate was met.
            ActionTask("Mine bulk stone", gather_stone, count=32),
            
            # 4. Get remaining tools (Axe for wood, Sword for food)
            ActionTask("Craft remaining stone tools", self._craft_remaining_stone_tools),
            
            # 5. Bulk Gather Wood (Fast with Stone Axe). This also gets us out
            # of a starter mine before we look for surface animals.
            ActionTask("Gather bulk wood", self._gather_bulk_wood),

            # 6. Establish storage before optional exploration.  A live run
            # carried the entire starter inventory hundreds of blocks looking
            # for sheep before it had any chest or home landmark.
            ActionTask("Setup storage", self._setup_storage),
            ActionTask("Deposit excess", self._deposit_excess),

            # 7. A bed is useful but not an endgame progression gate. If this
            # biome has no reachable sheep yet, defer it instead of retrying the
            # entire phase indefinitely.
            ActionTask("Gather wool for bed", self._gather_wool_optional),
            ActionTask("Craft bed", self._craft_bed_optional),
            
            # 8. Armor & Food
            ActionTask("Gather leather", gather_leather),
            ActionTask("Craft leather armor", craft_leather_armor),
            # .success: ActionTask coerces non-bool/non-TaskResult returns to
            # success, so pass the boolean through explicitly.
            ActionTask("Hunt food", self._hunt_food_optional),
        ]

        # Execute sequentially
        sequential_task = SequentialTask("Initial Gathering", tasks)
        result = sequential_task.run(client)

        if result.success:
            resources.refresh_inventory()
            summary = resources.get_summary()
            return TaskResult.ok("Initial gathering complete", inventory=summary["inventory"])

        # The task list is a means, not the goal. PHASE_REQUIREMENTS for this
        # phase is empty, so `missing` is always {} - previously we still
        # failed the phase over non-essential stragglers (e.g. the 16th log
        # while defense logic kept interrupting). If the core goals of this
        # phase are demonstrably met, advance instead of looping forever.
        missing = resources.check_phase_requirements(Phase.INITIAL_GATHERING)
        if not missing and self._core_goals_met(client):
            resources.refresh_inventory()
            summary = resources.get_summary()
            return TaskResult.ok(
                f"Initial gathering complete (core goals met; optional task incomplete: {result.reason})",
                inventory=summary["inventory"],
                partial=True,
            )

        return TaskResult.fail(f"Initial gathering failed: {result.reason}", missing=missing)

    def _core_goals_met(self, client) -> bool:
        """
        Concrete definition of "initial gathering is done enough to move on":
        stone-tier tools, a workable wood buffer, bulk cobblestone, and a
        verified storage chest that was opened for deposit during this run.
        """
        from ...common.inventory import resolve_storage_location

        try:
            stored = catalog_for(client, getattr(self, "state", None)).inventory_totals()
        except (OSError, RuntimeError, ValueError):
            stored = {}

        def owned(item_id: str) -> int:
            return count_item(client, item_id) + int(stored.get(item_id, 0) or 0)

        log_blocks = sum(owned(log) for log in LOG_BLOCKS)
        plank_total = sum(owned(plank) for plank in PLANK_ITEMS)
        wood_equivalent = (log_blocks * 4) + plank_total
        cobble = owned("minecraft:cobblestone")
        has_pickaxe = (
            owned("minecraft:stone_pickaxe") > 0
            or owned("minecraft:iron_pickaxe") > 0
        )
        has_cutter = (
            owned("minecraft:stone_axe") > 0
            or owned("minecraft:stone_sword") > 0
        )

        storage_pos = resolve_storage_location(
            client, state=getattr(self, "state", None), verify=True
        )
        deposit_verified = bool(
            getattr(self, "_storage_deposit_verified", False)
        )
        storage_ready = (
            storage_pos is not None
            and deposit_verified
        )
        # T1202 is an ownership gate, not a carried-inventory gate. Count live
        # inventory plus verified non-missing storage, but do not let the
        # existence of a chest substitute for the required 64 plank-equivalent
        # wood buffer.
        wood_ready = wood_equivalent >= 64
        met = (
            has_pickaxe
            and has_cutter
            and wood_ready
            and cobble >= 32
            and storage_ready
        )
        print(
            f"  Core goal check: pickaxe={has_pickaxe} cutter={has_cutter} "
            f"wood={wood_equivalent}/64 plank-equivalent cobble={cobble}/32 "
            f"storage={storage_pos} deposit_verified={deposit_verified} "
            f"-> {'MET' if met else 'NOT MET'}"
        )
        return met

    def _gather_minimal_wood(self, client) -> bool:
        """Gather tool wood, but do not block on unreachable trees."""
        planks = sum(count_item(client, item_id) for item_id in PLANK_ITEMS)
        has_tooling = any(
            count_item(client, item_id) > 0
            for item_id in (
                "minecraft:stone_pickaxe",
                "minecraft:iron_pickaxe",
                "minecraft:stone_axe",
                "minecraft:iron_axe",
            )
        )
        if has_tooling and planks >= 8:
            print(
                f"  Minimal wood already covered by tooling/materials "
                f"({planks} planks); skipping unreachable-tree hunt."
            )
            return True
        return gather_wood(client, count=4)

    def _gather_bulk_wood(self, client) -> bool:
        """Gather the full T1202 wood buffer with the stone axe."""
        if gather_wood(client, count=16):
            return True
        planks = sum(count_item(client, item_id) for item_id in PLANK_ITEMS)
        logs = sum(count_item(client, item_id) for item_id in LOG_BLOCKS)
        if (logs * 4) + planks >= 64:
            print(
                "  Bulk wood hunt ended after the acceptance buffer was met "
                f"({(logs * 4) + planks}/64 plank-equivalent)."
            )
            return True
        return False

    @staticmethod
    def _stabilize_gathering_hunger(client, minimum_food: int = 12) -> None:
        """Establish a working hunger margin or yield without charging a retry."""
        transport = getattr(client, "transport", None)
        if transport is None:
            return
        try:
            live = transport.dispatch("get_state", {})
        except Exception:
            return
        if not isinstance(live, dict):
            return
        nested = live.get("data")
        if isinstance(nested, dict):
            live = {**live, **nested}
        food = int(live.get("food_level", live.get("food", 20)) or 0)
        if food >= minimum_food:
            return
        print(f"  Hunger low ({food}/20) before gathering; recovering food...")
        if eat_until_hunger(client, minimum_food=minimum_food):
            return
        if acquire_emergency_food(
            client,
            minimum_health=12.0,
            minimum_food=minimum_food,
            timeout=120.0,
            max_exploration_distance=128.0,
        ):
            return
        raise SurvivalRecoveryRequired(
            f"initial gathering food remains below {minimum_food} after bounded recovery"
        )

    def _hunt_food_optional(self, client) -> bool:
        """Keep progression moving once a safe starter food buffer exists."""
        food_items = (
            "minecraft:apple", "minecraft:beef", "minecraft:cooked_beef",
            "minecraft:porkchop", "minecraft:cooked_porkchop",
            "minecraft:chicken", "minecraft:cooked_chicken",
            "minecraft:mutton", "minecraft:cooked_mutton",
            "minecraft:rabbit", "minecraft:cooked_rabbit",
            "minecraft:cod", "minecraft:cooked_cod",
            "minecraft:salmon", "minecraft:cooked_salmon",
            "minecraft:bread",
        )
        food_count = sum(count_item(client, item) for item in food_items)
        if food_count >= 6:
            print(f"  Starter food buffer ready ({food_count} items); returning home.")
            return True

        needed = max(1, 6 - food_count)
        result = self.combat.hunt_passive_mobs(
            self.context,
            target_count=needed,
            timeout=60,
        )
        if not result.success:
            print(f"  Optional food hunt ended: {result.reason}")
        return True

    def _ensure_crafting_table(self, client) -> bool:
        """Finds or places a crafting table and opens it (Using CraftingAction)."""
        return self.crafting.ensure_crafting_table(self.context)

    def _craft_wooden_tools(self, client) -> bool:
        """Craft wooden tools using Actions."""
        import time

        # Recovered bots can already have stone/iron tooling while carrying
        # only a small plank buffer.  Wooden tools are then unnecessary, and
        # attempting to craft them forces a pointless log hunt.
        if (
            self.inventory.count_item(self.context, "minecraft:stone_pickaxe") > 0
            or self.inventory.count_item(self.context, "minecraft:iron_pickaxe") > 0
        ):
            print("  Stone-tier tooling already present; skipping wooden tools.")
            return True
        
        if self.inventory.count_item(self.context, "minecraft:wooden_pickaxe") > 0:
            print("  Already have wooden pickaxe!")
            return True
        
        # Check existing planks
        planks = sum(self.inventory.count_item(self.context, p) for p in PLANK_ITEMS)
        print(f"  Existing planks: {planks}")
        
        if planks < 12:
            needed_planks = 12 - planks
            print(f"  Need {needed_planks} more planks...")
            # craft() verifies by plank-family count, so ask for the number of
            # PLANKS we want (the bridge converts logs of whatever wood it has).
            self.crafting.craft(self.context, "minecraft:oak_planks", needed_planks)
            time.sleep(0.3)
        
        planks = sum(self.inventory.count_item(self.context, p) for p in PLANK_ITEMS)
        if planks < 9:
             print(f"  Still not enough planks ({planks})")
             return False

        sticks = self.inventory.count_item(self.context, "minecraft:stick")
        if sticks < 4:
            print(f"  Crafting sticks...")
            self.crafting.craft(self.context, "minecraft:stick", 4)
            time.sleep(0.3)
        
        if not self.crafting.ensure_crafting_table(self.context):
            return False
            
        print("  Crafting wooden pickaxe...")
        result = self.crafting.craft(self.context, "minecraft:wooden_pickaxe", 1)
        client.transport.dispatch("close_screen", {}) # Ensure screen closed
        return result

    def _craft_stone_pickaxe_only(self, client) -> bool:
        """Craft just the stone pickaxe using Actions."""
        if self.inventory.count_item(self.context, "minecraft:stone_pickaxe") > 0:
            return True
            
        if not self.crafting.ensure_crafting_table(self.context):
            return False
            
        print("  Crafting stone pickaxe...")
        result = self.crafting.craft(self.context, "minecraft:stone_pickaxe", 1)
        client.transport.dispatch("close_screen", {})
        return result

    def _craft_bed(self, client) -> bool:
        """Craft a bed (tries all wool colors) using Actions."""
        # Generic check for ANY bed
        beds = ["minecraft:white_bed", "minecraft:black_bed", "minecraft:gray_bed", "minecraft:light_gray_bed", "minecraft:brown_bed", "minecraft:red_bed"]
        for bed in beds:
            if self.inventory.count_item(self.context, bed) > 0:
                print(f"  Already have bed ({bed})")
                return True

        wool_colors = [
            "white", "black", "gray", "light_gray", "brown", 
            "red", "orange", "yellow", "lime", "green", 
            "cyan", "light_blue", "blue", "purple", "magenta", "pink"
        ]
        
        for color in wool_colors:
            bed_id = f"minecraft:{color}_bed"
            wool_id = f"minecraft:{color}_wool"
            
            if self.inventory.count_item(self.context, wool_id) >= 3:
                print(f"  Found 3 {wool_id}, attempting to craft {bed_id}...")
                if self.crafting.ensure_crafting_table(self.context):
                    result = self.crafting.craft(self.context, bed_id, 1)
                    if result:
                        client.transport.dispatch("close_screen", {})
                        print(f"  Successfully crafted {bed_id}")
                        return True
        return False

    def _gather_wool_optional(self, client) -> bool:
        """Try one bounded sheep hunt, then continue progression without a bed."""
        bed_ids = (
            "minecraft:white_bed", "minecraft:black_bed",
            "minecraft:gray_bed", "minecraft:light_gray_bed",
            "minecraft:brown_bed", "minecraft:red_bed",
            "minecraft:orange_bed", "minecraft:yellow_bed",
            "minecraft:lime_bed", "minecraft:green_bed",
            "minecraft:cyan_bed", "minecraft:light_blue_bed",
            "minecraft:blue_bed", "minecraft:purple_bed",
            "minecraft:magenta_bed", "minecraft:pink_bed",
        )
        existing = next(
            (bed for bed in bed_ids if count_item(client, bed) > 0),
            None,
        )
        if existing:
            print(f"  Bed already ready ({existing}); skipping wool hunt.")
            return True
        if gather_wool(client, timeout=30):
            return True
        print("  No reachable matching sheep yet; deferring the optional bed.")
        return True

    def _craft_bed_optional(self, client) -> bool:
        """Craft a bed when wool is available without making it a phase gate."""
        if self._craft_bed(client):
            return True
        print("  Bed materials are not ready; continuing without a bed for now.")
        return True

    def _craft_remaining_stone_tools(self, client) -> bool:
        """Craft remaining stone tools using Actions."""
        import time
        
        need_sword = self.inventory.count_item(self.context, "minecraft:stone_sword") == 0
        need_axe = self.inventory.count_item(self.context, "minecraft:stone_axe") == 0
        
        needed_sticks = 0
        if need_sword: needed_sticks += 1
        if need_axe: needed_sticks += 2
        
        current_sticks = self.inventory.count_item(self.context, "minecraft:stick")
        if current_sticks < needed_sticks:
             print(f"  Not enough sticks (Have {current_sticks})")
             # Check planks
             planks = sum(self.inventory.count_item(self.context, p) for p in PLANK_ITEMS)
             
             if planks < 2:
                 self.crafting.craft(self.context, "minecraft:oak_planks", 1)
                 time.sleep(0.5)
             
             self.crafting.craft(self.context, "minecraft:stick", 4)
             time.sleep(0.5)

        if not (need_sword or need_axe):
             print("  Already have remaining stone tools!")
             return True

        if not self.crafting.ensure_crafting_table(self.context):
            return False
            
        success = True
        if need_sword:
             print("  Crafting stone sword...")
             if not self.crafting.craft(self.context, "minecraft:stone_sword", 1): success = False
        if need_axe:
             print("  Crafting stone axe...")
             if not self.crafting.craft(self.context, "minecraft:stone_axe", 1): success = False
             
        client.transport.dispatch("close_screen", {})
        self.inventory.equip_best_weapon(self.context)
        return success

    def _setup_storage(self, client) -> bool:
        """Craft/Place a chest and remember it."""
        from ...common.inventory import (
            count_item,
            craft,
            find_item_slot,
            persist_storage_location,
            resolve_storage_location,
        )
        from ...common.resources import LOG_TO_PLANKS
        import time

        existing = resolve_storage_location(
            client, state=getattr(self, "state", None), verify=True
        )
        if existing is not None:
            persist_storage_location(
                client, existing, state=getattr(self, "state", None)
            )
            print(f"  Verified existing storage at {existing}.")
            return True
            
        print("  Setting up storage system...")
        
        def prepare_planks(required: int) -> bool:
            """Convert any carried log families until the shared target is met."""
            current = sum(count_item(client, item) for item in PLANK_ITEMS)
            while current < required:
                progressed = False
                for log_id, plank_id in LOG_TO_PLANKS.items():
                    log_count = count_item(client, log_id)
                    if log_count <= 0:
                        continue
                    target = min(required, current + log_count * 4)
                    craft(client, plank_id, target)
                    refreshed = sum(
                        count_item(client, item) for item in PLANK_ITEMS
                    )
                    if refreshed > current:
                        current = refreshed
                        progressed = True
                    if current >= required:
                        return True
                if not progressed:
                    return False
            return True

        # A chest needs eight planks.  If no table item is carried, reserve an
        # additional four because ensure_crafting_table may have to craft one
        # before the chest recipe can run.  Previously the phase prepared
        # exactly eight, spent four on a table, then failed forever with five
        # mixed-family planks.  Select the actual carried log family rather
        # than hard-coding oak so recovered jungle/birch inventories work.
        planks = sum(count_item(client, p) for p in PLANK_ITEMS)
        plank_budget = 8 + (
            0 if count_item(client, "minecraft:crafting_table") > 0 else 4
        )
        if planks < plank_budget:
            print(
                "  Not enough planks for chest/table "
                f"({planks}/{plank_budget}), converting logs..."
            )
            # Check if we have logs!
            logs = sum(count_item(client, block) for block in LOG_BLOCKS)
            if logs == 0:
                 print("  No logs to convert to planks!")
                 return False

            if not prepare_planks(plank_budget):
                print(
                    f"  Failed to prepare {plank_budget} planks for storage"
                )
                return False
            time.sleep(1.0)
            # Force refresh to ensure client knows about planks
            client.transport.dispatch("get_inventory", {})
            time.sleep(0.5)
            planks = sum(count_item(client, p) for p in PLANK_ITEMS)
            if planks < plank_budget:
                print(
                    "  Storage preparation produced only "
                    f"{planks}/{plank_budget} planks"
                )
                return False
            
        # Craft Chest
        if count_item(client, "minecraft:chest") == 0:
            if not self._ensure_crafting_table(client):
                print("  Failed to ensure crafting table for storage")
                return False

            # A nearby table may have been reused, or four planks may have
            # been consumed to make one.  Verify the chest's own budget after
            # that operation and top it up from any remaining log family.
            planks = sum(count_item(client, p) for p in PLANK_ITEMS)
            if planks < 8 and not prepare_planks(8):
                print(f"  Only {planks}/8 planks remain after table setup")
                return False
            
            print("  Crafting chest...")
            if not craft(client, "minecraft:chest", 1):
                print("  Failed to craft chest")
                client.transport.dispatch("close_screen", {})
                return False
            client.transport.dispatch("close_screen", {})
            time.sleep(0.5)

        # Place Chest
        # Find spot near player
        state = client.transport.dispatch('get_state', {})
        pos = state.get('block_position', {})
        x, y, z = int(pos.get('x', 0)), int(pos.get('y', 0)), int(pos.get('z', 0))

        chest_pos = None

        # Preferred: harness placement (repositioning + retries + verification)
        from ...common import harness_ops
        if harness_ops.available():
            try:
                target = harness_ops.find_place_pos_near(client, x + 1, y, z)
                if target:
                    above = client.transport.dispatch(
                        'get_block',
                        {'x': target[0], 'y': target[1] + 1, 'z': target[2]},
                    ).get('id', '')
                    if above not in {'minecraft:air', 'minecraft:cave_air'}:
                        print(f"  Rejecting chest spot {target}: lid blocked by {above}")
                        target = None
                if target and harness_ops.place_block(client, target[0], target[1], target[2], "minecraft:chest"):
                    check = client.transport.dispatch('get_block', {'x': target[0], 'y': target[1], 'z': target[2]})
                    if 'chest' in check.get('id', ''):
                        chest_pos = tuple(target)
            except Exception as e:
                print(f"  Harness chest placement failed (falling back): {e}")

        # Native fallback: try a few spots
        for dx, dz in [] if chest_pos else [(1,0), (-1,0), (0,1), (0,-1), (2,0), (-2,0), (0,2), (0,-2)]:
            tx, ty, tz = x+dx, y, z
            check = client.transport.dispatch('get_block', {'x': tx, 'y': ty, 'z': tz})
            bid = check.get('id', '')
            above = client.transport.dispatch('get_block', {'x': tx, 'y': ty + 1, 'z': tz}).get('id', '')
            if ('air' in bid or 'grass' in bid) and ('air' in above):
                # Good spot
                slot = find_item_slot(client, "minecraft:chest")
                if slot is not None:
                    if slot >= 9:
                        client.transport.dispatch('select_slot', {'slot': 0})
                        client.transport.dispatch('inventory_click', {'slot': slot, 'type': 'PICKUP', 'button': 0})
                        time.sleep(0.1)
                        client.transport.dispatch('inventory_click', {'slot': 36, 'type': 'PICKUP', 'button': 0})
                        time.sleep(0.1)
                        client.transport.dispatch('inventory_click', {'slot': slot, 'type': 'PICKUP', 'button': 0})
                        client.transport.dispatch('select_slot', {'slot': 0})
                    else:
                        client.transport.dispatch('select_slot', {'slot': slot})
                        
                    time.sleep(0.3)
                    try:
                        client.transport.dispatch('place_block', {'x': tx, 'y': ty, 'z': tz, 'block': 'minecraft:chest'})
                    except Exception as e:
                        print(f"  Placement error at {tx, ty, tz}: {e}")
                        continue
                    time.sleep(0.5)
                    # Verify
                    check = client.transport.dispatch('get_block', {'x': tx, 'y': ty, 'z': tz})
                    if 'chest' in check.get('id', ''):
                        chest_pos = (tx, ty, tz)
                        break
        
        if chest_pos:
            print(f"  Storage initialized at {chest_pos}")
            if not persist_storage_location(
                client, chest_pos, state=getattr(self, "state", None)
            ):
                print("  Failed to persist verified storage location")
                return False
            
            # CRITICAL SAFETY: Blacklist chest so Baritone NEVER breaks it
            print("  Safeguard: Blacklisting chests from mining...")
            client.transport.dispatch("chat", {"message": "#blacklist minecraft:chest"})
            time.sleep(0.5)
            
            # Step away to ensure we aren't standing inside/on it
            print("  Stepping back from chest...")
            px, py, pz = chest_pos
            # Try to go to x-1 or x+1
            client.transport.dispatch("goto", {"x": px+1, "y": py, "z": pz})
            time.sleep(1.0)
            
            return True
            
        print("  Failed to place and verify storage chest")
        return False

    def _deposit_excess(self, client) -> bool:
        """Dump non-essential items to storage."""
        from ...common.inventory import dump_to_chest
        
        # Keep essentials. Wood must cover EVERY family: in a non-oak biome an
        # oak-only list dumps the logs this phase just gathered, and the
        # phase's own core-goal check (>=8 log-equivalents in inventory) then
        # fails forever - this looped an entire run in a birch biome.
        wool_colors = [
            "white", "black", "gray", "light_gray", "brown",
            "red", "orange", "yellow", "lime", "green",
            "cyan", "light_blue", "blue", "purple", "magenta", "pink",
        ]
        keep = [
            # Tools
            "minecraft:wooden_pickaxe", "minecraft:stone_pickaxe",
            "minecraft:stone_sword", "minecraft:stone_axe",
            "minecraft:crafting_table", "minecraft:furnace",
            # Resources
            "minecraft:coal", "minecraft:stick", "minecraft:torch",
            # Wood of every family (logs and planks), stone
            *LOG_BLOCKS,
            *PLANK_ITEMS,
            "minecraft:cobblestone",
            # Bed and bed materials (bed is crafted right before this deposit;
            # wool may be waiting for a deferred bed craft)
            *[f"minecraft:{color}_bed" for color in wool_colors],
            *[f"minecraft:{color}_wool" for color in wool_colors],
            # Food
            "minecraft:apple", "minecraft:cooked_beef", "minecraft:beef",
            "minecraft:cooked_porkchop", "minecraft:porkchop",
            "minecraft:cooked_chicken", "minecraft:chicken",
            "minecraft:cooked_mutton", "minecraft:mutton",
            "minecraft:cooked_rabbit", "minecraft:rabbit",
            "minecraft:cooked_salmon", "minecraft:salmon",
            "minecraft:cooked_cod", "minecraft:cod",
            "minecraft:bread", "minecraft:wheat"
        ]
        
        print("  Depositing excess items to storage...")
        deposited = dump_to_chest(
            client,
            keep_items=keep,
            state=getattr(self, "state", None),
        )
        self._storage_deposit_verified = deposited >= 0
        if deposited < 0:
            print("  Storage deposit could not be verified")
            return False
        print(f"  Storage deposit verified ({deposited} stacks moved)")
        return True
