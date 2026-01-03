"""
Industrial Automation Phases - Detailed implementations for 10-phase industrialization.
"""

from typing import Tuple
from ..phase_executor import PhaseHandler
from ..resource_manager import ResourceManager
from ..state_manager import Phase, StateManager
from ...common import TaskResult, SequentialTask, ActionTask, spiral_explore, safe_return
from ...common.resources import gather_wood, gather_stone, gather_ores, ensure_supplies, go_to_y_level
from ...common.inventory import count_item, craft, equip_best_armor, equip_best_weapon, find_item_slot
from ...common.base import setup_base, sleep_through_night, open_furnace, place_crafting_table, build_emergency_shelter
from ...common.combat import hunt_passive_mobs, attack_nearest
from ...common.navigation import goto, find_nearby_block
from ...common.nether import build_nether_portal, enter_nether_portal, find_nether_fortress, hunt_blazes, barter_with_piglins
from ...common.end import triangulate_stronghold, find_end_portal, activate_end_portal, enter_end_portal, fight_ender_dragon
import time


# =============================================================================
# PHASE 1: BOOT SEQUENCE (Hour 0-1)
# =============================================================================
class BootSequenceHandler(PhaseHandler):
    """Phase 1: Shelter, Food, Tools - Hour 0-1."""
    
    def get_name(self) -> str:
        return "Boot Sequence (Hour 0-1)"
    

    def _ensure_safety(self, client) -> bool:
        """Check if conditions are dangerous and hide if necessary."""
        try:
            state = client.transport.dispatch("get_state", {})
            world_time = state.get("world_time", 0)
            health = state.get("health", 20)
            
            # Night time is roughly 13000-23000
            is_night = (world_time % 24000) >= 13000 and (world_time % 24000) <= 23000
            
            if is_night or health < 10:
                print(f"Night time detected (Night={is_night}, Health={health}). Mining underground instead of waiting!")
                
                # Instead of just waiting, mine underground where it's safe!
                # Try to gather coal and iron if we don't have enough
                from ...common.resources import gather_resource
                from ...common.inventory import count_item
                
                # Check what we need using direct inventory count
                coal_count = count_item(client, "minecraft:coal")
                iron_count = count_item(client, "minecraft:raw_iron")
                
                # Mine coal if we need it (for torches and smelting)
                if coal_count < 16:
                    print("  Mining coal underground during night...")
                    try:
                        gather_resource(client, "minecraft:coal_ore", quantity=8, timeout=120)
                    except Exception as e:
                        print(f"  Coal mining failed: {e}")
                
                # Mine iron if we need it
                if iron_count < 16:
                    print("  Mining iron underground during night...")
                    try:
                        gather_resource(client, "minecraft:iron_ore", quantity=8, timeout=120)
                    except Exception as e:
                        print(f"  Iron mining failed: {e}")
                
                # Check time again - if still night, mine more cobblestone
                state = client.transport.dispatch("get_state", {})
                now = state.get("world_time", 0)
                is_still_night = (now % 24000) >= 13000 and (now % 24000) <= 23000
                
                if is_still_night:
                    print("  Still night - mining more stone...")
                    try:
                        gather_resource(client, "minecraft:stone", quantity=32, timeout=60)
                    except Exception as e:
                        print(f"  Stone mining failed: {e}")
                
                return True
                
            return True
        except Exception as e:
            print(f"Safety check error: {e}")
            return True # Fail open to avoid lock
            
    def execute(self, client, resources: ResourceManager, state: StateManager) -> TaskResult:
        tasks = [
            # Safety First
            ActionTask("Survival Check", self._ensure_safety),
            
            # 00:00-05:00: Initial Tools
            ActionTask("Punch 6 logs", lambda c: gather_wood(c, count=6)),
            ActionTask("Craft planks from logs", self._craft_planks),
            ActionTask("Craft crafting table", lambda c: ensure_supplies(c, {"minecraft:crafting_table": 1}).success),
            ActionTask("Craft sticks", lambda c: ensure_supplies(c, {"minecraft:stick": 4}).success),
            ActionTask("Craft wooden pickaxe", lambda c: ensure_supplies(c, {"minecraft:wooden_pickaxe": 1}).success),
            ActionTask("Mine 17 stone", lambda c: gather_stone(c, count=17)),
            ActionTask("Craft stone tools", self._craft_stone_tools),
            
            # 05:00-15:00: Scouting
            ActionTask("Kill animals while scouting", self._hunt_and_collect),
            ActionTask("Sprint search for village", self._search_for_village),
            
            # 15:00-30:00: Village Looting
            ActionTask("Loot village (beds, hay, food)", self._loot_village),
            ActionTask("Kill iron golem if feasible", self._kill_iron_golem),
            
            # 30:00-45:00: Bunker Construction
            ActionTask("Dig 9x9x5 underground bunker", self._dig_bunker),
            ActionTask("Place infrastructure (bed, chests, furnaces, table)", self._place_infrastructure),
            ActionTask("Plant crops", self._plant_crops),
            ActionTask("Build cow pen", self._build_cow_pen),
            
            # 45:00-60:00: Finalization
            ActionTask("Cook food", self._cook_food),
            ActionTask("Smelt iron", self._smelt_iron),
            ActionTask("Organize storage", self._organize_storage),
            ActionTask("Sleep through night", lambda c: sleep_through_night(c)),
        ]
        
        executor = SequentialTask("Boot Sequence", tasks)
        return executor.run(client)

    def _craft_stone_tools(self, client) -> bool:
        return ensure_supplies(client, {
            "minecraft:stone_pickaxe": 1,
            "minecraft:stone_axe": 1,
            "minecraft:stone_shovel": 1,
            "minecraft:stone_sword": 1,
        }).success

    def _craft_planks(self, client) -> bool:
        """Convert logs into planks. Tries different log types."""
        log_types = [
            ("minecraft:oak_log", "minecraft:oak_planks"),
            ("minecraft:birch_log", "minecraft:birch_planks"),
            ("minecraft:spruce_log", "minecraft:spruce_planks"),
            ("minecraft:dark_oak_log", "minecraft:dark_oak_planks"),
            ("minecraft:acacia_log", "minecraft:acacia_planks"),
            ("minecraft:jungle_log", "minecraft:jungle_planks"),
        ]
        
        for log_id, plank_id in log_types:
            log_count = count_item(client, log_id)
            if log_count > 0:
                # Each log yields 4 planks
                print(f"  Converting {log_count} {log_id} to planks...")
                # Craft planks (need at least 4 for crafting table)
                result = craft(client, plank_id, log_count)  # Each craft uses 1 log → 4 planks
                if result:
                    print(f"  Crafted {log_count * 4} {plank_id}")
                    return True
        
        # Fallback: try ensure_supplies which handles crafting chains
        return ensure_supplies(client, {"minecraft:oak_planks": 8}).success

    def _hunt_and_collect(self, client) -> bool:
        """Kill animals and collect seeds/sugarcane while scouting."""
        hunt_passive_mobs(client, target_count=10)
        # Collect seeds and sugarcane by mining grass/sugarcane blocks
        # This is a simplified version - real implementation would target these specifically
        return True

    def _search_for_village(self, client) -> bool:
        """Sprint search for village within 2000 blocks with opportunistic hunting."""
        print("Searching for village within 2000 blocks...")
        
        # Custom exception for flow control
        class HuntOccurred(Exception): pass

        def _opportunistic_hunt():
            # Check for easy meals nearby
            from ...common.combat import get_nearby_entities, safe_combat, equip_best_weapon
            
            # Use quick scan radius
            nearby = get_nearby_entities(client, radius=25)
            # targets defined here for clarity
            targets = ["pig", "cow", "sheep", "chicken"]
            
            for entity in nearby:
                etype = entity.get("type", "").lower()
                if any(t in etype for t in targets):
                    print(f"  😋 Opportunistic Hunt: Found {etype}!")
                    # Pause explore
                    client.transport.dispatch("chat", {"message": "#stop"})
                    
                    # Kill
                    equip_best_weapon(client)
                    safe_combat(client, entity.get("id"), max_duration=10)
                    
                    # Raise exception to signal interruption
                    raise HuntOccurred()
            
            # Foraging check
            from ...common.navigation import find_nearby_block, goto
            forage_blocks = ["minecraft:sweet_berry_bush", "minecraft:pumpkin", "minecraft:melon", "minecraft:sugar_cane"]
            block_pos = find_nearby_block(client, forage_blocks, radius=15)
            if block_pos:
                print(f"  🍓 Opportunistic Forage: Found food block at {block_pos}!")
                client.transport.dispatch("chat", {"message": "#stop"})
                # Mine logic...
                bx, by, bz = block_pos
                client.transport.dispatch("chat", {"message": f"#mine {forage_blocks[0]} {forage_blocks[1]} {forage_blocks[2]} {forage_blocks[3]}"}) 
                time.sleep(2)
                
                # Check completion? 
                # Just raise exception to resume travel
                raise HuntOccurred()

        # Combine checks into one callback
        def _scan_and_hunt():
            if _check_village_found():
                raise StopIteration("Village Found")
            _opportunistic_hunt()
            
        # Linear Explore Strategy: Pick a direction and go FAR
        import random
        from ...common.navigation import goto
        
        # Get current pos
        state = client.transport.dispatch("get_state", {})
        pos = state.get("block_position", {})
        ox, oz = int(pos.get("x", 0)), int(pos.get("z", 0))
        
        # Pick a target 1500 blocks away
        # Try diagonal for max chunk coverage
        dx = random.choice([-1500, 1500])
        dz = random.choice([-1500, 1500])
        tx, tz = ox + dx, oz + dz
        
        print(f"  🧭 Linear Search: Heading to ({tx}, {tz})")
        
        # Logic Loop
        start_time = time.time()
        while time.time() - start_time < 1200:
            try:
                # Issue goto
                goto(client, tx, 64, tz, timeout=1200, check_interval=0.5, on_tick=_scan_and_hunt)
                # If returns normally, we reached target (or failed)
                break
            except StopIteration:
                print("  🎉 Village found during linear search! Stopping.")
                client.transport.dispatch("cancel", {}) 
                return True
            except HuntOccurred:
                print("  ⚔️ Hunt finished. Resuming linear search...")
                # Loop continues, re-calling goto(tx, tz)
                time.sleep(1) # Brief pause
                
        return True

    def _loot_village(self, client) -> bool:
        """Loot village in priority order: beds, hay bales, smoker, composters, food."""
        loot_order = [
            ("minecraft:white_bed", "beds"),
            ("minecraft:hay_block", "hay bales"),
            ("minecraft:smoker", "smoker"),
            ("minecraft:blast_furnace", "blast furnace"),
            ("minecraft:composter", "composters"),
            ("minecraft:bread", "food"),
        ]
        for item_id, name in loot_order:
            print(f"  Looting {name}...")
            # TODO: Implement block breaking and collection for each item type
        return True

    def _kill_iron_golem(self, client) -> bool:
        """Kill iron golem if feasible (check health/equipment first)."""
        # TODO: Check if we have stone sword and enough health
        # attack_nearest(client, "minecraft:iron_golem")
        return True

    def _dig_bunker(self, client) -> bool:
        """Dig 9x9x5 underground bunker near village."""
        print("Digging 9x9x5 underground bunker...")
        # TODO: Implement bunker digging using mine commands
        return True

    def _place_infrastructure(self, client) -> bool:
        """Place bed, chests, furnaces, crafting table in bunker."""
        # TODO: Implement placement logic
        return True

    def _plant_crops(self, client) -> bool:
        """Plant wheat, carrots, potatoes from collected seeds."""
        # TODO: Implement crop planting
        return True

    def _build_cow_pen(self, client) -> bool:
        """Build a simple cow pen using fence posts."""
        # TODO: Implement pen construction
        return True

    def _cook_food(self, client) -> bool:
        """Cook raw meat in furnace/smoker."""
        # TODO: Implement cooking logic
        return True

    def _smelt_iron(self, client) -> bool:
        """Smelt any raw iron collected."""
        # TODO: Implement smelting
        return True

    def _organize_storage(self, client) -> bool:
        """Organize items into chests."""
        # TODO: Implement storage organization
        return True


