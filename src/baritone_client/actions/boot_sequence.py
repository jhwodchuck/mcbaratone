"""
Boot sequence specific actions for the initial gathering phase.
"""

import time
from typing import List
from .base import BaseAction
from .resource_gathering import ResourceGatheringAction
from .tool_progression import ToolProgressionAction
from .night_survival import NightSurvivalAction
from .crafting import CraftingAction
from .inventory import InventoryAction
from ..core.interfaces import ActionContext, ActionResult
from ..common.inventory import count_item, craft
from ..common.resources import gather_wood, gather_stone, ensure_supplies
from ..common.base import setup_base, sleep_through_night
from ..common.combat import hunt_passive_mobs
from ..common.navigation import find_nearby_block


class SafetyCheckAction(BaseAction):
    """Check if conditions are dangerous and handle night survival."""

    def execute(self, context: ActionContext) -> ActionResult:
        """Perform safety checks and night survival activities."""
        try:
            state = self.run_command(context, "get_state", {})
            world_time = state.get("world_time", 0)
            health = state.get("health", 20)

            # Night time is roughly 13000-23000
            is_night = (world_time % 24000) >= 13000 and (world_time % 24000) <= 23000

            if is_night or health < 10:
                print("Night time detected - mining underground for safety")

                # Check what resources we need
                coal_count = count_item(context.client, "minecraft:coal")
                iron_count = count_item(context.client, "minecraft:raw_iron")

                # Mine stone for safety during night
                if is_night:
                    print("Mining stone underground during night...")
                    try:
                        context.client.transport.dispatch("chat", {"message": "#cancel"})
                        gather_stone(context.client, count=64, timeout=180)
                    except Exception as e:
                        print(f"Stone mining failed: {e}")

                return ActionResult.ok("Safety check and night mining complete")
            else:
                return ActionResult.ok("Day time - no safety actions needed")

        except Exception as e:
            print(f"Safety check error: {e}")
            return ActionResult.fail(f"Safety check failed: {e}")


class BaseRecoveryAction(BaseAction):
    """Recover base location and update world_map if needed."""

    def execute(self, context: ActionContext) -> ActionResult:
        """Recover base location from waypoints or crafting table."""
        print("Recovering Base Location...")

        found_pos = None

        # 1. Check existing waypoint
        try:
            wp = self.run_command(context, "waypoint", {"name": "base"})
            if wp:
                found_pos = (wp["x"], wp["y"], wp["z"])
                print(f"Existing 'base' waypoint found at {found_pos}")
        except Exception:
            pass

        # 2. If no waypoint, scan for crafting table
        if not found_pos:
            print("Scanning for crafting table nearby...")
            pos = find_nearby_block(context.client, ["minecraft:crafting_table"], radius=64)
            if pos:
                found_pos = pos
                print(f"Found crafting table at {found_pos}! Saving as base.")
                self.run_command(context, "chat", {"message": f"#waypoint save base {pos[0]} {pos[1]} {pos[2]}"})

        # 3. Update world_map.md if we have a location
        if found_pos:
            try:
                # Check if already logged (primitive check)
                with open("c:/gh/mcbaratone/world_map.md", "r") as f:
                    content = f.read()

                entry = f"({found_pos[0]}, {found_pos[1]}, {found_pos[2]})"
                if entry not in content:
                    with open("c:/gh/mcbaratone/world_map.md", "a") as f:
                        f.write(f"\n- **Crafting Table/Base (Recovered)**: {entry}")
                    print("Updated world_map.md with recovered base location.")
                else:
                    print("Base location already in world_map.md.")
            except Exception as e:
                print(f"Failed to update world_map.md: {e}")

        return ActionResult.ok("Base recovery complete")


class ConditionalWoodGatheringAction(BaseAction):
    """Gather wood only if not enough logs or planks are present."""

    def __init__(self, needed_logs: int = 4):
        self.needed_logs = needed_logs

    def execute(self, context: ActionContext) -> ActionResult:
        """Check inventory and gather wood if needed."""
        if count_item(context.client, "minecraft:log") >= self.needed_logs:
            print("Sufficient logs present; skipping wood gathering.")
            return ActionResult.ok("Already have sufficient logs")

        if count_item(context.client, "minecraft:planks") >= self.needed_logs * 4:
            print("Sufficient planks present; skipping wood gathering.")
            return ActionResult.ok("Already have sufficient planks")

        # Gather minimal wood
        success = gather_wood(context.client, count=self.needed_logs)
        if success:
            return ActionResult.ok(f"Gathered {self.needed_logs} logs")
        else:
            return ActionResult.fail("Failed to gather wood")


