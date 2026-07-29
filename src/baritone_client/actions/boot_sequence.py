"""
Boot sequence specific actions for the initial gathering phase.
"""

import time
from math import hypot
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
from ..common.base import (
    find_flat_ground,
    setup_base,
    sleep_through_night,
    wait_for_safe_daylight,
)
from ..common.combat import hunt_passive_mobs
from ..common.navigation import find_nearby_block, goto
from ..common.runtime_artifacts import append_world_map_entry
from ..common.tasks import SurvivalRecoveryRequired
from .boot_readiness import (
    durable_boot_capabilities,
    has_durable_tool_set,
    nearby_infrastructure_record,
    require_survival_margin,
)


_BOOT_WOOD_RECIPES = (
    ("minecraft:oak_log", "minecraft:oak_planks"),
    ("minecraft:spruce_log", "minecraft:spruce_planks"),
    ("minecraft:birch_log", "minecraft:birch_planks"),
    ("minecraft:jungle_log", "minecraft:jungle_planks"),
    ("minecraft:acacia_log", "minecraft:acacia_planks"),
    ("minecraft:dark_oak_log", "minecraft:dark_oak_planks"),
    ("minecraft:mangrove_log", "minecraft:mangrove_planks"),
    ("minecraft:cherry_log", "minecraft:cherry_planks"),
    ("minecraft:pale_oak_log", "minecraft:pale_oak_planks"),
)
_BOOT_LOGS = [log_id for log_id, _ in _BOOT_WOOD_RECIPES]
_BOOT_PLANKS = [
    *(plank_id for _, plank_id in _BOOT_WOOD_RECIPES),
    "minecraft:bamboo_planks",
]
_BOOT_ANCHOR_STAGING_RADIUS = 12.0


def _count_family(client, item_ids) -> int:
    return sum(count_item(client, item_id) for item_id in item_ids)


def _horizontal_distance_to(client, position) -> float:
    """Measure staging distance without penalizing a table on another Y level."""
    state = client.transport.dispatch("get_state", {})
    current = state.get("block_position", state.get("position", {}))
    if not all(axis in current for axis in ("x", "z")):
        return float("inf")
    return hypot(
        float(current["x"]) - position[0],
        float(current["z"]) - position[2],
    )


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

            if is_night:
                print("Night time detected - establishing shelter until daylight")
                if wait_for_safe_daylight(context.client):
                    require_survival_margin(context.client)
                    return ActionResult.ok("Sheltered safely until daylight")
                return ActionResult.fail("Could not establish safe daylight")

            require_survival_margin(context.client)

            return ActionResult.ok("Day time - no safety actions needed")

        except SurvivalRecoveryRequired:
            raise
        except Exception as e:
            print(f"Safety check error: {e}")
            return ActionResult.fail(f"Safety check failed: {e}")