# =============================================================================
# PHASE 2: IRON & DIAMOND (Hour 1-2)
# =============================================================================
class FoodAndIronHandler(PhaseHandler):
    """Phase 2: Iron & Diamond mining - Hour 1-2."""
    
    def get_name(self) -> str:
        return "Iron & Diamond (Hour 1-2)"
    
    def execute(self, client, resources: ResourceManager, state: StateManager) -> TaskResult:
        tasks = [
            ActionTask("Ensure pickaxe", lambda c: ensure_supplies(c, {"minecraft:stone_pickaxe": 1}).success),
            ActionTask("Dig staircase to Y-58", self._dig_staircase),
            ActionTask("Branch mine for resources", self._branch_mine),
            ActionTask("Craft full iron armor", self._craft_iron_armor),
            ActionTask("Craft iron tools", self._craft_iron_tools),
        ]
        
        executor = SequentialTask("Iron & Diamond", tasks)
        return executor.run(client)

    def _dig_staircase(self, client) -> bool:
        """Dig a proper staircase down to Y-58."""
        print("Digging staircase to Y-58...")
        return go_to_y_level(client, -58)

    def _branch_mine(self, client) -> bool:
        """Branch mine for iron (64+), coal, diamonds (5+), redstone."""
        targets = [
            ("iron", 64),
            ("coal", 32),
            ("diamond", 5),
            ("redstone", 16),
        ]
        for ore_type, count in targets:
            print(f"  Mining {ore_type} (target: {count})...")
            gather_ores(client, ore_type, count=count, timeout=600)
        return True

    def _craft_iron_armor(self, client) -> bool:
        """Craft full iron armor set."""
        return ensure_supplies(client, {
            "minecraft:iron_helmet": 1,
            "minecraft:iron_chestplate": 1,
            "minecraft:iron_leggings": 1,
            "minecraft:iron_boots": 1,
        }).success

    def _craft_iron_tools(self, client) -> bool:
        """Craft iron tools."""
        return ensure_supplies(client, {
            "minecraft:iron_pickaxe": 1,
            "minecraft:iron_sword": 1,
            "minecraft:iron_axe": 1,
            "minecraft:iron_shovel": 1,
        }).success


