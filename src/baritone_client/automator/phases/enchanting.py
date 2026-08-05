"""
Phase 3: Enchanting Phase
"""

import time
from pathlib import Path

from ..phase_executor import PhaseHandler
from ..resource_manager import ResourceManager
from ..state_manager import Phase, StateManager
from ...common import TaskResult, SequentialTask, ActionTask, harness_ops
from ...common.inventory import (
    craft,
    count_item,
    deposit_excess_to_chest,
    get_inventory,
    withdraw_required_from_chest,
)
from ...common.combat import eat_until_hunger, hunt_mobs, scan_for_threats
from ...common.husbandry import visit_known_herd_for_loot
from . import leather_supply
from ...common.navigation import find_nearby_block, goto
from ...common.nether import enter_portal, find_nearest_portal
from ...common.base import (
    _clear_wrong_house_target,
    _good_house_plan,
    _house_door_aligned,
    _matches_house_role,
    build_good_house,
    wait_for_chunk_loaded,
    wait_for_safe_daylight,
)
from ...common.resources import (
    LOG_BLOCKS,
    LOG_TO_PLANKS,
    PLANK_ITEMS,
    _craft_with_table,
    _relocate_to_dry_stone_terrain,
    gather_wood,
)

WOOL_COLORS = (
    "white", "orange", "magenta", "light_blue", "yellow", "lime", "pink",
    "gray", "light_gray", "cyan", "purple", "blue", "brown", "green",
    "red", "black",
)
BED_ITEMS = tuple(f"minecraft:{color}_bed" for color in WOOL_COLORS)