class BaseRecoveryAction(BaseAction):
    """Recover base location and update world_map if needed."""

    def execute(self, context: ActionContext) -> ActionResult:
        """Recover base location from waypoints or crafting table."""
        print("Recovering Base Location...")

        found_pos = None

        # 1. The checkpointed starter house is authoritative.  Temporary
        # quarry crafting tables may have overwritten the Baritone `base`
        # waypoint during early tool crafting.
        structures = getattr(context.state, "custom_data", {}).get(
            "structures", {}
        )
        house = structures.get("starter_house", {})
        persisted_table = house.get("crafting_table")
        if isinstance(persisted_table, (list, tuple)) and len(persisted_table) == 3:
            candidate = tuple(int(value) for value in persisted_table)
            block = self.run_command(
                context,
                "get_block",
                {"x": candidate[0], "y": candidate[1], "z": candidate[2]},
            ).get("id", "")
            if block == "minecraft:crafting_table":
                found_pos = candidate
                print(f"Checkpointed house base verified at {found_pos}")
                self.run_command(
                    context,
                    "chat",
                    {"message": f"#waypoint save base {found_pos[0]} {found_pos[1]} {found_pos[2]}"},
                )
            else:
                # The table itself is repairable.  If the checkpointed
                # furnace/chest still prove this is the completed house, keep
                # its interior coordinate authoritative instead of selecting
                # a temporary roof or quarry table.
                verified_anchors = 0
                for key, accepted in (
                    ("furnace", {"minecraft:furnace", "minecraft:blast_furnace"}),
                    ("supply_chest", {"minecraft:chest", "minecraft:trapped_chest"}),
                ):
                    position = house.get(key)
                    if not isinstance(position, (list, tuple)) or len(position) != 3:
                        continue
                    anchor_block = self.run_command(
                        context,
                        "get_block",
                        {"x": position[0], "y": position[1], "z": position[2]},
                    ).get("id", "")
                    verified_anchors += int(anchor_block in accepted)
                if verified_anchors >= 2:
                    found_pos = candidate
                    print(
                        f"Checkpointed house anchors verified at {found_pos}; "
                        "crafting table repair remains pending"
                    )
                    self.run_command(
                        context,
                        "chat",
                        {"message": f"#waypoint save base {found_pos[0]} {found_pos[1]} {found_pos[2]}"},
                    )

        # 2. Check existing waypoint only when no persisted house was verified.
        try:
            if not found_pos:
                wp = self.run_command(context, "waypoint", {"name": "base"})
                if wp:
                    found_pos = (wp["x"], wp["y"], wp["z"])
                    print(f"Existing 'base' waypoint found at {found_pos}")
        except Exception:
            pass

        # 3. If no waypoint, scan for crafting table
        if not found_pos:
            print("Scanning for crafting table nearby...")
            pos = find_nearby_block(context.client, ["minecraft:crafting_table"], radius=64)
            if pos:
                found_pos = pos
                print(f"Found crafting table at {found_pos}! Saving as base.")
                self.run_command(context, "chat", {"message": f"#waypoint save base {pos[0]} {pos[1]} {pos[2]}"})

        # 4. Update world_map.md if we have a location
        if found_pos:
            position = tuple(int(value) for value in found_pos)
            if append_world_map_entry(
                "Crafting Table/Base (Recovered)",
                position,
                state=context.state,
            ):
                print("Updated runtime world map with recovered base location.")
            else:
                print("Base location already recorded in the runtime world map.")

            # Every boot retry must begin from the recovered anchor. Waiting
            # until infrastructure placement near the end of the sequence
            # allowed wood, food, and scouting retries to ratchet hundreds of
            # blocks farther away. Eventually a valid house was repeatedly
            # reported as unreachable even though the real defect was
            # cumulative retry drift.
            distance = _horizontal_distance_to(context.client, position)
            if distance != float("inf"):
                if distance > _BOOT_ANCHOR_STAGING_RADIUS:
                    require_survival_margin(context.client)
                    print(
                        "Returning to recovered boot anchor before resource "
                        f"work ({distance:.1f}m away)..."
                    )
                    if not goto(
                        context.client,
                        position[0],
                        position[1],
                        position[2],
                        timeout=300,
                        check_interval=1.0,
                        tolerance=3.0,
                    ):
                        distance = _horizontal_distance_to(
                            context.client, position
                        )
                        safety_aborted = getattr(
                            context.client,
                            "_last_navigation_survival_abort",
                            False,
                        )
                        if (
                            safety_aborted
                            or distance > _BOOT_ANCHOR_STAGING_RADIUS
                        ):
                            return ActionResult.fail(
                                "Could not return to recovered boot anchor"
                            )
                        print(
                            "Recovered boot anchor staged within "
                            f"{distance:.1f}m despite route completion status."
                        )

        return ActionResult.ok("Base recovery complete")