class PlankCraftingAction(BaseAction):
    """Craft planks from available logs."""

    def execute(self, context: ActionContext) -> ActionResult:
        """Convert logs to planks using available log types."""
        plank_types = [
            "minecraft:oak_planks", "minecraft:birch_planks", "minecraft:spruce_planks",
            "minecraft:dark_oak_planks", "minecraft:acacia_planks", "minecraft:jungle_planks",
            "minecraft:mangrove_planks", "minecraft:cherry_planks"
        ]
        total_planks = sum(count_item(context.client, p) for p in plank_types)

        if total_planks >= 4:
            print(f"Already have {total_planks} planks. Skipping craft.")
            return ActionResult.ok("Already have sufficient planks")

        log_types = [
            ("minecraft:oak_log", "minecraft:oak_planks"),
            ("minecraft:birch_log", "minecraft:birch_planks"),
            ("minecraft:spruce_log", "minecraft:spruce_planks"),
            ("minecraft:dark_oak_log", "minecraft:dark_oak_planks"),
            ("minecraft:acacia_log", "minecraft:acacia_planks"),
            ("minecraft:jungle_log", "minecraft:jungle_planks"),
        ]

        for log_id, plank_id in log_types:
            log_count = count_item(context.client, log_id)
            if log_count > 0:
                print(f"Converting {log_count} {log_id} to planks...")
                # Each log yields 4 planks
                result = craft(context.client, plank_id, log_count)
                if result:
                    print(f"Crafted {log_count * 4} {plank_id}")
                    return ActionResult.ok(f"Crafted planks from {log_count} logs")
                else:
                    return ActionResult.fail(f"Failed to craft planks from {log_id}")

        print("Warning: No logs found to craft planks.")
        return ActionResult.fail("No logs available for plank crafting")


class StoneToolCraftingAction(BaseAction):
    """Craft essential stone tools."""

    def execute(self, context: ActionContext) -> ActionResult:
        """Craft stone pickaxe, axe, shovel, and sword."""
        tools = {
            "minecraft:stone_pickaxe": 1,
            "minecraft:stone_axe": 1,
            "minecraft:stone_shovel": 1,
            "minecraft:stone_sword": 1,
        }

        result = ensure_supplies(context.client, tools)
        if result.success:
            return ActionResult.ok("Stone tools crafted successfully")
        else:
            return ActionResult.fail("Failed to craft stone tools")


class BedAcquisitionAction(BaseAction):
    """Acquire bed during day time for night survival."""

    def execute(self, context: ActionContext) -> ActionResult:
        """Check time and hunt sheep for bed if it's day."""
        # Check if we already have a bed
        bed_types = [
            "minecraft:white_bed", "minecraft:red_bed", "minecraft:blue_bed",
            "minecraft:green_bed", "minecraft:black_bed", "minecraft:yellow_bed",
            "minecraft:orange_bed", "minecraft:magenta_bed", "minecraft:light_blue_bed",
            "minecraft:yellow_bed", "minecraft:lime_bed", "minecraft:pink_bed",
            "minecraft:gray_bed", "minecraft:light_gray_bed", "minecraft:cyan_bed",
            "minecraft:purple_bed", "minecraft:brown_bed"
        ]

        if any(count_item(context.client, b) > 0 for b in bed_types):
            print("Already have a bed!")
            return ActionResult.ok("Bed already available")

        # Check time
        state = self.run_command(context, "get_state", {})
        time_raw = state.get("world_time", 0)
        is_day = (time_raw % 24000) < 13000

        if not is_day:
            print("It is night - skipping bed hunting.")
            return ActionResult.ok("Night time - bed acquisition skipped")

        print("It is day - hunting sheep for bed...")

        # Hunt sheep for wool
        wool_needed = 3
        current_wool = count_item(context.client, "minecraft:white_wool")

        if current_wool < wool_needed:
            hunt_passive_mobs(context.client, target_count=3, type_filter=["sheep"])

        # Check if we now have enough wool
        current_wool = count_item(context.client, "minecraft:white_wool")
        if current_wool < wool_needed:
            print(f"Only have {current_wool} wool, need {wool_needed}")
            return ActionResult.fail("Insufficient wool for bed")

        # Need planks too (any type)
        plank_types = [
            "minecraft:oak_planks", "minecraft:birch_planks", "minecraft:spruce_planks",
            "minecraft:dark_oak_planks", "minecraft:acacia_planks", "minecraft:jungle_planks",
            "minecraft:mangrove_planks", "minecraft:cherry_planks"
        ]
        total_planks = sum(count_item(context.client, p) for p in plank_types)

        if total_planks < 3:
            print(f"Need more planks for bed (have {total_planks}). Attempting to craft...")
            # Try to craft planks if we have logs
            plank_crafter = PlankCraftingAction()
            plank_result = plank_crafter.execute(context)
            if not plank_result.success:
                return ActionResult.fail("Cannot craft bed - insufficient planks")

        # Try craft white bed
        if ensure_supplies(context.client, {"minecraft:white_bed": 1}).success:
            print("Crafted white bed!")
            return ActionResult.ok("Bed crafted successfully")
        else:
            print("Failed to craft bed (maybe mixed wool colors?)")
            return ActionResult.fail("Bed crafting failed")