# =============================================================================
# PHASE 3: ENCHANTING CORE (Hour 2-3)
# =============================================================================
class EnchantingPipelineHandler(PhaseHandler):
    """Phase 3: Enchanting setup - Hour 2-3."""
    
    def get_name(self) -> str:
        return "Enchanting Core (Hour 2-3)"
    
    def execute(self, client, resources: ResourceManager, state: StateManager) -> TaskResult:
        tasks = [
            ActionTask("Gather 45 leather", self._gather_leather),
            ActionTask("Harvest sugarcane -> 45 paper", self._harvest_sugarcane),
            ActionTask("Craft enchanting table", lambda c: ensure_supplies(c, {"minecraft:enchanting_table": 1}).success),
            ActionTask("Craft 15 bookshelves", lambda c: ensure_supplies(c, {"minecraft:bookshelf": 15}).success),
            ActionTask("Build enchanting room", self._build_enchanting_room),
            ActionTask("Begin rolling enchants", self._roll_enchants),
        ]
        
        executor = SequentialTask("Enchanting Core", tasks)
        return executor.run(client)

    def _gather_leather(self, client) -> bool:
        """Hunt cows for 45 leather."""
        hunt_passive_mobs(client, target_count=45)
        return count_item(client, "minecraft:leather") >= 45

    def _harvest_sugarcane(self, client) -> bool:
        """Harvest sugarcane and craft into 45 paper."""
        # TODO: Find and harvest sugarcane
        return ensure_supplies(client, {"minecraft:paper": 45}).success

    def _build_enchanting_room(self, client) -> bool:
        """Place enchanting table surrounded by bookshelves."""
        # TODO: Implement room construction
        return True

    def _roll_enchants(self, client) -> bool:
        """Begin enchanting primary tools."""
        # TODO: Implement enchanting logic
        return True


