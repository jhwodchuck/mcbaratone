#!/usr/bin/env python3
"""
Spawn to Dragon - End-to-end Minecraft automation.

This script orchestrates a complete playthrough from spawn to defeating the Ender Dragon.
It uses the Baritone bridge for movement/mining and the common library for automation.

Usage:
    python spawn_to_dragon.py [--resume] [--phase PHASE]
    
Options:
    --resume    Resume from last checkpoint
    --phase     Start from specific phase (1-10)
"""

import argparse
import sys
import time
from pathlib import Path

# Add src to path for development
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from baritone_client import Client
from baritone_client.transport import TcpTransport
from baritone_client.common import (
    goto,
    gather_wood,
    gather_stone,
    gather_ores,
    go_to_y_level,
    has_items,
    count_item,
    get_inventory,
    craft,
    attack_nearest,
    heal_if_needed,
    hunt_passive_mobs,
    Task,
    TaskResult,
    SequentialTask,
    RetryTask,
    # Nether
    build_nether_portal,
    enter_nether_portal,
    find_nether_fortress,
    hunt_blazes,
    hunt_endermen,
    craft_eyes_of_ender,
    # End
    triangulate_stronghold,
    find_end_portal,
    activate_end_portal,
    enter_end_portal,
    fight_ender_dragon,
)
from baritone_client.common.base import setup_base, open_furnace
from baritone_client.common.inventory import find_item_slot
from baritone_client.automator.phase_executor import PhaseExecutor
from baritone_client.automator.resource_manager import ResourceManager
from baritone_client.automator.state_manager import StateManager, Phase
from baritone_client.automator.phases.initial_gathering import InitialGatheringHandler
from baritone_client.automator.phases.base_construction import BaseConstructionHandler
from baritone_client.automator.phases.iron_age import IronAgeHandler
from baritone_client.automator.phases.diamond_mining import DiamondMiningHandler
from baritone_client.automator.phases.nether_prep import NetherPrepHandler
from baritone_client.automator.phases.nether_travel import NetherTravelHandler
from baritone_client.automator.phases.ender_pearls import EnderPearlHandler
from baritone_client.automator.phases.stronghold import StrongholdHandler
from baritone_client.automator.phases.end_portal import EndPortalHandler
from baritone_client.automator.phases.dragon_fight import DragonFightHandler





# ============================================================
# Phase 4: Diamond Mining
# ============================================================

class GatherDiamondsTask(Task):
    @property
    def name(self) -> str:
        return "Mine Diamonds"
    
    def run(self, client) -> TaskResult:
        print("  Going to Y=-59 for diamonds...")
        go_to_y_level(client, y=-59, timeout=300)
        
        success = gather_ores(client, "diamond", count=20, timeout=900)
        if success:
            return TaskResult.ok("Gathered diamonds!")
        return TaskResult.fail("Could not gather enough diamonds")


class CraftDiamondGearTask(Task):
    @property
    def name(self) -> str:
        return "Craft Diamond Gear"
    
    def run(self, client) -> TaskResult:
        craft(client, "minecraft:diamond_pickaxe", 1)
        time.sleep(0.5)
        craft(client, "minecraft:diamond_sword", 1)
        return TaskResult.ok("Crafted diamond gear")


# ============================================================
# Phase 5: Nether Preparation
# ============================================================

class GatherObsidianTask(Task):
    @property
    def name(self) -> str:
        return "Gather Obsidian"
    
    def run(self, client) -> TaskResult:
        current = count_item(client, "minecraft:obsidian")
        if current >= 14:
            return TaskResult.ok(f"Already have {current} obsidian")
        
        client.transport.dispatch("mine", {
            "blocks": ["minecraft:obsidian"],
            "quantity": 14 - current,
        })
        
        timeout = 600
        start = time.time()
        while time.time() - start < timeout:
            obsidian = count_item(client, "minecraft:obsidian")
            if obsidian >= 14:
                client.transport.dispatch("cancel", {})
                return TaskResult.ok(f"Gathered {obsidian} obsidian")
            time.sleep(10)
        
        client.transport.dispatch("cancel", {})
        return TaskResult.fail("Could not gather enough obsidian")