class ConditionalWoodGatheringAction(BaseAction):
    """Gather wood only if not enough logs or planks are present."""

    def __init__(self, needed_logs: int = 4):
        self.needed_logs = needed_logs

    def execute(self, context: ActionContext) -> ActionResult:
        """Check inventory and gather wood if needed."""
        if durable_boot_capabilities(context):
            print(
                "Durable tools and verified infrastructure already exist; "
                "skipping bootstrap wood gathering."
            )
            return ActionResult.ok("Durable boot capabilities already established")

        logs = _count_family(context.client, _BOOT_LOGS)
        if logs >= self.needed_logs:
            print("Sufficient logs present; skipping wood gathering.")
            return ActionResult.ok("Already have sufficient logs")

        planks = _count_family(context.client, _BOOT_PLANKS)
        if planks + logs * 4 >= self.needed_logs * 4:
            print("Sufficient convertible wood present; skipping wood gathering.")
            return ActionResult.ok("Already have sufficient convertible wood")

        # Four convertible planks are enough to make the sticks for the stone
        # tool set when a table is already available. This lets a bot stranded
        # on a narrow pillar reach the stone/descent recovery before demanding
        # the larger base-building wood reserve; it can gather that reserve
        # safely after reaching terrain.
        table_available = count_item(
            context.client, "minecraft:crafting_table"
        ) > 0 or bool(
            find_nearby_block(
                context.client,
                ["minecraft:crafting_table"],
                radius=4,
            )
        )
        if table_available and planks + logs * 4 >= 4:
            print(
                "Bootstrap wood and a crafting table are available; "
                "deferring the larger wood reserve until after stone recovery."
            )
            return ActionResult.ok("Enough wood to bootstrap stone recovery")

        require_survival_margin(context.client)

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
        if durable_boot_capabilities(context):
            print(
                "Durable tools and verified infrastructure already exist; "
                "skipping bootstrap plank crafting."
            )
            return ActionResult.ok("Bootstrap planks are no longer required")

        total_planks = _count_family(context.client, _BOOT_PLANKS)

        if total_planks >= 4:
            print(f"Already have {total_planks} planks. Skipping craft.")
            return ActionResult.ok("Already have sufficient planks")

        for log_id, plank_id in _BOOT_WOOD_RECIPES:
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
        if has_durable_tool_set(context.client):
            print("Complete durable tool set already present; skipping stone tool crafting.")
            return ActionResult.ok("Durable tool set already available")

        # Full set: pickaxe 3 + axe 3 + shovel 1 + sword 2 = 9 cobble.
        # The generic requirement strategy only sees the requested finished
        # tool and otherwise opens the table with no raw material available.
        required_cobble = 9
        if count_item(context.client, "minecraft:cobblestone") < required_cobble:
            if not gather_stone(
                context.client,
                count=required_cobble,
                timeout=180,
            ):
                return ActionResult.fail("Failed to gather cobblestone for stone tools")
        if count_item(context.client, "minecraft:cobblestone") < required_cobble:
            return ActionResult.fail("Insufficient cobblestone for stone tools")

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

        # Base construction places the carried bed into the starter house.
        # Treat that verified world block as satisfying the dependency instead
        # of spending food hunting sheep for an unnecessary duplicate.
        try:
            house = context.state.custom_data.get("structures", {}).get(
                "starter_house", {}
            )
            bed_position = house.get("bed")
            if isinstance(bed_position, (list, tuple)) and len(bed_position) == 3:
                bx, by, bz = (int(value) for value in bed_position)
                bed_block = context.client.transport.dispatch(
                    "get_block", {"x": bx, "y": by, "z": bz}
                ).get("id", "")
                if bed_block.endswith("_bed"):
                    print(f"Checkpointed house bed verified at {(bx, by, bz)}")
                    return ActionResult.ok("House bed already available")
        except (AttributeError, TypeError, ValueError):
            pass

        # Check time
        state = self.run_command(context, "get_state", {})
        time_raw = state.get("world_time", 0)
        is_day = (time_raw % 24000) < 13000

        # A bed is optional and a long exploratory sheep search consumes the
        # last hunger needed to return to the completed house.  FoodAndIron is
        # the next checkpointed phase and owns deliberate food acquisition.
        if int(state.get("food_level", 20)) <= 8:
            print("Hunger is low - deferring optional bed search to preserve return energy.")
            return ActionResult.ok("Bed search deferred at low hunger")

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

        # The generic supply loop has no bed handler and otherwise waits its
        # full 600-second budget. Make one bounded direct attempt; a completed
        # shelter remains a valid fallback when this optional craft fails.
        if craft(context.client, "minecraft:white_bed", 1):
            print("Crafted white bed!")
            return ActionResult.ok("Bed crafted successfully")
        print("Bed craft unavailable; deferring optional bed acquisition.")
        return ActionResult.ok("Bed crafting deferred")