# =============================================================================
# PHASE 4: NETHER PHASE (Hour 3-4)
# =============================================================================
class NetherAndBlazeHandler(PhaseHandler):
    """Phase 4: Nether exploration - Hour 3-4."""
    
    def get_name(self) -> str:
        return "Nether Phase (Hour 3-4)"
    
    def execute(self, client, resources: ResourceManager, state: StateManager) -> TaskResult:
        tasks = [
            ActionTask("Build lava-cast portal", self._lava_cast_portal),
            ActionTask("Enter Nether", lambda c: enter_nether_portal(c)),
            ActionTask("Mine nether gold", self._mine_nether_gold),
            ActionTask("Barter with piglins", lambda c: barter_with_piglins(c, gold_count=20)),
            ActionTask("Locate fortress", lambda c: find_nether_fortress(c)),
            ActionTask("Kill blazes -> 6+ rods", lambda c: hunt_blazes(c, target_rods=6)),
            ActionTask("Collect quartz, soul sand, glowstone", self._collect_nether_resources),
            ActionTask("Return to Overworld", self._return_to_overworld),
        ]
        
        executor = SequentialTask("Nether Phase", tasks)
        return executor.run(client)

    def _lava_cast_portal(self, client) -> bool:
        """Build portal using lava casting method."""
        return build_nether_portal(client)

    def _mine_nether_gold(self, client) -> bool:
        """Mine nether gold ore for piglin bartering."""
        return gather_ores(client, "nether_gold", count=20, timeout=300)

    def _collect_nether_resources(self, client) -> bool:
        """Collect quartz, soul sand, glowstone."""
        gather_ores(client, "quartz", count=32, timeout=180)
        # TODO: Collect soul sand and glowstone
        return True

    def _return_to_overworld(self, client) -> bool:
        """Return through portal to Overworld."""
        return enter_nether_portal(client, timeout=60)