class BuildNetherPortalTask(Task):
    @property
    def name(self) -> str:
        return "Build Nether Portal"
    
    def run(self, client) -> TaskResult:
        state = client.transport.dispatch("get_state", {})
        if state.get("status") != "ok":
            return TaskResult.fail("Could not get position")
        
        data = state.get("data", {})
        x = int(data.get("x", 0))
        y = int(data.get("y", 64))
        z = int(data.get("z", 0)) + 5
        
        success = build_nether_portal(client, x, y, z)
        if success:
            return TaskResult.ok("Built nether portal")
        return TaskResult.fail("Could not build portal")


# ============================================================
# Phase 6-10: Advanced Phases (Nether, Pearls, Dragon)
# ============================================================

class EnterNetherTask(Task):
    @property
    def name(self) -> str:
        return "Enter Nether"
    
    def run(self, client) -> TaskResult:
        success = enter_nether_portal(client, timeout=30)
        if success:
            return TaskResult.ok("Entered the Nether!")
        return TaskResult.fail("Could not enter nether")


class FindFortressTask(Task):
    @property
    def name(self) -> str:
        return "Find Nether Fortress"
    
    def run(self, client) -> TaskResult:
        coords = find_nether_fortress(client, max_distance=500, timeout=600)
        if coords:
            return TaskResult.ok(f"Found fortress at {coords}", coords=coords)
        return TaskResult.fail("Could not find fortress")


class HuntBlazesTask(Task):
    @property
    def name(self) -> str:
        return "Hunt Blazes"
    
    def run(self, client) -> TaskResult:
        rods = hunt_blazes(client, target_count=10, timeout=900)
        if rods >= 6:
            return TaskResult.ok(f"Collected {rods} blaze rods", rods=rods)
        return TaskResult.fail(f"Only got {rods} blaze rods")


class ReturnToOverworldTask(Task):
    @property
    def name(self) -> str:
        return "Return to Overworld"
    
    def run(self, client) -> TaskResult:
        success = enter_nether_portal(client, timeout=60)
        if success:
            state = client.transport.dispatch("get_dimension", {})
            dimension = state.get("data", {}).get("dimension", "")
            if "overworld" in dimension.lower():
                return TaskResult.ok("Returned to overworld")
        return TaskResult.fail("Could not return to overworld")


class HuntEndermenTask(Task):
    @property
    def name(self) -> str:
        return "Hunt Endermen"
    
    def run(self, client) -> TaskResult:
        pearls = hunt_endermen(client, target_count=16, timeout=1200)
        if pearls >= 12:
            return TaskResult.ok(f"Collected {pearls} ender pearls", pearls=pearls)
        return TaskResult.fail(f"Only got {pearls} ender pearls")


class CraftEyesTask(Task):
    @property
    def name(self) -> str:
        return "Craft Eyes of Ender"
    
    def run(self, client) -> TaskResult:
        eyes = craft_eyes_of_ender(client, count=12)
        if eyes >= 12:
            return TaskResult.ok(f"Crafted {eyes} eyes of ender", eyes=eyes)
        return TaskResult.fail(f"Only crafted {eyes} eyes")


class FindStrongholdTask(Task):
    @property
    def name(self) -> str:
        return "Find Stronghold"
    
    def run(self, client) -> TaskResult:
        coords = triangulate_stronghold(client)
        if coords:
            print(f"  Stronghold estimated at {coords}")
            goto(client, coords[0], 64, coords[1], timeout=600)
            return TaskResult.ok(f"Located stronghold", coords=coords)
        return TaskResult.fail("Could not locate stronghold")


class FindEndPortalTask(Task):
    @property
    def name(self) -> str:
        return "Find End Portal"
    
    def run(self, client) -> TaskResult:
        success = find_end_portal(client, timeout=300)
        if success:
            return TaskResult.ok("Found end portal")
        return TaskResult.fail("Could not find end portal")


class ActivatePortalTask(Task):
    @property
    def name(self) -> str:
        return "Activate End Portal"
    
    def run(self, client) -> TaskResult:
        success = activate_end_portal(client)
        if success:
            return TaskResult.ok("End portal activated!")
        return TaskResult.fail("Could not activate portal")


class EnterEndTask(Task):
    @property
    def name(self) -> str:
        return "Enter The End"
    
    def run(self, client) -> TaskResult:
        success = enter_end_portal(client, timeout=30)
        if success:
            return TaskResult.ok("Entered The End!")
        return TaskResult.fail("Could not enter The End")