class EnchantingPipelineHandler(PhaseHandler):
    """Phase 3: Enchanting setup - Hour 2-3."""

    def __init__(self):
        self._leather_expedition_index = 0
    
    def get_name(self) -> str:
        return "Enchanting Core (Hour 2-3)"
    
    def execute(self, client, resources: ResourceManager, state: StateManager) -> TaskResult:
        tasks = [
            ActionTask(
                "Return to the Overworld",
                lambda c: self._ensure_overworld(c, state),
            ),
            ActionTask(
                "Verify house and restore base storage",
                lambda c: self._ensure_starter_base(c, state),
            ),
            ActionTask(
                "Ensure a verified sleeping bed",
                lambda c: self._ensure_sleeping_bed(c, state),
            ),
            ActionTask(
                "Gather 46 leather",
                lambda c: self._gather_leather(c, state),
            ),
            ActionTask(
                "Harvest sugarcane -> 138 paper",
                lambda c: self._harvest_sugarcane(c, state),
            ),
            ActionTask("Craft 46 books", self._craft_books),
            ActionTask(
                "Craft enchanting table",
                lambda c: self._craft_enchanting_table(c, state),
            ),
            ActionTask(
                "Craft 15 bookshelves",
                lambda c: self._craft_bookshelves(c, state),
            ),
            ActionTask(
                "Build enchanting room",
                lambda c: self._build_enchanting_room(c, state),
            ),
            ActionTask(
                "Verify level-30 enchanting capability",
                lambda c: self._roll_enchants(c, state),
            ),
        ]
        
        executor = SequentialTask("Enchanting Core", tasks)
        return executor.run(client)

    @staticmethod
    def _current_dimension(client) -> str:
        snapshot = client.transport.dispatch("get_state", {})
        if isinstance(snapshot, dict):
            snapshot = snapshot.get("data", snapshot)
        return str(snapshot.get("dimension", "")).lower()

    def _ensure_overworld(self, client, state: StateManager) -> bool:
        """Never run Overworld resource objectives from another dimension.

        Phase retries are free to choose another remaining objective after a
        Nether failure.  Previously that allowed ENCHANTING_PIPELINE to start
        while the player was still in the Nether, where it repeatedly tried
        to walk hundreds of blocks to an Overworld house and harvest
        sugarcane.  Return through a verified portal first, or fail closed so
        the retry cannot corrupt navigation or checkpoint evidence.
        """
        dimension = self._current_dimension(client)
        if "overworld" in dimension:
            return True
        if "nether" not in dimension:
            print(f"  Enchanting requires the Overworld; current dimension is {dimension!r}")
            return False

        candidates = []
        # A fresh find_blocks observation is stronger than a checkpointed
        # coordinate, which may refer to a portal that has since been broken.
        nearby = find_nearest_portal(client, "the_nether")
        if nearby is not None:
            candidates.append(tuple(int(value) for value in nearby))
        try:
            locations = state.get_locations("nether_portal").get(
                "nether_portal", []
            )
            for location in locations:
                if "nether" in str(location.get("dimension", "")).lower():
                    candidate = (
                        int(location["x"]),
                        int(location["y"]),
                        int(location["z"]),
                    )
                    if candidate not in candidates:
                        candidates.append(candidate)
        except Exception:
            pass
        if not candidates:
            print("  Enchanting paused: no verified Nether return portal was found.")
            return False
        for portal in candidates:
            print(f"  Returning to the Overworld before enchanting via {portal}")
            if not enter_portal(
                client,
                portal,
                target_dimension="minecraft:overworld",
                timeout=60,
            ):
                continue
            if "overworld" in self._current_dimension(client):
                return True
            print("  Portal traversal did not produce an Overworld state transition.")
        return False

    def _ensure_starter_base(self, client, state: StateManager) -> bool:
        """Repair the survival boundary and restore exact owned workstations."""
        structures = state.custom_data.setdefault("structures", {})
        house = structures.get("starter_house", {})
        origin = house.get("origin") or state.custom_data.get("base_location")
        if not isinstance(origin, (list, tuple)) or len(origin) != 3:
            return False
        x, y, z = (int(value) for value in origin)

        # This is the first task in ENCHANTING_PIPELINE's sequence, so it
        # reruns on every phase-level retry -- including mid-expedition,
        # hundreds of blocks from base. The origin chunk is then genuinely
        # unloaded, not a transient load race wait_for_chunk_loaded can wait
        # out, so a survey from here always misreads the intact house as
        # destroyed and starts a wasteful, dangerous rebuild at the wrong
        # location. Confirmed live: Bot07 gathering wood for a "destroyed"
        # house 200+ blocks from its real one. Defer verification until
        # actually near home; a later retry (typically right after an
        # expedition's own return-home) will catch it once proximity holds.
        live_state = client.transport.dispatch("get_state", {})
        position = live_state.get("block_position", live_state.get("position", {}))
        if all(axis in position for axis in ("x", "y", "z")):
            distance = (
                (float(position["x"]) - x) ** 2
                + (float(position["y"]) - y) ** 2
                + (float(position["z"]) - z) ** 2
            ) ** 0.5
            if distance > 24.0:
                print(
                    f"  Too far from starter house ({distance:.0f} blocks) "
                    "to verify integrity; deferring check."
                )
                return True

        # A reconnect can land the player back at base before the chunk
        # finishes streaming in; querying it too early misreads the intact
        # house as void_air ("missing") and triggers a needless, dangerous
        # from-scratch rebuild. Give it a moment to load first.
        wait_for_chunk_loaded(client, x, y, z)
        if not self._starter_house_integrity(client, (x, y, z)):
            print("  Starter-house integrity failed; repairing before progression.")
            if not build_good_house(client, x, y, z):
                return False
            if not self._starter_house_integrity(client, (x, y, z)):
                return False

        targets = {
            "crafting_table": (x + 1, y + 1, z + 1),
            "furnace": (x + 2, y + 1, z + 1),
            "supply_chest": (x + 1, y + 1, z + 2),
        }
        expected = {
            "crafting_table": "minecraft:crafting_table",
            "furnace": "minecraft:furnace",
            "supply_chest": "minecraft:chest",
        }

        missing = {
            name
            for name, target in targets.items()
            if expected[name]
            not in client.transport.dispatch(
                "get_block",
                {"x": target[0], "y": target[1], "z": target[2]},
            ).get("id", "")
        }
        if not missing:
            house.update({name: list(target) for name, target in targets.items()})
            return True

        plank_target = (
            (4 if "crafting_table" in missing else 0)
            + (8 if "supply_chest" in missing else 0)
        )
        if plank_target and not self._ensure_bookshelf_planks(
            client,
            state,
            plank_target,
        ):
            return False

        if "crafting_table" in missing:
            if count_item(client, "minecraft:crafting_table") < 1 and not craft(
                client,
                "minecraft:crafting_table",
                1,
            ):
                return False
            if not self._place_owned_base_block(
                client,
                targets["crafting_table"],
                "minecraft:crafting_table",
            ):
                return False

        if "supply_chest" in missing:
            if count_item(client, "minecraft:chest") < 1:
                if not harness_ops.ensure_crafting_table_open(
                    client,
                    table_pos=targets["crafting_table"],
                ):
                    return False
                if not harness_ops.craft_chest_manual(client):
                    client.transport.dispatch("close_screen", {})
                    return False
                client.transport.dispatch("close_screen", {})
            if not self._place_owned_base_block(
                client,
                targets["supply_chest"],
                "minecraft:chest",
            ):
                return False

        if "furnace" in missing:
            if count_item(client, "minecraft:furnace") < 1 and not craft(
                client,
                "minecraft:furnace",
                1,
            ):
                return False
            if not self._place_owned_base_block(
                client,
                targets["furnace"],
                "minecraft:furnace",
            ):
                return False

        verified = all(
            expected[name]
            in client.transport.dispatch(
                "get_block",
                {"x": target[0], "y": target[1], "z": target[2]},
            ).get("id", "")
            for name, target in targets.items()
        )
        if verified:
            house.update({name: list(target) for name, target in targets.items()})
        return verified

    def _starter_house_integrity(self, client, origin) -> bool:
        x, y, z = (int(value) for value in origin)
        for tx, ty, tz, role in _good_house_plan(x, y, z):
            block_id = client.transport.dispatch(
                "get_block",
                {"x": tx, "y": ty, "z": tz},
            ).get("id", "")
            if not _matches_house_role(block_id, role):
                return False
        return _house_door_aligned(client, x + 3, y + 1, z)

    def _place_owned_base_block(self, client, target, item_id: str) -> bool:
        """Place a workstation without allowing its approach to mine the house."""
        current = client.transport.dispatch(
            "get_block",
            {"x": target[0], "y": target[1], "z": target[2]},
        ).get("id", "")
        if current == item_id:
            return True
        if not _clear_wrong_house_target(
            client,
            target[0],
            target[1],
            target[2],
            item_id,
        ):
            return False
        client.transport.dispatch("chat", {"message": "#set allowBreak false"})
        try:
            return harness_ops.place_block_exact(
                client,
                target[0],
                target[1],
                target[2],
                item_id,
                allow_break=False,
            )
        finally:
            client.transport.dispatch("cancel", {})
            client.transport.dispatch("chat", {"message": "#set allowBreak true"})

    def _ensure_sleeping_bed(self, client, state: StateManager) -> bool:
        """Acquire, place, and verify a permanent bed inside the starter house."""
        house = state.custom_data.get("structures", {}).get("starter_house", {})
        existing = find_nearby_block(client, list(BED_ITEMS), radius=8)
        if existing is not None:
            house["bed"] = list(existing)
            return True

        enchanting_state = state.custom_data.setdefault("enchanting", {})
        # Some resume paths fail repeatedly before any successful sheep-hunt
        # attempt can complete (missing sheep, transient bridge issues, or
        # starvation loops). Count every invocation without a verified bed so
        # we can eventually skip this non-critical prerequisite instead of
        # blocking forever on ENCHANTING_PIPELINE.
        attempt_count = int(enchanting_state.get("bed_retry_attempts", 0)) + 1
        enchanting_state["bed_retry_attempts"] = attempt_count
        self._persist_enchanting_state(client, state)
        if attempt_count >= 4:
            enchanting_state["skip_bed_requirement"] = True
            print(
                "  Bed checks retried too many times; skipping sleeping-bed "
                "requirement to keep enchanting progression moving."
            )
            self._persist_enchanting_state(client, state)
            return True

        if enchanting_state.get("skip_bed_requirement"):
            print("  Continuing without verified bed: requirement was previously exhausted.")
            return True

        requirements = {f"minecraft:{color}_wool": 3 for color in WOOL_COLORS}
        requirements.update({bed_id: 1 for bed_id in BED_ITEMS})
        self._withdraw_at_home(client, state, requirements)

        bed_item = next(
            (item_id for item_id in BED_ITEMS if count_item(client, item_id) > 0),
            None,
        )
        wool_color = next(
            (
                color
                for color in WOOL_COLORS
                if count_item(client, f"minecraft:{color}_wool") >= 3
            ),
            None,
        )
        if bed_item is None and wool_color is None:
            best_color = max(
                WOOL_COLORS,
                key=lambda color: count_item(
                    client,
                    f"minecraft:{color}_wool",
                ),
            )
            current = count_item(client, f"minecraft:{best_color}_wool")
            if not self._wait_for_daylight(client, state):
                return False
            self._bank_diamonds_before_expedition(client, state)
            if not self._leave_starter_house(client, state):
                return False

            origin = house.get("origin") or state.custom_data.get("base_location")
            exploration_center = None
            if isinstance(origin, (list, tuple)) and len(origin) == 3:
                exploration_center = self._next_bed_exploration_center(
                    origin,
                    state,
                )
            hunted = None
            returned = False
            try:
                hunted = hunt_mobs(
                    client,
                    mob_types=["sheep"],
                    required_loot={
                        f"minecraft:{best_color}_wool": 3 - current,
                    },
                    search_radius=64,
                    timeout=420,
                    heal_threshold=10.0,
                    abort_on_other_hostiles=True,
                    latest_world_time=9000,
                    max_distance_from_origin=160.0,
                    exploration_center=exploration_center,
                )
            finally:
                client.transport.dispatch("cancel", {})
                returned = self._return_home(client, state)
            if not hunted or not hunted.success or not returned:
                if attempt_count >= 2:
                    enchanting_state["skip_bed_requirement"] = True
                    print(
                        "  Could not secure wool or bed after retries; "
                        "skipping bed requirement to keep progression."
                    )
                    self._persist_enchanting_state(client, state)
                    return True
                return False
            wool_color = next(
                (
                    color
                    for color in WOOL_COLORS
                    if count_item(client, f"minecraft:{color}_wool") >= 3
                ),
                None,
            )

        if bed_item is None:
            if wool_color is None:
                return False
            if not self._ensure_bookshelf_planks(client, state, 3):
                return False
            bed_item = f"minecraft:{wool_color}_bed"
            if not harness_ops.ensure_crafting_table_open(client):
                return False
            if not harness_ops.craft_bed_manual(client, bed_item):
                client.transport.dispatch("close_screen", {})
                return False
            client.transport.dispatch("close_screen", {})

        if count_item(client, bed_item) < 1:
            return False
        origin = house.get("origin") or state.custom_data.get("base_location")
        if not isinstance(origin, (list, tuple)) or len(origin) != 3:
            return False
        x, y, z = (int(value) for value in origin)
        candidates = (
            (x + 4, y + 1, z + 3),
            (x + 4, y + 1, z + 4),
            (x + 3, y + 1, z + 4),
        )
        for target in candidates:
            if harness_ops.place_block_exact(
                client,
                target[0],
                target[1],
                target[2],
                bed_item,
                allow_break=False,
            ):
                verified = find_nearby_block(client, list(BED_ITEMS), radius=8)
                if verified is not None:
                    house["bed"] = list(verified)
                    return True
        return False

    def _persist_enchanting_state(self, client, state: StateManager) -> None:
        """Persist in-memory enchanting skip/retry counters immediately."""
        try:
            state.save_checkpoint(get_inventory(client))
        except Exception:
            # Non-fatal: progress persistence is a safety optimization only.
            pass

    def _next_bed_exploration_center(self, origin, state):
        """Persist sheep-search rotation independently from leather searches.

        Mirrored south for the same reason as the leather rotation: an
        all-north offset list can never find sheep if the huntable biome
        lies south of base instead.
        """
        offsets = (
            (64, -96), (-64, -96), (96, -48), (-96, -48), (0, -128),
            (64, 96), (-64, 96), (96, 48), (-96, 48), (0, 128),
        )
        expedition_state = state.custom_data.setdefault("expeditions", {})
        index = int(expedition_state.get("bed_sector_index", 0))
        expedition_state["bed_sector_index"] = index + 1
        dx, dz = offsets[index % len(offsets)]
        return int(origin[0]) + 3 + dx, int(origin[2]) + dz

    def _gather_leather(self, client, state: StateManager) -> bool:
        """Safely collect leather for 46 books, including banked supplies."""
        target = 46
        withdrawn = self._withdraw_at_home(
            client, state, {"minecraft:leather": target}
        )
        local_expedition = withdrawn < 0
        current = count_item(client, "minecraft:leather")
        if current >= target:
            print("  Already have 46 leather.")
            return True

        if leather_supply.search_is_futile(client, state):
            return False

        if not self._wait_for_daylight(client, state):
            return False
        if local_expedition:
            print(
                "  Home route is unavailable; starting a bounded leather "
                "expedition from the live position."
            )
        else:
            self._bank_diamonds_before_expedition(client, state)
            if not self._leave_starter_house(client, state):
                return False

        missing = target - current
        print(f"  Hunting cows for {missing} additional leather...")
        house = state.custom_data.get("structures", {}).get("starter_house", {})
        origin = house.get("origin") or state.custom_data.get("base_location")
        exploration_center = None
        if local_expedition:
            live = client.transport.dispatch("get_state", {})
            if "swamp" in str(live.get("biome", "")):
                print("  Swamp route is boxed in; seeking nearby dry hunt terrain...")
                if _relocate_to_dry_stone_terrain(client):
                    live = client.transport.dispatch("get_state", {})
            position = live.get("block_position", live.get("position", {}))
            live_origin = (
                int(position.get("x", 0)),
                int(position.get("y", 0)),
                int(position.get("z", 0)),
            )
            exploration_center = self._next_leather_exploration_center(
                live_origin,
                state,
            )
        elif isinstance(origin, (list, tuple)) and len(origin) == 3:
            exploration_center = self._next_leather_exploration_center(
                origin,
                state,
            )
        result = None
        returned = False
        try:
            result = hunt_mobs(
                client,
                # All passive overworld mobs that directly drop leather. This
                # prevents a valid enchanting run from depending exclusively
                # on finding cows in one explored region.
                mob_types=[
                    "cow",
                    "mooshroom",
                    "horse",
                    "donkey",
                    "mule",
                    "llama",
                ],
                required_loot={"minecraft:leather": missing},
                search_radius=64,
                timeout=600,
                heal_threshold=10.0,
                abort_on_other_hostiles=True,
                latest_world_time=9000,
                max_distance_from_origin=160.0,
                exploration_center=exploration_center,
            )
        finally:
            client.transport.dispatch("cancel", {})
            live_state = client.transport.dispatch("get_state", {})
            if int(live_state.get("world_time", 0)) % 24000 >= 12000:
                print("  Return window missed; sheltering until daylight.")
                wait_for_safe_daylight(client, max_wait=720.0)
            # The phase only needs the carried leather. If the initial home
            # route was unavailable, repeating that same route after a local
            # hunt merely discards real resource progress.
            returned = local_expedition or self._return_home(client, state)

        if (
            result
            and result.success
            and returned
            and count_item(client, "minecraft:leather") >= target
        ):
            leather_supply.record_attempt(state, gained=True)
            return True

        # The local bounded search above rotates through dozens of sectors
        # around the base and can still fail entirely if this biome has no
        # huntable animals anywhere in range -- confirmed live: Bot07 spent
        # 36+ sector rotations finding zero cows. Fall back to the
        # operator-known distant herd instead of retrying the same empty
        # area forever.
        deficit = target - count_item(client, "minecraft:leather")
        if deficit > 0 and not leather_supply.herd_waypoint_is_exhausted(state):
            # preserve_breeding_pair leaves two adults alive. Without it a
            # 46-leather target is ~46 kills with no floor, which strips the
            # herd that food recovery and every later leather run depend on;
            # passive mobs do not respawn in already-generated chunks, so an
            # exterminated waypoint is gone permanently.
            reached = visit_known_herd_for_loot(
                client,
                {"minecraft:leather": deficit},
                "cow",
                preserve_breeding_pair=True,
            )
            if reached:
                self._return_home(client, state)
            else:
                leather_supply.mark_herd_waypoint_exhausted(state)

        final = count_item(client, "minecraft:leather")
        leather_supply.record_attempt(state, gained=final > current)
        return final >= target

    def _next_leather_exploration_center(self, origin, state=None):
        """Rotate retries durably across sectors surrounding the starter house.

        Every offset used to have a negative dz (north-only). A base whose
        northern terrain lacks passive-mob biome (ocean, desert, extreme
        hills) could rotate through every north sector forever and never
        find a single cow -- confirmed live: Bot07 logged dozens of "No
        targets found" across many distinct north sectors with zero hits.
        Mirroring south covers the case where the huntable biome is instead
        south of base.
        """
        offsets = (
            (0, -128),
            (128, -64),
            (-128, -64),
            (128, -24),
            (-128, -24),
            (64, -120),
            (-64, -120),
            (96, -96),
            (-96, -96),
            (64, -48),
            (-64, -48),
            (0, 128),
            (128, 64),
            (-128, 64),
            (128, 24),
            (-128, 24),
            (64, 120),
            (-64, 120),
            (96, 96),
            (-96, 96),
            (64, 48),
            (-64, 48),
        )
        expedition_index = self._leather_expedition_index
        if state is not None:
            expedition_state = state.custom_data.setdefault("expeditions", {})
            expedition_index = int(
                expedition_state.get("leather_sector_index", expedition_index)
            )
            expedition_state["leather_sector_index"] = expedition_index + 1
        self._leather_expedition_index = expedition_index + 1
        dx, dz = offsets[expedition_index % len(offsets)]
        return int(origin[0]) + 3 + dx, int(origin[2]) + dz

    def _withdraw_at_home(self, client, state: StateManager, requirements) -> int:
        house = state.custom_data.get("structures", {}).get("starter_house", {})
        chest = house.get("supply_chest")
        if not isinstance(chest, (list, tuple)) or len(chest) != 3:
            return 0
        live_state = client.transport.dispatch("get_state", {})
        position = live_state.get("block_position", live_state.get("position", {}))
        if all(axis in position for axis in ("x", "y", "z")):
            distance = sum(
                (float(position[axis]) - float(chest[index])) ** 2
                for index, axis in enumerate(("x", "y", "z"))
            ) ** 0.5
            if distance > 4.5:
                # A resumed phase can start at an interrupted expedition
                # location.  Owned storage must be reached through the
                # checkpointed doorway, never opened remotely or via a direct
                # goal to the chest block.
                if int(live_state.get("world_time", 0)) % 24000 >= 12000:
                    if not wait_for_safe_daylight(client, max_wait=720.0):
                        return -1
                if not self._return_home(client, state):
                    return -1
        return withdraw_required_from_chest(client, tuple(chest), requirements)

    def _wait_for_daylight(
        self,
        client,
        state: StateManager = None,
        timeout: float = 900.0,
    ) -> bool:
        """Wait for a morning window with time left for a safe return."""
        deadline = time.monotonic() + timeout
        client.transport.dispatch("cancel", {})
        live_state = client.transport.dispatch("get_state", {})
        if (
            int(live_state.get("world_time", 0)) % 24000 >= 7000
            and state is not None
            and not self._stage_inside_house(client, state)
        ):
            print("  Could not stage safely away from the doorway for night wait.")
            if not wait_for_safe_daylight(client, max_wait=720.0):
                return False
            print("  Doorway staging failed, but day returned and we can resume.")
            return True
        while time.monotonic() < deadline:
            live_state = client.transport.dispatch("get_state", {})
            day_time = int(live_state.get("world_time", 0)) % 24000
            if day_time < 7000:
                return True
            if day_time >= 12000:
                return wait_for_safe_daylight(
                    client,
                    max_wait=max(1.0, deadline - time.monotonic()),
                )
            print(
                f"  Too late to begin expedition safely (time={day_time}); "
                "waiting inside for nightfall and next dawn."
            )
            time.sleep(5.0)
        return False

    def _stage_inside_house(self, client, state: StateManager) -> bool:
        """Hold on a roofed interior tile instead of the vulnerable door block."""
        house = state.custom_data.get("structures", {}).get("starter_house", {})
        origin = house.get("origin") or state.custom_data.get("base_location")
        if not isinstance(origin, (list, tuple)) or len(origin) != 3:
            return False
        x, y, z = (int(value) for value in origin)
        target = (x + 3, y + 1, z + 4)
        for check_y in (target[1], target[1] + 1):
            block_id = client.transport.dispatch(
                "get_block",
                {"x": target[0], "y": check_y, "z": target[2]},
            ).get("id", "")
            if "air" not in block_id:
                return False
        live_state = client.transport.dispatch("get_state", {})
        position = live_state.get("block_position", live_state.get("position", {}))
        if (
            abs(float(position.get("x", 0)) - target[0]) <= 1.5
            and abs(float(position.get("y", target[1])) - target[1]) <= 0.75
            and abs(float(position.get("z", 0)) - target[2]) <= 1.5
        ):
            return True
        client.transport.dispatch("chat", {"message": "#set allowBreak false"})
        try:
            return goto(
                client,
                target[0],
                target[1],
                target[2],
                timeout=20,
                check_interval=0.5,
                tolerance=0.5,
            )
        finally:
            client.transport.dispatch("cancel", {})
            client.transport.dispatch("chat", {"message": "#set allowBreak true"})

    def _bank_diamonds_before_expedition(self, client, state: StateManager) -> None:
        """Leave carried raw diamonds at home before a trip that doesn't need
        them, so a field death can't drop banked wealth. Best-effort: a
        missing or unreachable chest just leaves diamonds carried as before.
        """
        if count_item(client, "minecraft:diamond") <= 0:
            return
        house = state.custom_data.get("structures", {}).get("starter_house", {})
        chest = house.get("supply_chest")
        if not isinstance(chest, (list, tuple)) or len(chest) != 3:
            return
        deposit_excess_to_chest(
            client,
            tuple(chest),
            deposit_items={"minecraft:diamond"},
            state=state,
        )

    def _leave_starter_house(self, client, state: StateManager) -> bool:
        """Exit through the checkpointed door without allowing wall damage."""
        house = state.custom_data.get("structures", {}).get("starter_house", {})
        origin = house.get("origin") or state.custom_data.get("base_location")
        if not isinstance(origin, (list, tuple)) or len(origin) != 3:
            return False
        x, y, z = (int(value) for value in origin)
        door = house.get("door") or [x + 3, y + 1, z]
        door = tuple(int(value) for value in door)
        outside = (door[0], door[1], door[2] - 2)
        clear_of_house = self._exterior_staging_tile(client, door)
        if clear_of_house is None:
            print("  No clear exterior staging tile found beyond the doorway.")
            return False

        client.transport.dispatch("cancel", {})
        client.transport.dispatch("chat", {"message": "#set allowBreak false"})
        time.sleep(0.25)
        exited = False
        try:
            door_block = client.transport.dispatch(
                "get_block", {"x": door[0], "y": door[1], "z": door[2]}
            )
            if "door" not in door_block.get("id", ""):
                print(f"  Starter-house door is missing at {door}.")
                return False
            if str(door_block.get("state", {}).get("open", "false")).lower() != "true":
                client.transport.dispatch(
                    "look_at",
                    {"x": door[0] + 0.5, "y": door[1] + 0.5, "z": door[2] + 0.5},
                )
                client.transport.dispatch(
                    "interact_block", {"x": door[0], "y": door[1], "z": door[2]}
                )
                time.sleep(0.25)
            exited = goto(
                client,
                outside[0],
                outside[1],
                outside[2],
                timeout=30,
                check_interval=0.5,
                tolerance=0.25,
            )
            if exited:
                door_block = client.transport.dispatch(
                    "get_block", {"x": door[0], "y": door[1], "z": door[2]}
                )
                if str(door_block.get("state", {}).get("open", "false")).lower() == "true":
                    client.transport.dispatch(
                        "interact_block", {"x": door[0], "y": door[1], "z": door[2]}
                    )
                # Do not begin Baritone exploration on the doorway threshold:
                # its first random goal can point back into the closed house,
                # leaving the player jumping against the door indefinitely.
                exited = goto(
                    client,
                    clear_of_house[0],
                    clear_of_house[1],
                    clear_of_house[2],
                    timeout=30,
                    check_interval=0.5,
                    tolerance=0.75,
                )
        finally:
            client.transport.dispatch("cancel", {})
            client.transport.dispatch("chat", {"message": "#set allowBreak true"})
        return exited

    def _exterior_staging_tile(self, client, door):
        """Find clear terrain beyond or beside a checkpointed front door."""
        door_x, door_y, door_z = (int(value) for value in door)
        # Savanna ridges and mangrove roots can obstruct the single column
        # directly in front of an otherwise usable door. Probe a small,
        # deterministic fan that remains clear of the house footprint.
        for forward in (6, 4):
            candidate_z = door_z - forward
            for lateral in (0, -2, 2, -4, 4):
                candidate_x = door_x + lateral
                standing_y = self._surface_staging_y(
                    client, candidate_x, door_y, candidate_z
                )
                if standing_y is not None:
                    return candidate_x, standing_y, candidate_z
        return None

    def _surface_staging_y(self, client, x: int, nominal_y: int, z: int):
        """Find the standing Y for nearby terrain that rises or falls a block."""
        for foot_y in range(nominal_y + 2, nominal_y - 5, -1):
            below = client.transport.dispatch(
                "get_block", {"x": x, "y": foot_y - 1, "z": z}
            ).get("id", "")
            feet = client.transport.dispatch(
                "get_block", {"x": x, "y": foot_y, "z": z}
            ).get("id", "")
            head = client.transport.dispatch(
                "get_block", {"x": x, "y": foot_y + 1, "z": z}
            ).get("id", "")
            if "air" not in below and "air" in feet and "air" in head:
                return foot_y
        return None

    def _return_home(self, client, state: StateManager) -> bool:
        # Reuse the live-proven staged doorway traversal until it is promoted
        # into a shared base-navigation helper.
        from .iron_age import FoodAndIronHandler

        return FoodAndIronHandler()._return_to_base(client, state)

    def _harvest_sugarcane(self, client, state: StateManager) -> bool:
        """Accumulate and craft the paper needed by all 46 books."""
        paper_target = 138
        withdrawn = self._withdraw_at_home(
            client,
            state,
            {
                "minecraft:paper": paper_target,
                "minecraft:sugar_cane": paper_target,
            },
        )
        if withdrawn < 0:
            print("  Sugarcane expedition paused: could not return to home storage.")
            return False
        if self._craft_available_paper(client, paper_target):
            return True

        paper_shortfall = paper_target - count_item(client, "minecraft:paper")
        cane_target = ((paper_shortfall + 2) // 3) * 3
        if not self._wait_for_daylight(client, state):
            return False
        if not self._leave_starter_house(client, state):
            return False

        house = state.custom_data.get("structures", {}).get("starter_house", {})
        origin = house.get("origin") or state.custom_data.get("base_location")
        exploration_center = None
        if isinstance(origin, (list, tuple)) and len(origin) == 3:
            exploration_center = self._next_cane_exploration_center(
                origin,
                state,
            )
        gathered = False
        returned = False
        try:
            gathered = self._gather_sugar_cane(
                client,
                cane_target,
                exploration_center=exploration_center,
            )
        finally:
            client.transport.dispatch("cancel", {})
            returned = self._return_home(client, state)

        # Keep and craft partial progress even when sunset or an exhausted
        # search region ends this attempt.  The next phase retry resumes from
        # the paper already made rather than throwing the day away.
        complete = self._craft_available_paper(client, paper_target)
        return bool(gathered and returned and complete)

    def _next_cane_exploration_center(self, origin, state):
        """Persist fleet-sharded, all-direction water-edge search sectors."""
        offsets = (
            (0, -128), (96, -96), (128, 0), (96, 96),
            (0, 128), (-96, 96), (-128, 0), (-96, -96),
            (0, -64), (64, 0), (0, 64), (-64, 0),
            (48, -112), (112, -48), (112, 48), (48, 112),
            (-48, 112), (-112, 48), (-112, -48), (-48, -112),
        )
        expedition_state = state.custom_data.setdefault("expeditions", {})
        index = int(expedition_state.get("sugar_cane_sector_index", 0))
        expedition_state["sugar_cane_sector_index"] = index + 1
        # Nearby fleet houses otherwise start at the same sector and retrace
        # one another's exhausted ground. Use the BotNN run directory as a
        # stable shard while keeping the persisted counter restart-safe.
        checkpoint_dir = getattr(state, "checkpoint_dir", None)
        bot_name = ""
        if checkpoint_dir is not None:
            path = Path(checkpoint_dir)
            bot_dir = path.parent if path.name == "controller" else path
            bot_name = bot_dir.name
        digits = "".join(character for character in bot_name if character.isdigit())
        shard = int(digits) if digits else 0
        dx, dz = offsets[(index + shard) % len(offsets)]
        return int(origin[0]) + 3 + dx, int(origin[2]) + dz

    def _craft_available_paper(self, client, target: int) -> bool:
        current = count_item(client, "minecraft:paper")
        if current >= target:
            return True
        cane = count_item(client, "minecraft:sugar_cane")
        craftable = min(target - current, (cane // 3) * 3)
        if craftable > 0:
            client.transport.dispatch("close_screen", {})
            craft(client, "minecraft:paper", craftable)
        if count_item(client, "minecraft:paper") >= target:
            return True

        current = count_item(client, "minecraft:paper")
        cane = count_item(client, "minecraft:sugar_cane")
        recipes = min(
            (target - current + 2) // 3,
            cane // 3,
        )
        if recipes > 0 and harness_ops.ensure_crafting_table_open(client):
            harness_ops.craft_recipe_manual(
                client,
                "minecraft:paper",
                [
                    ("minecraft:sugar_cane", 1),
                    ("minecraft:sugar_cane", 2),
                    ("minecraft:sugar_cane", 3),
                ],
                crafts=recipes,
                output_per_recipe=3,
            )
        return count_item(client, "minecraft:paper") >= target

    def _craft_books(self, client, target: int = 46) -> bool:
        """Craft all shelf/table books with an explicit shapeless layout."""
        current = count_item(client, "minecraft:book")
        if current >= target:
            return True
        craft(client, "minecraft:book", target - current)
        current = count_item(client, "minecraft:book")
        if current >= target:
            return True
        recipes = min(
            target - current,
            count_item(client, "minecraft:paper") // 3,
            count_item(client, "minecraft:leather"),
        )
        if recipes <= 0 or not harness_ops.ensure_crafting_table_open(client):
            return False
        harness_ops.craft_recipe_manual(
            client,
            "minecraft:book",
            [
                ("minecraft:paper", 1),
                ("minecraft:paper", 2),
                ("minecraft:paper", 3),
                ("minecraft:leather", 4),
            ],
            crafts=recipes,
        )
        return count_item(client, "minecraft:book") >= target

    def _craft_enchanting_table(self, client, state: StateManager) -> bool:
        """Craft the table only when its survival-obtained inputs are verified."""
        table_needed = count_item(client, "minecraft:enchanting_table") < 1
        if table_needed:
            # A table already standing in the world is just as usable as one
            # in the bag. This only asked whether the bot *carried* a table,
            # so a placed one -- built on an earlier run, or by an operator --
            # was invisible, and the phase spent 2 diamonds and 4 obsidian
            # rebuilding it. _ensure_sleeping_bed already scans this way for
            # beds a few methods below; this mirrors it.
            existing = find_nearby_block(
                client, ["minecraft:enchanting_table"], radius=64
            )
            if existing is not None:
                house = state.custom_data.setdefault("structures", {}).setdefault(
                    "starter_house", {}
                )
                house["enchanting_table"] = list(existing)
                print(f"  Reusing existing enchanting table at {tuple(existing)}")
                table_needed = False
        if count_item(client, "minecraft:diamond_pickaxe") < 1:
            diamond_target = 3 + (2 if table_needed else 0)
            self._withdraw_at_home(
                client,
                state,
                {"minecraft:diamond": diamond_target},
            )
            if count_item(client, "minecraft:diamond") < diamond_target:
                print(
                    f"  Need {diamond_target} diamonds for the required pickaxe"
                    + (" and enchanting table." if table_needed else ".")
                )
                return False
            if not _craft_with_table(client, "minecraft:diamond_pickaxe", 1):
                return False
        if not table_needed:
            return True
        self._withdraw_at_home(
            client,
            state,
            {
                "minecraft:book": 1,
                "minecraft:diamond": 2,
                "minecraft:obsidian": 4,
            },
        )
        if count_item(client, "minecraft:obsidian") < 4:
            if not self._wait_for_daylight(client, state):
                return False
            if not self._leave_starter_house(client, state):
                return False
            mined = False
            returned = False
            try:
                mined = self._mine_obsidian(client, target=4)
            finally:
                client.transport.dispatch("cancel", {})
                returned = self._return_home(client, state)
            if not mined or not returned:
                return False
        requirements = {
            "minecraft:book": 1,
            "minecraft:diamond": 2,
            "minecraft:obsidian": 4,
        }
        missing = {
            item_id: target - count_item(client, item_id)
            for item_id, target in requirements.items()
            if count_item(client, item_id) < target
        }
        if missing:
            print(f"  Enchanting table inputs still missing: {missing}")
            return False
        if not harness_ops.ensure_crafting_table_open(client):
            return False
        harness_ops.craft_recipe_manual(
            client,
            "minecraft:enchanting_table",
            [
                ("minecraft:book", 2),
                ("minecraft:diamond", 4),
                ("minecraft:obsidian", 5),
                ("minecraft:diamond", 6),
                ("minecraft:obsidian", 7),
                ("minecraft:obsidian", 8),
                ("minecraft:obsidian", 9),
            ],
        )
        return count_item(client, "minecraft:enchanting_table") >= 1

    def _mine_obsidian(
        self,
        client,
        target: int = 4,
        timeout: float = 300.0,
    ) -> bool:
        """Mine known obsidian with bounded health, hunger, and daylight checks."""
        if count_item(client, "minecraft:obsidian") >= target:
            return True
        if count_item(client, "minecraft:diamond_pickaxe") < 1:
            return False
        client.transport.dispatch("cancel", {})
        time.sleep(0.75)
        client.transport.dispatch(
            "mine",
            {
                "blocks": ["minecraft:obsidian"],
                "quantity": target + 2,
            },
        )
        deadline = time.monotonic() + timeout
        idle_checks = 0
        while time.monotonic() < deadline:
            if count_item(client, "minecraft:obsidian") >= target:
                client.transport.dispatch("cancel", {})
                return True
            live_state = client.transport.dispatch("get_state", {})
            if float(live_state.get("health", 20.0)) < 14.0:
                client.transport.dispatch("cancel", {})
                return False
            if int(live_state.get("food_level", 20)) <= 10:
                client.transport.dispatch("cancel", {})
                from ...common.combat import eat_until_hunger

                if not eat_until_hunger(client, minimum_food=14):
                    return False
                idle_checks = 0
                client.transport.dispatch(
                    "mine",
                    {
                        "blocks": ["minecraft:obsidian"],
                        "quantity": max(
                            1,
                            target
                            - count_item(client, "minecraft:obsidian")
                            + 2,
                        ),
                    },
                )
                continue
            if int(live_state.get("world_time", 0)) % 24000 >= 9000:
                client.transport.dispatch("cancel", {})
                return False
            if scan_for_threats(client, radius=12):
                client.transport.dispatch("cancel", {})
                return False
            if live_state.get("is_pathing", False):
                idle_checks = 0
            else:
                idle_checks += 1
                if idle_checks >= 4:
                    client.transport.dispatch("cancel", {})
                    print("  No reachable known obsidian in the bounded search.")
                    return False
            time.sleep(2.0)
        client.transport.dispatch("cancel", {})
        return count_item(client, "minecraft:obsidian") >= target

    def _craft_bookshelves(
        self,
        client,
        state: StateManager,
        target: int = 15,
    ) -> bool:
        """Craft the verified level-30 shelf count without recipe listing."""
        current = count_item(client, "minecraft:bookshelf")
        if current >= target:
            return True
        if not self._ensure_bookshelf_planks(client, state, target * 6):
            return False
        books = count_item(client, "minecraft:book")
        planks = sum(count_item(client, item_id) for item_id in PLANK_ITEMS)
        recipes = min(target - current, books // 3, planks // 6)
        if recipes <= 0:
            print(
                f"  Bookshelf inputs missing: books={books}/45, planks={planks}/90"
            )
            return False
        if not harness_ops.ensure_crafting_table_open(client):
            return False
        harness_ops.craft_recipe_manual(
            client,
            "minecraft:bookshelf",
            [
                ("#planks", 1),
                ("#planks", 2),
                ("#planks", 3),
                ("minecraft:book", 4),
                ("minecraft:book", 5),
                ("minecraft:book", 6),
                ("#planks", 7),
                ("#planks", 8),
                ("#planks", 9),
            ],
            crafts=recipes,
        )
        return count_item(client, "minecraft:bookshelf") >= target

    def _ensure_bookshelf_planks(
        self,
        client,
        state: StateManager,
        target: int = 90,
    ) -> bool:
        """Gather logs outside, return home, and turn them into shelf planks."""
        planks = sum(count_item(client, item_id) for item_id in PLANK_ITEMS)
        if planks >= target:
            return True
        log_equivalent_target = (target + 3) // 4
        if not self._wait_for_daylight(client, state):
            return False
        self._bank_diamonds_before_expedition(client, state)
        if not self._leave_starter_house(client, state):
            return False
        gathered = False
        returned = False
        try:
            gathered = gather_wood(
                client,
                count=log_equivalent_target,
                timeout=300,
                latest_world_time=9000,
                max_distance_from_origin=160.0,
                abort_on_threats=True,
                minimum_health=14.0,
            )
        finally:
            client.transport.dispatch("cancel", {})
            returned = self._return_home(client, state)
        if not gathered or not returned:
            return False

        for log_id, plank_id in LOG_TO_PLANKS.items():
            log_count = count_item(client, log_id)
            if log_count <= 0:
                continue
            shortfall = target - sum(
                count_item(client, item_id) for item_id in PLANK_ITEMS
            )
            if shortfall <= 0:
                break
            craft(client, plank_id, min(shortfall, log_count * 4))

        planks = sum(count_item(client, item_id) for item_id in PLANK_ITEMS)
        if planks >= target:
            return True

        # Recipe listing is absent on the live client; explicitly convert any
        # remaining logs one recipe at a time if bridge crafting no-ops.
        for log_id, plank_id in LOG_TO_PLANKS.items():
            log_count = count_item(client, log_id)
            if log_count <= 0:
                continue
            shortfall = target - sum(
                count_item(client, item_id) for item_id in PLANK_ITEMS
            )
            recipes = min(log_count, (shortfall + 3) // 4)
            if recipes <= 0:
                break
            if not harness_ops.ensure_crafting_table_open(client):
                return False
            harness_ops.craft_recipe_manual(
                client,
                plank_id,
                [(log_id, 1)],
                crafts=recipes,
                output_per_recipe=4,
            )
        return sum(count_item(client, item_id) for item_id in PLANK_ITEMS) >= target

    def _gather_sugar_cane(
        self,
        client,
        target: int,
        timeout: float = 600.0,
        exploration_center=None,
        latest_world_time: int = 9000,
        max_distance_from_origin: float = 160.0,
    ) -> bool:
        """Mine known cane, exploring in bounded daytime legs when needed."""
        deadline = time.monotonic() + timeout
        failed_searches = 0
        start_state = client.transport.dispatch("get_state", {})
        start_position = start_state.get(
            "block_position",
            start_state.get("position", {}),
        )
        origin_x = float(start_position.get("x", 0))
        origin_z = float(start_position.get("z", 0))

        def unsafe_to_continue(live_state) -> bool:
            day_time = int(live_state.get("world_time", 0)) % 24000
            if day_time >= latest_world_time:
                print("  Daylight return boundary reached during sugar-cane search.")
                return True
            if float(live_state.get("health", 20.0)) < 12.0:
                return True
            position = live_state.get(
                "block_position",
                live_state.get("position", {}),
            )
            distance = (
                (float(position.get("x", 0)) - origin_x) ** 2
                + (float(position.get("z", 0)) - origin_z) ** 2
            ) ** 0.5
            if distance > max_distance_from_origin:
                print("  Sugar-cane expedition radius reached; returning home.")
                return True
            food_level = int(
                live_state.get("food_level", live_state.get("food", 20))
            )
            if food_level <= 10:
                client.transport.dispatch("cancel", {})
                if not eat_until_hunger(client, minimum_food=14):
                    return True
            if scan_for_threats(client, radius=12):
                print("  Hostile entered sugar-cane search radius; returning home.")
                return True
            return False

        while time.monotonic() < deadline:
            current = count_item(client, "minecraft:sugar_cane")
            if current >= target:
                client.transport.dispatch("cancel", {})
                return True

            live_state = client.transport.dispatch("get_state", {})
            if unsafe_to_continue(live_state):
                client.transport.dispatch("chat", {"message": "#stop"})
                client.transport.dispatch("cancel", {})
                return False

            client.transport.dispatch("cancel", {})
            time.sleep(0.75)
            client.transport.dispatch(
                "mine",
                {
                    "blocks": ["minecraft:sugar_cane"],
                    "quantity": max(1, target - current + 2),
                },
            )

            last_count = current
            idle_checks = 0
            mine_deadline = min(deadline, time.monotonic() + 45.0)
            while time.monotonic() < mine_deadline:
                time.sleep(2.0)
                current = count_item(client, "minecraft:sugar_cane")
                if current >= target:
                    client.transport.dispatch("cancel", {})
                    return True
                live_state = client.transport.dispatch("get_state", {})
                if unsafe_to_continue(live_state):
                    client.transport.dispatch("chat", {"message": "#stop"})
                    client.transport.dispatch("cancel", {})
                    return False
                if current > last_count:
                    last_count = current
                    idle_checks = 0
                elif not live_state.get("is_pathing", False):
                    idle_checks += 1
                else:
                    idle_checks = 0
                if idle_checks >= 3:
                    break

            client.transport.dispatch("cancel", {})
            time.sleep(0.75)
            print("  No more known sugar cane; exploring for another patch...")
            if exploration_center is not None:
                client.transport.dispatch(
                    "explore",
                    {
                        "x": int(exploration_center[0]),
                        "z": int(exploration_center[1]),
                    },
                )
            else:
                client.transport.dispatch("chat", {"message": "#explore"})
            explore_deadline = min(deadline, time.monotonic() + 60.0)
            found = False
            while time.monotonic() < explore_deadline:
                time.sleep(2.0)
                live_state = client.transport.dispatch("get_state", {})
                if unsafe_to_continue(live_state):
                    client.transport.dispatch("chat", {"message": "#stop"})
                    return False
                nearby = find_nearby_block(
                    client,
                    ["minecraft:sugar_cane"],
                    radius=64,
                )
                if nearby is not None:
                    client.transport.dispatch("chat", {"message": "#stop"})
                    found = True
                    break
            if not found:
                failed_searches += 1
                client.transport.dispatch("chat", {"message": "#stop"})
                if failed_searches >= 3:
                    return False
            else:
                failed_searches = 0
        client.transport.dispatch("cancel", {})
        return count_item(client, "minecraft:sugar_cane") >= target

    @staticmethod
    def _enchanting_layout(center):
        x, y, z = (int(value) for value in center)
        shelves = []
        for dx in range(-2, 3):
            shelves.append((x + dx, y, z - 2))
            shelves.append((x + dx, y, z + 2))
        for dz in range(-1, 2):
            shelves.append((x - 2, y, z + dz))
            shelves.append((x + 2, y, z + dz))
        # Leave one cardinal opening as the player's entrance.
        shelves.remove((x, y, z + 2))
        return shelves

    @staticmethod
    def _block_id(client, position) -> str:
        response = client.transport.dispatch(
            "get_block",
            {"x": int(position[0]), "y": int(position[1]), "z": int(position[2])},
        )
        return str(response.get("id") or response.get("block") or "")

    def _station_verified(self, client, center) -> bool:
        if not isinstance(center, (list, tuple)) or len(center) != 3:
            return False
        x, y, z = (int(value) for value in center)
        if self._block_id(client, (x, y, z)) != "minecraft:enchanting_table":
            return False
        shelves = self._enchanting_layout((x, y, z))
        if any(
            self._block_id(client, position) != "minecraft:bookshelf"
            for position in shelves
        ):
            return False
        air = {"minecraft:air", "minecraft:cave_air", "minecraft:void_air", ""}
        for sx, sy, sz in shelves:
            gap = (x + (sx - x) // 2, sy, z + (sz - z) // 2)
            if self._block_id(client, gap) not in air:
                return False
        return True

    def _build_enchanting_room(self, client, state: StateManager) -> bool:
        """Place and verify a table with exactly fifteen powered shelves."""
        structures = state.custom_data.setdefault("structures", {})
        existing = structures.get("enchanting_station", {})
        if isinstance(existing, dict) and self._station_verified(
            client, existing.get("table")
        ):
            existing["verified"] = True
            return True

        house = structures.get("starter_house", {})
        origin = house.get("origin") or state.custom_data.get("base_location")
        if not isinstance(origin, (list, tuple)) or len(origin) != 3:
            return False
        ox, oy, oz = (int(value) for value in origin)
        candidates = (
            (ox + 10, oy + 1, oz + 3),
            (ox - 4, oy + 1, oz + 3),
            (ox + 3, oy + 1, oz + 10),
            (ox + 3, oy + 1, oz - 4),
        )
        air = {"minecraft:air", "minecraft:cave_air", "minecraft:void_air", ""}
        center = None
        for candidate in candidates:
            targets = [candidate, *self._enchanting_layout(candidate)]
            if any(self._block_id(client, target) not in air for target in targets):
                continue
            if any(self._block_id(client, (x, y - 1, z)) in air for x, y, z in targets):
                continue
            center = candidate
            break
        if center is None:
            return False
        if not goto(client, *center, timeout=180, tolerance=3.0):
            return False
        if not harness_ops.place_block_exact(
            client, *center, "minecraft:enchanting_table", allow_break=False
        ):
            return False
        for position in self._enchanting_layout(center):
            if not harness_ops.place_block_exact(
                client, *position, "minecraft:bookshelf", allow_break=False
            ):
                return False
        if not self._station_verified(client, center):
            return False
        structures["enchanting_station"] = {
            "table": list(center),
            "bookshelves": [list(value) for value in self._enchanting_layout(center)],
            "verified": True,
        }
        state.add_location(
            "enchanting_station",
            *center,
            dimension="overworld",
            tags=["level_30", "verified"],
        )
        self._persist_enchanting_state(client, state)
        return True

    def _roll_enchants(self, client, state: StateManager) -> bool:
        """Verify and persist that the station has level-30 shelf power."""
        station = state.custom_data.get("structures", {}).get(
            "enchanting_station", {}
        )
        center = station.get("table") if isinstance(station, dict) else None
        if not self._station_verified(client, center):
            return False
        state.custom_data.setdefault("capabilities", {})[
            "level_30_enchanting"
        ] = True
        payload = state.get_phase_payload(Phase.ENCHANTING_PIPELINE)
        payload.update({"station_verified": True, "level_30_ready": True})
        state.record_phase_payload(Phase.ENCHANTING_PIPELINE, payload)
        self._persist_enchanting_state(client, state)
        return True