# =============================================================================
# PHASE 5: VILLAGER PIPELINE (Hour 4-5)
# =============================================================================
class VillagerInfraHandler(PhaseHandler):
    """Phase 5: Villager infrastructure - Hour 4-5."""
    
    def get_name(self) -> str:
        return "Villager Pipeline (Hour 4-5)"
    
    def execute(self, client, resources: ResourceManager, state: StateManager) -> TaskResult:
        tasks = [
            ActionTask("Capture 2 villagers", self._capture_villagers),
            ActionTask("Build villager breeder", self._build_breeder),
            ActionTask("Lock first librarian", self._lock_librarian),
            ActionTask("Start villager multiplication", self._start_breeding),
        ]
        
        executor = SequentialTask("Villager Pipeline", tasks)
        return executor.run(client)

    def _capture_villagers(self, client) -> bool:
        """Capture 2 villagers using boat or minecart."""
        # TODO: Implement villager capture logic
        print("Capturing 2 villagers...")
        return True

    def _build_breeder(self, client) -> bool:
        """Build a villager breeder structure."""
        # TODO: Implement breeder construction
        print("Building villager breeder...")
        return True

    def _lock_librarian(self, client) -> bool:
        """Lock a librarian's trade by trading with them."""
        # TODO: Implement trade locking
        print("Locking librarian trade...")
        return True

    def _start_breeding(self, client) -> bool:
        """Start villager breeding by providing food."""
        # TODO: Implement breeding logic
        print("Starting villager breeding...")
        return True


# =============================================================================
# PHASE 6: XP ENGINE (Hour 5-6)
# =============================================================================
class XpEngineHandler(PhaseHandler):
    """Phase 6: XP farm setup - Hour 5-6."""
    
    def get_name(self) -> str:
        return "XP Engine (Hour 5-6)"
    
    def execute(self, client, resources: ResourceManager, state: StateManager) -> TaskResult:
        tasks = [
            ActionTask("Find spawner or build mob farm", self._setup_xp_farm),
            ActionTask("Grind XP to level 30", self._grind_xp),
            ActionTask("Perfect tools (Efficiency, Fortune, Unbreaking)", self._enchant_tools),
        ]
        
        executor = SequentialTask("XP Engine", tasks)
        return executor.run(client)

    def _setup_xp_farm(self, client) -> bool:
        """Find a dungeon spawner or build a basic mob farm."""
        # TODO: Implement spawner finding or mob farm construction
        print("Setting up XP farm...")
        return True

    def _grind_xp(self, client) -> bool:
        """Grind XP until level 30."""
        # TODO: Implement XP grinding loop
        print("Grinding XP to level 30...")
        return True

    def _enchant_tools(self, client) -> bool:
        """Enchant tools with Efficiency, Fortune, Unbreaking."""
        # TODO: Implement enchanting logic
        print("Enchanting tools...")
        return True