class FightDragonTask(Task):
    @property
    def name(self) -> str:
        return "Fight Ender Dragon"
    
    def run(self, client) -> TaskResult:
        success = fight_ender_dragon(client, timeout=1800)
        if success:
            return TaskResult.ok("DRAGON DEFEATED!")
        return TaskResult.fail("Dragon fight failed")


# ============================================================
# Phase Creation Functions
# ============================================================



def create_phase_4() -> Task:
    """Phase 4: Diamond Mining"""
    return SequentialTask("Phase 4: Diamond Mining", [
        RetryTask(GatherDiamondsTask(), max_retries=2),
        CraftDiamondGearTask(),
    ])


def create_phase_5() -> Task:
    """Phase 5: Nether Preparation"""
    return SequentialTask("Phase 5: Nether Preparation", [
        RetryTask(GatherObsidianTask(), max_retries=2),
        BuildNetherPortalTask(),
    ])


def create_phase_6() -> Task:
    """Phase 6: Nether Travel"""
    return SequentialTask("Phase 6: Nether Travel", [
        EnterNetherTask(),
        RetryTask(FindFortressTask(), max_retries=2),
        RetryTask(HuntBlazesTask(), max_retries=3),
    ])


def create_phase_7() -> Task:
    """Phase 7: Ender Pearl Farming"""
    return SequentialTask("Phase 7: Ender Pearl Farming", [
        ReturnToOverworldTask(),
        RetryTask(HuntEndermenTask(), max_retries=3),
        CraftEyesTask(),
    ])


def create_phase_8() -> Task:
    """Phase 8: Stronghold Location"""
    return SequentialTask("Phase 8: Stronghold Location", [
        RetryTask(FindStrongholdTask(), max_retries=2),
        RetryTask(FindEndPortalTask(), max_retries=2),
    ])


def create_phase_9() -> Task:
    """Phase 9: End Portal"""
    return SequentialTask("Phase 9: End Portal", [
        ActivatePortalTask(),
        EnterEndTask(),
    ])


def create_phase_10() -> Task:
    """Phase 10: Dragon Fight"""
    return SequentialTask("Phase 10: Dragon Fight", [
        RetryTask(FightDragonTask(), max_retries=5),
    ])