class HuntingAndScoutingAction(BaseAction):
    """Hunt animals and collect resources while scouting."""

    def __init__(self, target_animals: int = 10):
        self.target_animals = target_animals

    def execute(self, context: ActionContext) -> ActionResult:
        """Hunt passive mobs and collect seeds/sugarcane."""
        state = self.run_command(context, "get_state", {})
        day_time = int(state.get("world_time", 0)) % 24000
        food_level = int(state.get("food_level", 20))
        if food_level <= 8 or day_time >= 11000:
            print(
                "Deferring optional boot hunt "
                f"(food={food_level}, time={day_time}) to FoodAndIron."
            )
            return ActionResult.ok("Boot hunt deferred to FoodAndIron")

        print(f"Hunting {self.target_animals} animals while scouting...")

        kills = hunt_passive_mobs(
            context.client, target_count=self.target_animals
        )

        # Could add seed/sugarcane collection here
        if kills < self.target_animals:
            return ActionResult.ok(
                f"Boot hunt partial: {kills}/{self.target_animals}; "
                "FoodAndIron will continue food acquisition"
            )
        return ActionResult.ok(f"Hunted {kills} animals")


class InfrastructurePlacementAction(BaseAction):
    """Place essential infrastructure at base location."""

    def execute(self, context: ActionContext) -> ActionResult:
        """Return to base and set up infrastructure."""
        print("Setting up infrastructure at base...")

        # 1. Resolve and return to the checkpointed house directly.  A named
        # ``base`` waypoint can be overwritten by temporary quarry tables, and
        # merely seeing ``is_pathing=False`` is not proof of arrival.
        house = nearby_infrastructure_record(context)
        house_target = house.get("crafting_table")
        if not isinstance(house_target, (list, tuple)) or len(house_target) != 3:
            origin = house.get("origin") or getattr(
                context.state, "custom_data", {}
            ).get("base_location")
            if isinstance(origin, (list, tuple)) and len(origin) == 3:
                house_target = (int(origin[0]), int(origin[1]) + 1, int(origin[2]))

        if not isinstance(house_target, (list, tuple)) or len(house_target) != 3:
            # BOOT_SEQUENCE now establishes survival capabilities before the
            # full starter house. Build a compact verified infrastructure
            # anchor near the player instead of requiring T1203 up front.
            bootstrap_site = find_flat_ground(
                context.client,
                radius=12,
                footprint=3,
            )
            if bootstrap_site is None:
                return ActionResult.fail(
                    "No safe site found for bootstrap infrastructure"
                )
            success, location = setup_base(context.client, bootstrap_site)
            if not success or location is None:
                return ActionResult.fail(
                    "Bootstrap infrastructure setup remained incomplete"
                )
            x, y, z = (int(value) for value in location)
            custom_data = getattr(context.state, "custom_data", {})
            custom_data["bootstrap_base_location"] = [x, y, z]
            custom_data.setdefault("structures", {})["bootstrap_base"] = {
                "origin": [x, y, z],
                "crafting_table": [x + 1, y, z + 1],
                "furnace": [x + 2, y, z + 1],
                "supply_chest": [x + 1, y, z + 2],
                "verified": True,
            }
            return ActionResult.ok(
                "Bootstrap infrastructure established before house construction"
            )

        print(f"Traveling to checkpointed house at {tuple(house_target)}...")
        if not goto(
            context.client,
            int(house_target[0]),
            int(house_target[1]),
            int(house_target[2]),
            timeout=300,
            check_interval=1.0,
            tolerance=3.0,
        ):
            return ActionResult.fail("Could not reach checkpointed house")
        print("Arrived at checkpointed house.")

        # 2. Reuse the verified infrastructure created by BASE_CONSTRUCTION.
        # Placed blocks no longer appear in inventory; blindly checking only
        # inventory here used to craft duplicates and could replace the
        # existing chest/table.  Probe the persisted coordinates first.
        expected = (
            ("crafting_table", house.get("crafting_table"), ("minecraft:crafting_table",)),
            ("furnace", house.get("furnace"), ("minecraft:furnace", "minecraft:blast_furnace")),
            ("supply_chest", house.get("supply_chest"), ("minecraft:chest", "minecraft:trapped_chest")),
        )
        missing = set()
        for key, position, block_ids in expected:
            if not isinstance(position, (list, tuple)) or len(position) != 3:
                missing.add(key)
                continue
            block = self.run_command(
                context,
                "get_block",
                {"x": position[0], "y": position[1], "z": position[2]},
            ).get("id", "")
            if block not in block_ids:
                missing.add(key)
        if not missing:
            return ActionResult.ok("Existing base infrastructure verified")

        # 3. Repair missing infrastructure at the persisted house interior.
        if "crafting_table" in missing and count_item(
            context.client, "minecraft:crafting_table"
        ) == 0:
            print("Need crafting table - crafting...")
            result = ensure_supplies(context.client, {"minecraft:crafting_table": 1})
            if not result.success:
                return ActionResult.fail("Could not craft missing crafting table")

        if "furnace" in missing and count_item(context.client, "minecraft:furnace") == 0:
            print("Need furnace - crafting...")
            result = ensure_supplies(context.client, {"minecraft:furnace": 1})
            if not result.success:
                return ActionResult.fail("Could not craft missing furnace")

        if "supply_chest" in missing and count_item(context.client, "minecraft:chest") == 0:
            print("Need chest - crafting...")
            result = ensure_supplies(context.client, {"minecraft:chest": 1})
            if not result.success:
                return ActionResult.fail("Could not craft missing chest")

        try:
            origin = house.get("origin") or getattr(
                context.state, "custom_data", {}
            ).get("base_location")
            location = None
            if isinstance(origin, (list, tuple)) and len(origin) == 3:
                location = (int(origin[0]), int(origin[1]) + 1, int(origin[2]))
            success, _ = setup_base(context.client, location)
            if success:
                return ActionResult.ok("Infrastructure setup complete")
            return ActionResult.fail("Infrastructure setup remained incomplete")
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
        """Verify that durable storage exists for later organization."""
        structures = getattr(context.state, "custom_data", {}).get(
            "structures", {}
        )
        record = structures.get("starter_house") or structures.get(
            "bootstrap_base", {}
        )
        position = record.get("supply_chest") if isinstance(record, dict) else None
        if not isinstance(position, (list, tuple)) or len(position) != 3:
            return ActionResult.fail("No persisted supply chest to organize")
        block = self.run_command(
            context,
            "get_block",
            {"x": position[0], "y": position[1], "z": position[2]},
        ).get("id", "")
        if block not in {"minecraft:chest", "minecraft:trapped_chest"}:
            return ActionResult.fail("Persisted supply chest is not present")
        return ActionResult.ok("Durable storage verified")


class FinalSleepAction(BaseAction):
    """Sleep through night to complete boot sequence."""

    def execute(self, context: ActionContext) -> ActionResult:
        """Sleep through the night."""
        require_survival_margin(context.client)
        success = sleep_through_night(context.client)
        if success:
            return ActionResult.ok("Successfully slept through night")

        # A bed is useful but not a hard boot dependency.  Sheep may not spawn
        # close enough on the first day, and treating that random absence as a
        # phase failure restarts the entire boot sequence.  The completed house
        # (or the verified underground pocket) is a valid no-bed fallback.
        state = self.run_command(context, "get_state", {})
        if int(state.get("world_time", 0)) % 24000 >= 12000:
            print("No usable bed; waiting safely for daylight instead...")
            if wait_for_safe_daylight(context.client):
                return ActionResult.ok("Waited safely for daylight without a bed")

        return ActionResult.fail("Failed to sleep or wait safely through night")