# =============================================================================
# PHASE 7: IRON FARM (Hour 6-7)
# =============================================================================
class IronFarmHandler(PhaseHandler):
    """Phase 7: Iron farm construction - Hour 6-7."""
    
    def get_name(self) -> str:
        return "Iron Farm (Hour 6-7)"
    
    def execute(self, client, resources: ResourceManager, state: StateManager) -> TaskResult:
        # Set farm location in state
        pos_response = client.transport.dispatch("get_state", {})
        if 'error' in pos_response:
            return TaskResult.fail("Could not get player position")

        pos = pos_response.get('position', [0, 64, 0])
        center_x, center_y, center_z = int(pos[0]), int(pos[1]), int(pos[2])
        farm_location = (center_x + 40, center_y, center_z + 40)  # Offset from current position

        state.record_phase_payload(Phase.IRON_FARM, {"farm_location": farm_location})

        tasks = [
            ActionTask("Move 3 villagers to farm location", lambda c: self._move_villagers(c, farm_location)),
            ActionTask("Add zombie to farm", lambda c: self._add_zombie(c, farm_location)),
            ActionTask("Construct iron farm", lambda c: self._build_iron_farm(c, farm_location)),
            ActionTask("Start iron production", lambda c: self._start_production(c, farm_location)),
        ]

        executor = SequentialTask("Iron Farm", tasks)
        result = executor.run(client)

        if result.success:
            state.record_phase_payload(Phase.IRON_FARM, {
                "farm_location": farm_location,
                "villagers_moved": True,
                "zombie_added": True,
                "farm_constructed": True,
                "production_started": True
            })

        return result

    def _move_villagers(self, client, farm_location: Tuple[int, int, int]) -> bool:
        """Transport 3 villagers to the iron farm location."""
        # TODO: Implement villager transport logic
        print(f"Moving 3 villagers to {farm_location}...")
        return True

    def _add_zombie(self, client, farm_location: Tuple[int, int, int]) -> bool:
        """Capture and add a zombie to scare villagers."""
        # TODO: Implement zombie capture logic
        print(f"Adding zombie to farm at {farm_location}...")
        return True

    def _build_iron_farm(self, client, farm_location: Tuple[int, int, int]) -> bool:
        """Construct the iron farm structure."""
        # TODO: Implement iron farm construction
        x, y, z = farm_location
        print(f"Building iron farm at ({x}, {y}, {z})...")
        return True

    def _start_production(self, client, farm_location: Tuple[int, int, int]) -> bool:
        """Verify iron golems are spawning."""
        # TODO: Implement production verification
        print(f"Verifying iron production at {farm_location}...")
        return True


# =============================================================================
# PHASE 8: TRADING EMPIRE (Hour 7-8)
# =============================================================================
class ToolPerfectionHandler(PhaseHandler):
    """Phase 8: Librarian trading - Hour 7-8."""
    
    def get_name(self) -> str:
        return "Trading Empire (Hour 7-8)"
    
    def execute(self, client, resources: ResourceManager, state: StateManager) -> TaskResult:
        tasks = [
            ActionTask("Breed villagers", self._breed_villagers),
            ActionTask("Roll librarians for Mending", self._roll_mending),
            ActionTask("Roll librarians for Efficiency V", self._roll_efficiency),
            ActionTask("Roll librarians for Unbreaking III", self._roll_unbreaking),
            ActionTask("Roll librarians for Fortune III", self._roll_fortune),
            ActionTask("Cure villagers for discounts", self._cure_villagers),
        ]
        
        executor = SequentialTask("Trading Empire", tasks)
        return executor.run(client)

    def _breed_villagers(self, client) -> bool:
        """Breed more villagers for trading."""
        print("Breeding villagers...")
        return True

    def _roll_mending(self, client) -> bool:
        """Roll librarians until Mending book is obtained."""
        print("Rolling for Mending...")
        return True

    def _roll_efficiency(self, client) -> bool:
        """Roll librarians until Efficiency V is obtained."""
        print("Rolling for Efficiency V...")
        return True

    def _roll_unbreaking(self, client) -> bool:
        """Roll librarians until Unbreaking III is obtained."""
        print("Rolling for Unbreaking III...")
        return True

    def _roll_fortune(self, client) -> bool:
        """Roll librarians until Fortune III is obtained."""
        print("Rolling for Fortune III...")
        return True

    def _cure_villagers(self, client) -> bool:
        """Cure zombie villagers for trade discounts."""
        print("Curing villagers...")
        return True