class DeathRecoveryTask(Task):
    @property
    def name(self) -> str:
        return "Recover from Death"
    
    def run(self, client) -> TaskResult:
        print("  💀 Player is dead! Starting recovery...")
        
        # 1. Respawn
        client.transport.dispatch("respawn", {})
        time.sleep(2.0)
        
        # 2. Get death location
        response = client.transport.dispatch("get_death_location", {})
        if response.get("status") != "ok":
            return TaskResult.fail("Could not get death location")
        
        data = response.get("data", {})
        x, y, z = data.get("x"), data.get("y"), data.get("z")
        dim = data.get("dimension")
        
        if x is None:
            return TaskResult.fail("No death location recorded")
            
        print(f"  📍 Recorded death at ({x}, {y}, {z}) in {dim}")
        
        # 3. Handle dimension mismatch
        current_dim_resp = client.transport.dispatch("get_dimension", {})
        current_dim = current_dim_resp.get("data", {}).get("dimension", "")

        if current_dim != dim:
            print(f"  ⚠️ Death in {dim}, currently in {current_dim}. Need to travel back.")
            # Simple case: if death in nether, need to find a portal
            if dim == "minecraft:the_nether":
                print("  Recovery in Nether required. Finding portal from spawn...")
                # From Nether spawn, search for nearby portals
                success = self._find_nether_portal_from_spawn(client)
                if not success:
                    return TaskResult.fail("Could not find portal back to Overworld")
                # Now enter the portal to return to Overworld
                success = enter_nether_portal(client, timeout=30)
                if not success:
                    return TaskResult.fail("Could not enter portal to return to Overworld")
                print("  ✅ Returned to Overworld via portal")
            elif dim == "minecraft:overworld":
                print("  Death in Overworld. Proceeding.")
        
        # 4. Navigate to death location
        print(f"  🏃 Running to death location to recover items...")
        success = goto(client, int(x), int(y), int(z), timeout=600)
        
        if success:
            print("  ✅ Reached death location. Gathering items...")
            # Wait a bit for items to be picked up
            time.sleep(2.0)
            return TaskResult.ok("Recovered items")
        
        return TaskResult.fail("Failed to reach death location")

    def _find_nether_portal_from_spawn(self, client) -> bool:
        """Find a Nether portal from spawn coordinates in the Nether."""
        print("  🔍 Searching for Nether portal from spawn...")

        # Nether spawn is at (0, 0) in Nether coordinates (which corresponds to overworld 0,0)
        # Portals are usually built near spawn or in common locations
        # We'll search in a spiral pattern around common portal locations

        search_locations = [
            (0, 64, 0),      # Exact spawn
            (10, 64, 0),     # East of spawn
            (-10, 64, 0),    # West of spawn
            (0, 64, 10),     # South of spawn
            (0, 64, -10),    # North of spawn
            (20, 64, 0),     # Further east
            (-20, 64, 0),    # Further west
            (0, 64, 20),     # Further south
            (0, 64, -20),    # Further north
        ]

        for x, y, z in search_locations:
            print(f"  Checking portal at ({x}, {y}, {z})...")
            success = goto(client, x, y, z, timeout=60)
            if not success:
                continue

            # Look for portal blocks nearby
            state = client.transport.dispatch("get_state", {})
            if state.get("status") == "ok":
                # Check if we're standing in or near a portal
                # This is a simple check - in practice we'd scan blocks
                time.sleep(1.0)  # Wait for any portal effects

                # Try to detect if we're in a portal by checking dimension after a moment
                # If we're in a portal, entering it should trigger dimension change
                # But since we're already in Nether, we need to find the portal structure

                # For now, use a simple heuristic: check if there are obsidian blocks nearby
                # This is a placeholder - real implementation would scan for portal frame
                blocks_response = client.transport.dispatch("scan_blocks", {
                    "center": {"x": x, "y": y, "z": z},
                    "radius": 5,
                    "block_types": ["minecraft:obsidian", "minecraft:nether_portal"]
                })

                if blocks_response.get("status") == "ok":
                    blocks = blocks_response.get("data", {}).get("blocks", [])
                    obsidian_count = sum(1 for block in blocks if block.get("type") == "minecraft:obsidian")
                    portal_count = sum(1 for block in blocks if block.get("type") == "minecraft:nether_portal")

                    if obsidian_count >= 10 or portal_count > 0:
                        print(f"  🎯 Found portal structure at ({x}, {y}, {z}) - {obsidian_count} obsidian, {portal_count} portal blocks")
                        return True

        print("  ❌ No portal found in common locations")
        return False


# ============================================================
# Main Orchestrator
# ============================================================

PHASES = [
    ("Phase 1: Initial Gathering", None),
    ("Phase 2: Food and Shelter", None),
    ("Phase 3: Iron Tier", None),
    ("Phase 4: Diamond Mining", create_phase_4),
    ("Phase 5: Nether Preparation", create_phase_5),
    ("Phase 6: Nether Travel", create_phase_6),
    ("Phase 7: Ender Pearl Farming", create_phase_7),
    ("Phase 8: Stronghold Location", create_phase_8),
    ("Phase 9: End Portal", create_phase_9),
    ("Phase 10: Dragon Fight", create_phase_10),
]