class HuntingAndScoutingAction(BaseAction):
    """Hunt animals and collect resources while scouting."""

    def __init__(self, target_animals: int = 10):
        self.target_animals = target_animals

    def execute(self, context: ActionContext) -> ActionResult:
        """Hunt passive mobs and collect seeds/sugarcane."""
        print(f"Hunting {self.target_animals} animals while scouting...")

        hunt_passive_mobs(context.client, target_count=self.target_animals)

        # Could add seed/sugarcane collection here
        # For now, simplified version
        return ActionResult.ok(f"Hunted {self.target_animals} animals")


class InfrastructurePlacementAction(BaseAction):
    """Place essential infrastructure at base location."""

    def execute(self, context: ActionContext) -> ActionResult:
        """Return to base and set up infrastructure."""
        print("Setting up infrastructure at base...")

        # 1. Return to Base
        self.run_command(context, "chat", {"message": "#goto base"})
        print("Traveling to base...")
        time.sleep(3)

        # Simple wait loop for arrival
        for _ in range(60):  # Max 3 mins
            state = self.run_command(context, "get_state", {})
            if not state.get("is_pathing", False):
                break
            time.sleep(3)

        print("Arrived at base area.")

        # 2. Setup Base
        # Ensure we have required materials
        if count_item(context.client, "minecraft:furnace") == 0:
            print("Need furnace - crafting...")
            ensure_supplies(context.client, {"minecraft:furnace": 1})

        if count_item(context.client, "minecraft:chest") == 0:
            print("Need chest - crafting...")
            ensure_supplies(context.client, {"minecraft:chest": 1})

        # Call setup_base
        try:
            setup_base(context.client)
            return ActionResult.ok("Infrastructure setup complete")
        except Exception as e:
            return ActionResult.fail(f"Infrastructure setup failed: {e}")


class FoodCookingAction(BaseAction):
    """Cook raw food in furnace."""

    def execute(self, context: ActionContext) -> ActionResult:
        """Cook available raw meat."""
        # TODO: Implement cooking logic
        print("Cooking food...")
        return ActionResult.ok("Food cooking placeholder")


class IronSmeltingAction(BaseAction):
    """Smelt raw iron into ingots."""

    def execute(self, context: ActionContext) -> ActionResult:
        """Smelt available raw iron."""
        # TODO: Implement smelting logic
        print("Smelting iron...")
        return ActionResult.ok("Iron smelting placeholder")


class StorageOrganizationAction(BaseAction):
    """Organize items into chests."""

    def execute(self, context: ActionContext) -> ActionResult:
        """Organize inventory into storage."""
        # TODO: Implement storage organization
        print("Organizing storage...")
        return ActionResult.ok("Storage organization placeholder")


class FinalSleepAction(BaseAction):
    """Sleep through night to complete boot sequence."""

    def execute(self, context: ActionContext) -> ActionResult:
        """Sleep through the night."""
        success = sleep_through_night(context.client)
        if success:
            return ActionResult.ok("Successfully slept through night")
        else:
            return ActionResult.fail("Failed to sleep through night")