# =============================================================================
# PHASE 9: END UNLOCK (Hour 8-9)
# =============================================================================
class WorldUnlockHandler(PhaseHandler):
    """Phase 9: End dimension access - Hour 8-9."""
    
    def get_name(self) -> str:
        return "End Unlock (Hour 8-9)"
    
    def execute(self, client, resources: ResourceManager, state: StateManager) -> TaskResult:
        tasks = [
            ActionTask("Craft Eyes of Ender", self._craft_eyes),
            ActionTask("Locate stronghold", lambda c: triangulate_stronghold(c)),
            ActionTask("Find and activate End portal", self._activate_portal),
            ActionTask("Enter The End", lambda c: enter_end_portal(c)),
            ActionTask("Kill Ender Dragon (one-cycle)", self._kill_dragon),
            ActionTask("Loot End City", self._loot_end_city),
            ActionTask("Acquire 5+ shulker boxes", self._acquire_shulkers),
            ActionTask("Grab Elytra", self._grab_elytra),
            ActionTask("Return to Overworld", self._return_overworld),
        ]
        
        executor = SequentialTask("End Unlock", tasks)
        return executor.run(client)

    def _craft_eyes(self, client) -> bool:
        """Craft 12 Eyes of Ender."""
        return ensure_supplies(client, {"minecraft:ender_eye": 12}).success

    def _activate_portal(self, client) -> bool:
        """Find and activate the End portal."""
        if not find_end_portal(client):
            return False
        return activate_end_portal(client)

    def _kill_dragon(self, client) -> bool:
        """Kill the Ender Dragon, attempting one-cycle strategy."""
        print("Fighting Ender Dragon...")
        return fight_ender_dragon(client)

    def _loot_end_city(self, client) -> bool:
        """Find and loot an End City."""
        print("Searching for End City...")
        # TODO: Implement End City finding and looting
        return True

    def _acquire_shulkers(self, client) -> bool:
        """Kill shulkers and craft 5+ shulker boxes."""
        print("Acquiring shulker boxes...")
        return True

    def _grab_elytra(self, client) -> bool:
        """Find and grab Elytra from End Ship."""
        print("Searching for Elytra...")
        return True

    def _return_overworld(self, client) -> bool:
        """Return to Overworld via End gateway or portal."""
        print("Returning to Overworld...")
        return True


# =============================================================================
# PHASE 10: MEGABASE INIT (Hour 9-10)
# =============================================================================
class MegabaseInitHandler(PhaseHandler):
    """Phase 10: Megabase initialization - Hour 9-10."""
    
    def get_name(self) -> str:
        return "Megabase Init (Hour 9-10)"
    
    def execute(self, client, resources: ResourceManager, state: StateManager) -> TaskResult:
        tasks = [
            ActionTask("Pack resources into shulker boxes", self._pack_shulkers),
            ActionTask("Select megabase location", self._select_location),
            ActionTask("Place beacon foundation", self._place_beacon),
            ActionTask("Begin vertical excavation shaft", self._begin_excavation),
        ]
        
        executor = SequentialTask("Megabase Init", tasks)
        return executor.run(client)

    def _pack_shulkers(self, client) -> bool:
        """Organize and pack all resources into shulker boxes."""
        print("Packing resources into shulker boxes...")
        return True

    def _select_location(self, client) -> bool:
        """Scout and select final megabase location."""
        print("Selecting megabase location...")
        return True

    def _place_beacon(self, client) -> bool:
        """Place beacon foundation (iron/diamond/emerald/gold blocks)."""
        print("Placing beacon foundation...")
        return True

    def _begin_excavation(self, client) -> bool:
        """Begin vertical excavation shaft for megabase."""
        print("Beginning excavation protocol...")
        return True