def run_automation(client, start_phase: int = 1, resume: bool = False):
    """Run the full automation pipeline."""
    resources = ResourceManager(client)
    state_manager = StateManager()

    if resume:
        if state_manager.load_checkpoint():
            print("Resuming from checkpoint")
            # Set start_phase based on current phase
            phase_order = list(Phase)
            current_phase_index = phase_order.index(state_manager.get_current_phase())
            start_phase = current_phase_index + 1
        else:
            print("No checkpoint found, starting from phase 1")

    # Set starting phase
    phase_order = list(Phase)
    if start_phase <= len(phase_order):
        state_manager.set_phase(phase_order[start_phase - 1])

    # Create executor with handlers for all phases
    executor = PhaseExecutor(client, resources, state_manager)
    executor.register_handler(Phase.INITIAL_GATHERING, InitialGatheringHandler())
    executor.register_handler(Phase.BASE_CONSTRUCTION, BaseConstructionHandler())
    executor.register_handler(Phase.IRON_AGE, IronAgeHandler())
    executor.register_handler(Phase.DIAMOND_MINING, DiamondMiningHandler())
    executor.register_handler(Phase.NETHER_PREP, NetherPrepHandler())
    executor.register_handler(Phase.NETHER_TRAVEL, NetherTravelHandler())
    executor.register_handler(Phase.ENDER_PEARL_FARM, EnderPearlHandler())
    executor.register_handler(Phase.STRONGHOLD_LOCATE, StrongholdHandler())
    executor.register_handler(Phase.END_PORTAL, EndPortalHandler())
    executor.register_handler(Phase.DRAGON_FIGHT, DragonFightHandler())

    print("\n" + "=" * 60)
    print("  🎮 SPAWN TO DRAGON AUTOMATION 🐉")
    print("=" * 60)
    print(f"  Starting from Phase {start_phase}")
    print("=" * 60 + "\n")

    for i, (phase_name, create_fn) in enumerate(PHASES, start=1):
        if i < start_phase:
            print(f"⏭️  Skipping {phase_name}")
            continue

        print(f"\n{'─' * 60}")
        print(f"  {phase_name}")
        print(f"{'─' * 60}")

        # Check if dead before starting phase
        try:
            state_data = client.transport.dispatch("get_state", {}).get("data", {})
            if state_data.get("is_dead") or state_data.get("health", 20) <= 0:
                recovery = DeathRecoveryTask()
                recovery.run(client)
        except:
            pass

        try:
            # Simple health check
            if count_item(client, "minecraft:cooked_beef") > 0 or count_item(client, "minecraft:bread") > 0:
                heal_if_needed(client)
        except:
            pass

        phase_enum = {
            1: Phase.INITIAL_GATHERING,
            2: Phase.BASE_CONSTRUCTION,
            3: Phase.IRON_AGE,
            4: Phase.DIAMOND_MINING,
            5: Phase.NETHER_PREP,
            6: Phase.NETHER_TRAVEL,
            7: Phase.ENDER_PEARL_FARM,
            8: Phase.STRONGHOLD_LOCATE,
            9: Phase.END_PORTAL,
            10: Phase.DRAGON_FIGHT,
        }.get(i)

        success = False
        if phase_enum and executor.has_handler(phase_enum):
            success = executor.execute_phase(phase_enum)
            state_manager.advance_phase()
        elif create_fn is not None:
            # Use custom task
            phase_task = create_fn()
            result = phase_task.run(client)
            if result.data:
                print(f"    Details for {phase_name}:")
                for key, value in result.data.items():
                    print(f"      {key}: {value}")
            success = result.success
            if success:
                # Advance manually
                if i < len(phase_order):
                    state_manager.set_phase(phase_order[i])
            else:
                print(f"\n❌ Failed at {phase_name}: {result.reason}")
        else:
            # No handler or task, skip
            success = True
            state_manager.advance_phase()

        if not success:
            state_manager.save_checkpoint(resources.refresh_inventory())
            return False

        state_manager.save_checkpoint(resources.refresh_inventory())
        print(f"  ✅ {phase_name} complete!")

    print("\n" + "=" * 60)
    print("  🎉🐉 ENDER DRAGON DEFEATED! VICTORY! 🐉🎉")
    print("=" * 60 + "\n")

    return True


def main():
    parser = argparse.ArgumentParser(description="Spawn to Dragon Automation")
    parser.add_argument("--resume", action="store_true", help="Resume from checkpoint")
    parser.add_argument("--phase", type=int, default=1, help="Start from phase (1-10)")
    parser.add_argument("--host", default="localhost", help="Bridge host")
    parser.add_argument("--port", type=int, default=5555, help="Bridge port")
    
    args = parser.parse_args()
    
    print(f"Connecting to bridge at {args.host}:{args.port}...")
    
    try:
        transport = TcpTransport(host=args.host, port=args.port)
        client = Client(transport)
        
        print("Connected!")
        
        success = run_automation(client, start_phase=args.phase, resume=args.resume)
        
        client.shutdown()
        sys.exit(0 if success else 1)
        
    except ConnectionRefusedError:
        print("❌ Could not connect to bridge. Is Minecraft running with the mod?")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n⏹️  Interrupted by user")
        sys.exit(130)
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
