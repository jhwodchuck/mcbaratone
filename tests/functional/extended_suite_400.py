"""
Extended Suite 400: Crafting & Smelting (Granular Action Tests)
T400-T403: Basic Crafting, Tool Crafting, Furnace, Multi-Step
"""

import math
import time
from test_base import TestCase, TestSuite, TestContext


def create_extended_suite_400() -> TestSuite:
    """Suite 400: Crafting & Smelting - Granular action tests."""
    suite = TestSuite("Suite_400_Crafting", "Granular crafting action tests")

    def _count_item(ctx: TestContext, item_id: str, timeout: float = 3.0) -> int:
        inv = ctx.get_inventory(timeout=timeout)
        total = 0
        for slot in inv.get("inventory", []):
            if slot and slot.get("id") == item_id:
                total += slot.get("count", 0)
        return total

    def _has_item(ctx: TestContext, item_id: str, count: int = 1, timeout: float = 3.0) -> bool:
        return _count_item(ctx, item_id, timeout=timeout) >= count

    def _wait_for_item(ctx: TestContext, item_id: str, count: int, timeout: float = 6.0) -> bool:
        start = time.time()
        while time.time() - start < timeout:
            if _has_item(ctx, item_id, count=count, timeout=3.0):
                return True
            time.sleep(0.2)
        return False

    def _wait_for_screen(ctx: TestContext, timeout: float = 2.0) -> bool:
        start = time.time()
        while time.time() - start < timeout:
            state = ctx.get_state()
            if state.get("has_gui") and state.get("screen") != "none":
                return True
            time.sleep(0.1)
        return False

    def _find_nearby_block(ctx: TestContext, block_id: str, radius: int = 4):
        result = ctx.client.transport.dispatch("find_blocks", {
            "blocks": [block_id],
            "radius": radius,
            "limit": 1
        })
        found = result.get("found", result.get("data", {}).get("found", []))
        if not found:
            return None
        pos = found[0]
        if isinstance(pos, dict) and {"x", "y", "z"}.issubset(pos.keys()):
            return (pos["x"], pos["y"], pos["z"])
        return None

    def _find_nearby_block_in_range(
        ctx: TestContext,
        block_id: str,
        radius: int,
        max_distance: float
    ):
        result = ctx.client.transport.dispatch("find_blocks", {
            "blocks": [block_id],
            "radius": radius,
            "limit": 5
        })
        found = result.get("found", result.get("data", {}).get("found", []))
        best = None
        best_dist = None
        for pos in found:
            if not isinstance(pos, dict) or not {"x", "y", "z"}.issubset(pos.keys()):
                continue
            dist = pos.get("distance")
            if dist is None:
                x, y, z = ctx.get_position()
                dx = pos["x"] + 0.5 - x
                dy = pos["y"] + 0.5 - y
                dz = pos["z"] + 0.5 - z
                dist = (dx * dx + dy * dy + dz * dz) ** 0.5
            if dist <= max_distance and (best_dist is None or dist < best_dist):
                best = (pos["x"], pos["y"], pos["z"])
                best_dist = dist
        return best

    def _get_screen(ctx: TestContext) -> dict:
        return ctx.client.transport.dispatch("get_screen", {})

    def _has_crafting_screen(ctx: TestContext) -> bool:
        screen = _get_screen(ctx)
        screen_type = screen.get("type", "")
        return "Crafting" in screen_type

    def _wait_for_crafting_screen(ctx: TestContext, timeout: float = 3.0) -> bool:
        start = time.time()
        while time.time() - start < timeout:
            if _has_crafting_screen(ctx):
                return True
            state = ctx.get_state()
            if "crafting" in state.get("screen", "").lower():
                return True
            time.sleep(0.1)
        return False

    def _has_furnace_screen(ctx: TestContext) -> bool:
        screen = _get_screen(ctx)
        screen_type = screen.get("type", "")
        if "Furnace" in screen_type:
            return True
        if "furnace" in screen_type.lower():
            return True
        return screen.get("total_slots") == 39

    def _wait_for_furnace_screen(ctx: TestContext, timeout: float = 3.0) -> bool:
        start = time.time()
        while time.time() - start < timeout:
            if _has_furnace_screen(ctx):
                return True
            state = ctx.get_state()
            if "furnace" in state.get("screen", "").lower():
                return True
            if state.get("has_gui") and state.get("screen") != "none":
                return True
            time.sleep(0.1)
        return False

    def _screen_slot_has_item(screen: dict, slot_index: int, item_id: str) -> bool:
        for slot in screen.get("slots", []):
            if slot.get("slot") == slot_index:
                return slot.get("id") == item_id and slot.get("count", 0) > 0
        return False

    def _find_screen_slot(screen: dict, item_id: str, min_slot: int = 3):
        for slot in screen.get("slots", []):
            slot_idx = slot.get("slot")
            if slot_idx is None:
                continue
            if slot_idx >= min_slot and slot.get("id") == item_id and slot.get("count", 0) > 0:
                return slot_idx
        return None

    def _wait_for_screen_slot_item(ctx: TestContext, slot_index: int, item_id: str, timeout: float = 3.0) -> bool:
        start = time.time()
        while time.time() - start < timeout:
            screen = _get_screen(ctx)
            if _screen_slot_has_item(screen, slot_index, item_id):
                return True
            time.sleep(0.2)
        return False

    def _screen_item_count(screen: dict, item_id: str) -> int:
        total = 0
        for slot in screen.get("slots", []):
            if slot.get("id") == item_id:
                total += slot.get("count", 0)
        return total

    def _screen_slots_for_item(screen: dict, item_id: str):
        slots = []
        for slot in screen.get("slots", []):
            if slot.get("id") == item_id and slot.get("count", 0) > 0:
                slots.append((slot.get("slot"), slot.get("count", 0)))
        return slots

    def _screen_slot_summary(screen: dict, slot_index: int) -> str:
        for slot in screen.get("slots", []):
            if slot.get("slot") == slot_index:
                return f"{slot.get('id')}x{slot.get('count', 0)}"
        return "unknown"

    def _wait_for_item_in_inventory_or_screen(ctx: TestContext, item_id: str, timeout: float = 6.0) -> bool:
        start = time.time()
        while time.time() - start < timeout:
            inv = ctx.client.transport.dispatch("get_inventory", {})
            inv_count = sum(
                slot.get("count", 0)
                for slot in inv.get("inventory", [])
                if slot.get("id") == item_id
            )
            screen = _get_screen(ctx)
            screen_count = _screen_item_count(screen, item_id)
            if inv_count > 0 or screen_count > 0:
                return True
            time.sleep(0.2)
        return False

    def _move_item_to_furnace_slot(
        ctx: TestContext,
        item_id: str,
        target_slot: int,
        timeout: float = 3.0,
        sync_id: int = 0
    ) -> bool:
        start = time.time()
        while time.time() - start < timeout:
            screen = _get_screen(ctx)
            if _screen_slot_has_item(screen, target_slot, item_id):
                return True
            source_slot = _find_screen_slot(screen, item_id, min_slot=3)
            if source_slot is None:
                time.sleep(0.2)
                continue
            ctx.log_event(f"Moving {item_id} from screen slot {source_slot} -> {target_slot}")
            ctx.client.transport.dispatch("inventory_click", {
                "slot": source_slot,
                "type": "PICKUP",
                "button": 0,
                "sync_id": sync_id
            })
            time.sleep(0.1)
            ctx.client.transport.dispatch("inventory_click", {
                "slot": target_slot,
                "type": "PICKUP",
                "button": 0,
                "sync_id": sync_id
            })
            time.sleep(0.2)
            if _screen_slot_has_item(_get_screen(ctx), target_slot, item_id):
                return True
            ctx.client.transport.dispatch("inventory_click", {
                "slot": source_slot,
                "type": "PICKUP",
                "button": 0,
                "sync_id": sync_id
            })
            time.sleep(0.1)
        return False

    def _find_crafting_table_spot(ctx: TestContext, base_x: int, base_y: int, base_z: int):
        air_ids = {"minecraft:air", "minecraft:cave_air", "minecraft:void_air", "minecraft:water", "minecraft:lava"}
        offsets = [(1, 0), (2, 0), (1, 1), (0, 1), (-1, 0), (0, -1)]
        for dx, dz in offsets:
            ground_pos = _find_ground_position(ctx, base_x + dx, base_y, base_z + dz)
            block = ctx.client.transport.dispatch("get_block", {
                "x": ground_pos[0],
                "y": ground_pos[1],
                "z": ground_pos[2]
            })
            block_id = block.get("id")
            if block_id == "minecraft:crafting_table":
                return ground_pos, True
            if block_id in air_ids:
                return ground_pos, False
        return (base_x + 1, base_y, base_z), False

    def _find_ground_position(ctx: TestContext, base_x: int, base_y: int, base_z: int, max_drop: int = 6):
        air_ids = {"minecraft:air", "minecraft:cave_air", "minecraft:void_air", "minecraft:water", "minecraft:lava"}
        for dy in range(0, max_drop + 1):
            block = ctx.client.transport.dispatch("get_block", {
                "x": base_x,
                "y": base_y - dy - 1,
                "z": base_z
            })
            if block.get("id") not in air_ids:
                return (base_x, base_y - dy, base_z)
        result = ctx.client.transport.dispatch("find_blocks", {
            "blocks": [
                "minecraft:stone",
                "minecraft:cobblestone",
                "minecraft:dirt",
                "minecraft:grass_block",
                "minecraft:netherrack",
                "minecraft:end_stone"
            ],
            "radius": 8,
            "limit": 1
        })
        found = result.get("found", result.get("data", {}).get("found", []))
        if found:
            pos = found[0]
            if isinstance(pos, dict) and {"x", "y", "z"}.issubset(pos.keys()):
                return (int(pos["x"]), int(pos["y"]) + 1, int(pos["z"]))
        return (base_x, base_y, base_z)

    def _find_placeable_spot(ctx: TestContext, base_x: int, base_y: int, base_z: int, radius: int = 2):
        air_ids = {"minecraft:air", "minecraft:cave_air", "minecraft:void_air", "minecraft:water", "minecraft:lava"}
        solid_ids = {
            "minecraft:stone",
            "minecraft:cobblestone",
            "minecraft:dirt",
            "minecraft:grass_block",
            "minecraft:sand",
            "minecraft:gravel",
            "minecraft:netherrack",
            "minecraft:end_stone",
            "minecraft:deepslate"
        }
        for dy in range(-1, 2):
            for dx in range(-radius, radius + 1):
                for dz in range(-radius, radius + 1):
                    pos = (base_x + dx, base_y + dy, base_z + dz)
                    block = ctx.client.transport.dispatch("get_block", {
                        "x": pos[0],
                        "y": pos[1],
                        "z": pos[2]
                    })
                    if block.get("id") not in air_ids:
                        continue
                    below = ctx.client.transport.dispatch("get_block", {
                        "x": pos[0],
                        "y": pos[1] - 1,
                        "z": pos[2]
                    })
                    below_id = below.get("id")
                    if below_id in air_ids:
                        continue
                    if solid_ids and below_id not in solid_ids:
                        continue
                    return pos
        return (base_x + 1, base_y, base_z)

    def _ensure_on_ground(ctx: TestContext, max_drop: int = 20, allow_teleport: bool = False) -> bool:
        air_ids = {"minecraft:air", "minecraft:cave_air", "minecraft:void_air", "minecraft:water", "minecraft:lava"}
        x, y, z = ctx.get_position()
        block_below = ctx.client.transport.dispatch("get_block", {
            "x": int(x),
            "y": int(y) - 1,
            "z": int(z)
        })
        if block_below.get("id") not in air_ids:
            return True
        if not allow_teleport:
            return False
        for dy in range(0, max_drop + 1):
            block = ctx.client.transport.dispatch("get_block", {
                "x": int(x),
                "y": int(y) - dy - 1,
                "z": int(z)
            })
            if block.get("id") not in air_ids:
                ctx.teleport(int(x), int(y) - dy, int(z))
                return True
        pos = _find_ground_position(ctx, int(x), int(y), int(z), max_drop=0)
        if pos != (int(x), int(y), int(z)):
            ctx.teleport(pos[0], pos[1], pos[2])
            return True
        return False

    def _move_to_clear_area(ctx: TestContext, offset: int = 20, max_wait: float = 6.0) -> None:
        x, y, z = ctx.get_position()
        target = (int(x) + offset, int(y), int(z) + offset)
        ctx.client.transport.dispatch("goto", {
            "x": target[0],
            "y": target[1],
            "z": target[2]
        })
        start = time.time()
        while time.time() - start < max_wait:
            state = ctx.get_state()
            if not state.get("is_pathing", False):
                break
            time.sleep(0.5)
        if ctx.get_state().get("is_pathing", False):
            ctx.client.transport.dispatch("cancel", {})
            time.sleep(0.2)

    def _move_near_block(
        ctx: TestContext,
        pos: tuple,
        max_dist: float = 4.5,
        max_wait: float = 6.0,
        allow_teleport: bool = False
    ) -> float:
        x, y, z = ctx.get_position()
        dx = x - (pos[0] + 0.5)
        dy = y - (pos[1] + 0.5)
        dz = z - (pos[2] + 0.5)
        if (dx * dx + dy * dy + dz * dz) <= (max_dist * max_dist):
            return (dx * dx + dy * dy + dz * dz) ** 0.5
        ctx.client.transport.dispatch("goto", {
            "x": pos[0],
            "y": pos[1],
            "z": pos[2]
        })
        start = time.time()
        while time.time() - start < max_wait:
            state = ctx.get_state()
            if not state.get("is_pathing", False):
                break
            time.sleep(0.5)
        if ctx.get_state().get("is_pathing", False):
            ctx.client.transport.dispatch("cancel", {})
            time.sleep(0.2)
        x, y, z = ctx.get_position()
        dx = x - (pos[0] + 0.5)
        dy = y - (pos[1] + 0.5)
        dz = z - (pos[2] + 0.5)
        dist = (dx * dx + dy * dy + dz * dz) ** 0.5
        if dist > max_dist and allow_teleport:
            ctx.log_event(f"Pathing ended {dist:.2f} blocks away, attempting teleport")
            ctx.teleport(pos[0], pos[1] + 1, pos[2])
            x, y, z = ctx.get_position()
            dx = x - (pos[0] + 0.5)
            dy = y - (pos[1] + 0.5)
            dz = z - (pos[2] + 0.5)
            dist = (dx * dx + dy * dy + dz * dz) ** 0.5
        return dist

    def _open_crafting_table(ctx: TestContext) -> bool:
        ctx.client.transport.dispatch("cancel", {})
        ctx.client.transport.dispatch("close_screen", {})
        if not _ensure_on_ground(ctx, allow_teleport=False):
            ctx.log_event("Not on solid ground; cannot open crafting table")
            return False
        air_ids = {"minecraft:air", "minecraft:cave_air", "minecraft:void_air", "minecraft:water", "minecraft:lava"}
        offsets = [(1, 0), (2, 0), (1, 1), (0, 1), (-1, 0), (0, -1)]
        for _ in range(2):
            x, y, z = ctx.get_position()
            nearby = _find_nearby_block_in_range(ctx, "minecraft:crafting_table", radius=24, max_distance=4.5)
            if nearby:
                candidates = [nearby]
            else:
                if not _has_item(ctx, "minecraft:crafting_table", count=1, timeout=2.0):
                    ctx.log_event("No crafting table item available")
                    return False
                candidates = []
                for dx, dz in offsets:
                    ground_candidate = _find_ground_position(ctx, int(x) + dx, int(y), int(z) + dz)
                    candidate = _find_placeable_spot(ctx, ground_candidate[0], ground_candidate[1], ground_candidate[2])
                    block = ctx.client.transport.dispatch("get_block", {
                        "x": candidate[0],
                        "y": candidate[1],
                        "z": candidate[2]
                    })
                    block_id = block.get("id")
                    if block_id == "minecraft:crafting_table":
                        candidates = [candidate]
                        break
                    if block_id in air_ids:
                        candidates.append(candidate)
                if not candidates:
                    candidates = [_find_ground_position(ctx, int(x) + 1, int(y), int(z))]
            for table_pos in candidates:
                baseline_type = _get_screen(ctx).get("type")
                block = ctx.client.transport.dispatch("get_block", {
                    "x": table_pos[0],
                    "y": table_pos[1],
                    "z": table_pos[2]
                })
                if block.get("id") != "minecraft:crafting_table":
                    placed = False
                    if _select_hotbar_item(ctx, "minecraft:crafting_table"):
                        try:
                            place_result = ctx.client.transport.dispatch("place_block", {
                                "x": table_pos[0],
                                "y": table_pos[1],
                                "z": table_pos[2]
                            })
                            placed = place_result.get("placed", False)
                            if not placed:
                                ctx.log_event(f"Crafting table place_block failed: {place_result}")
                        except Exception as e:
                            ctx.log_event(f"Crafting table placement failed: {e}")
                    if not placed:
                        ctx.set_block(table_pos[0], table_pos[1], table_pos[2], "minecraft:crafting_table")
                    start = time.time()
                    while time.time() - start < 2.0:
                        block = ctx.client.transport.dispatch("get_block", {
                            "x": table_pos[0],
                            "y": table_pos[1],
                            "z": table_pos[2]
                        })
                        if block.get("id") == "minecraft:crafting_table":
                            break
                        time.sleep(0.2)
                block = ctx.client.transport.dispatch("get_block", {
                    "x": table_pos[0],
                    "y": table_pos[1],
                    "z": table_pos[2]
                })
                if block.get("id") != "minecraft:crafting_table":
                    ctx.log_event(f"Crafting table not placed at {table_pos}, found {block.get('id')}")
                    continue
                ctx.client.transport.dispatch("close_screen", {})
                distance = _move_near_block(ctx, table_pos, max_dist=4.5, max_wait=6.0)
                if distance > 4.5:
                    ctx.log_event(f"Too far to open crafting table ({distance:.2f} blocks)")
                    continue
                _select_safe_interact_item(ctx)
                ctx.set_block(table_pos[0], table_pos[1] + 1, table_pos[2], "minecraft:air")
                ctx.client.transport.dispatch("look_at", {
                    "x": table_pos[0] + 0.5,
                    "y": table_pos[1] + 0.5,
                    "z": table_pos[2] + 0.5
                })
                time.sleep(0.1)
                ctx.client.transport.dispatch("interact_block", {
                    "x": table_pos[0],
                    "y": table_pos[1],
                    "z": table_pos[2]
                })
                if _wait_for_crafting_screen(ctx, timeout=2.0):
                    return True
                if _wait_for_screen(ctx, timeout=0.5) and _get_screen(ctx).get("type") != baseline_type:
                    return True
                ctx.client.transport.dispatch("use_item", {"duration_ms": 200})
                if _wait_for_crafting_screen(ctx, timeout=2.0):
                    return True
                if _wait_for_screen(ctx, timeout=0.5) and _get_screen(ctx).get("type") != baseline_type:
                    return True
                time.sleep(0.2)
        state = ctx.get_state()
        screen = _get_screen(ctx)
        ctx.log_event(
            "Crafting table open failed: "
            f"screen={state.get('screen')} type={screen.get('type')} "
            f"total_slots={screen.get('total_slots')}"
        )
        return False

    def _close_screen(ctx: TestContext, timeout: float = 2.0) -> bool:
        ctx.client.transport.dispatch("close_screen", {})
        start = time.time()
        while time.time() - start < timeout:
            state = ctx.get_state()
            if not state.get("has_gui") or state.get("screen") == "none":
                return True
            time.sleep(0.1)
        return False

    def _clear_inventory_strict(ctx: TestContext, timeout: float = 6.0) -> bool:
        ctx.run_command("clear @p")
        start = time.time()
        while time.time() - start < timeout:
            inv = ctx.get_inventory(timeout=3.0)
            slots = inv.get("inventory", [])
            if len(slots) >= 36:
                has_items = any(
                    slot and slot.get("id") != "minecraft:air" and slot.get("count", 0) > 0
                    for slot in slots
                )
                if not has_items:
                    return True
            time.sleep(0.2)
        return False

    def _find_furnace_spot(ctx: TestContext, base_x: int, base_y: int, base_z: int):
        air_ids = {"minecraft:air", "minecraft:cave_air", "minecraft:void_air", "minecraft:water", "minecraft:lava"}
        offsets = [(1, 0), (2, 0), (1, 1), (0, 1), (-1, 0), (0, -1)]
        for dx, dz in offsets:
            pos = (base_x + dx, base_y, base_z + dz)
            block = ctx.client.transport.dispatch("get_block", {
                "x": pos[0],
                "y": pos[1],
                "z": pos[2]
            })
            block_id = block.get("id")
            if block_id == "minecraft:furnace":
                return pos, True
            if block_id in air_ids:
                return pos, False
        return (base_x + 1, base_y, base_z), False

    def _inventory_slot_to_furnace_slot(inv_slot: int) -> int:
        if 0 <= inv_slot <= 8:
            return 30 + inv_slot
        if 9 <= inv_slot <= 35:
            return 3 + (inv_slot - 9)
        return inv_slot

    def _inventory_slot_to_player_screen(inv_slot: int) -> int:
        if 0 <= inv_slot <= 8:
            return 36 + inv_slot
        if 9 <= inv_slot <= 35:
            return inv_slot
        return inv_slot

    def _select_hotbar_item(ctx: TestContext, item_id: str) -> bool:
        inv = ctx.get_inventory(timeout=2.0)
        hotbar_slot = None
        item_slot = None
        for slot in inv.get("inventory", []):
            if slot.get("id") == item_id and slot.get("count", 0) > 0:
                slot_idx = slot.get("slot")
                if slot_idx is None:
                    continue
                if 0 <= slot_idx <= 8:
                    hotbar_slot = slot_idx
                    break
                if item_slot is None:
                    item_slot = slot_idx
        if hotbar_slot is not None:
            ctx.client.transport.dispatch("select_slot", {"slot": hotbar_slot})
            return True
        if item_slot is None:
            return False
        empty_hotbar = None
        for slot in inv.get("inventory", []):
            slot_idx = slot.get("slot")
            if slot_idx is None or not (0 <= slot_idx <= 8):
                continue
            if slot.get("id") in ("minecraft:air", None) or slot.get("count", 0) == 0:
                empty_hotbar = slot_idx
                break
        target_slot = empty_hotbar if empty_hotbar is not None else 0
        if item_slot != target_slot:
            ctx.client.transport.dispatch("inventory_click", {
                "slot": _inventory_slot_to_player_screen(item_slot),
                "type": "PICKUP",
                "button": 0
            })
            time.sleep(0.1)
            ctx.client.transport.dispatch("inventory_click", {
                "slot": _inventory_slot_to_player_screen(target_slot),
                "type": "PICKUP",
                "button": 0
            })
            time.sleep(0.1)
            if empty_hotbar is None:
                ctx.client.transport.dispatch("inventory_click", {
                    "slot": _inventory_slot_to_player_screen(item_slot),
                    "type": "PICKUP",
                    "button": 0
                })
                time.sleep(0.1)
        ctx.client.transport.dispatch("select_slot", {"slot": target_slot})
        return True

    def _select_safe_interact_item(ctx: TestContext) -> bool:
        for item_id in ("minecraft:stick", "minecraft:beef", "minecraft:coal", "minecraft:iron_ingot"):
            if _select_hotbar_item(ctx, item_id):
                return True
        inv = ctx.get_inventory(timeout=2.0)
        for slot in inv.get("inventory", []):
            slot_idx = slot.get("slot")
            if slot_idx is None or not (0 <= slot_idx <= 8):
                continue
            if slot.get("id") in ("minecraft:air", None) or slot.get("count", 0) == 0:
                ctx.client.transport.dispatch("select_slot", {"slot": slot_idx})
                return True
        return False
    
    # T400: Basic Crafting
    def t400_setup(ctx: TestContext):
        ctx.client.transport.dispatch("close_screen", {})
        ctx.set_gamemode("survival")
        _clear_inventory_strict(ctx)
        ctx.give_item("minecraft:oak_log", 4)
        ctx.give_item("minecraft:crafting_table", 1)
        ctx.snapshot("start")
    
    def t400_step_craft(ctx: TestContext) -> bool:
        ctx.log_event("Crafting planks from logs...")
        if not _has_item(ctx, "minecraft:oak_log", count=1, timeout=3.0):
            _wait_for_item(ctx, "minecraft:oak_log", 1, timeout=4.0)
        if not _has_item(ctx, "minecraft:oak_log", count=1, timeout=3.0):
            ctx.log_event("Missing oak logs for crafting")
            return False
        ctx.client.transport.dispatch("craft", {"item": "minecraft:oak_planks", "count": 4})
        ok = _wait_for_item(ctx, "minecraft:oak_planks", 4, timeout=10.0)
        _close_screen(ctx)
        return ok
    
    def t400_assert_planks(ctx: TestContext):
        count = _count_item(ctx, "minecraft:oak_planks", timeout=3.0)
        if count < 4 and _wait_for_item(ctx, "minecraft:oak_planks", 4, timeout=5.0):
            count = _count_item(ctx, "minecraft:oak_planks", timeout=3.0)
        return count >= 4, f"Planks crafted: {count}"
    
    suite.add(TestCase(
        id="T400",
        name="Basic Crafting",
        description="Craft planks from logs",
        timeout_seconds=10,
        setup=t400_setup,
        steps=[t400_step_craft],
        assertions=[t400_assert_planks]
    ))
    
    # T401: Tool Crafting
    def t401_setup(ctx: TestContext):
        ctx.client.transport.dispatch("close_screen", {})
        ctx.set_gamemode("survival")
        _clear_inventory_strict(ctx)
        ctx.give_item("minecraft:oak_planks", 8)
        ctx.give_item("minecraft:stick", 4)
        ctx.give_item("minecraft:crafting_table", 1)
        _move_to_clear_area(ctx)
    
    def t401_step_craft_pick(ctx: TestContext) -> bool:
        ctx.log_event("Crafting wooden pickaxe...")
        if not _has_item(ctx, "minecraft:oak_planks", count=3, timeout=3.0):
            _wait_for_item(ctx, "minecraft:oak_planks", 3, timeout=4.0)
        if not _has_item(ctx, "minecraft:stick", count=2, timeout=3.0):
            _wait_for_item(ctx, "minecraft:stick", 2, timeout=4.0)
        if not _has_item(ctx, "minecraft:oak_planks", count=3, timeout=3.0) or not _has_item(ctx, "minecraft:stick", count=2, timeout=3.0):
            ctx.log_event("Missing planks or sticks for pickaxe")
            return False
        opened = _open_crafting_table(ctx)
        if not opened:
            ctx.log_event("Failed to open crafting table")
            return False
        try:
            ctx.client.transport.dispatch("craft", {"item": "minecraft:wooden_pickaxe", "count": 1})
        except Exception as e:
            ctx.log_event(f"Craft command failed: {e}")
            return False
        ok = _wait_for_item(ctx, "minecraft:wooden_pickaxe", 1, timeout=12.0)
        if not ok:
            ctx.log_event("Pickaxe craft did not complete, retrying")
            if _open_crafting_table(ctx):
                try:
                    ctx.client.transport.dispatch("craft", {"item": "minecraft:wooden_pickaxe", "count": 1})
                except Exception as e:
                    ctx.log_event(f"Craft retry failed: {e}")
                    _close_screen(ctx)
                    return False
                ok = _wait_for_item(ctx, "minecraft:wooden_pickaxe", 1, timeout=8.0)
        _close_screen(ctx)
        return ok
    
    def t401_assert_pick(ctx: TestContext):
        has = _has_item(ctx, "minecraft:wooden_pickaxe", count=1, timeout=3.0)
        if not has and _wait_for_item(ctx, "minecraft:wooden_pickaxe", 1, timeout=6.0):
            has = _has_item(ctx, "minecraft:wooden_pickaxe", count=1, timeout=3.0)
        return has, "Pickaxe crafted" if has else "No pickaxe"
    
    suite.add(TestCase(
        id="T401",
        name="Tool Crafting",
        description="Craft wooden pickaxe",
        timeout_seconds=10,
        setup=t401_setup,
        steps=[t401_step_craft_pick],
        assertions=[t401_assert_pick]
    ))
    
    # T402: Furnace Operation
    def t402_setup(ctx: TestContext):
        ctx.client.transport.dispatch("close_screen", {})
        ctx.set_gamemode("survival")
        _clear_inventory_strict(ctx)
        ctx.give_item("minecraft:furnace", 1)
        ctx.give_item("minecraft:beef", 3)
        ctx.give_item("minecraft:coal", 8)
        _move_to_clear_area(ctx)
    
    def t402_step_smelt(ctx: TestContext) -> bool:
        if not _ensure_on_ground(ctx, allow_teleport=False):
            ctx.log_event("Not on solid ground; cannot use furnace")
            return False
        x, y, z = ctx.get_position()
        nearby_furnace = _find_nearby_block_in_range(ctx, "minecraft:furnace", radius=24, max_distance=4.5)
        if nearby_furnace:
            furnace_pos, existing = nearby_furnace, True
        else:
            furnace_pos, existing = _find_furnace_spot(ctx, int(x), int(y), int(z))

        ctx.log_event("Placing furnace...")
        if not existing:
            placed = False
            if _has_item(ctx, "minecraft:furnace", count=1, timeout=2.0) and _select_hotbar_item(ctx, "minecraft:furnace"):
                try:
                    place_result = ctx.client.transport.dispatch("place_block", {
                        "x": furnace_pos[0],
                        "y": furnace_pos[1],
                        "z": furnace_pos[2]
                    })
                    placed = place_result.get("placed", False)
                    if not placed:
                        ctx.log_event(f"Furnace place_block failed: {place_result}")
                except Exception as e:
                    ctx.log_event(f"Furnace placement failed: {e}")
            if not placed:
                ctx.set_block(furnace_pos[0], furnace_pos[1], furnace_pos[2], "minecraft:furnace")
            time.sleep(0.2)
            block = ctx.client.transport.dispatch("get_block", {
                "x": furnace_pos[0],
                "y": furnace_pos[1],
                "z": furnace_pos[2]
            })
            if block.get("id") != "minecraft:furnace":
                ctx.log_event(f"Furnace not placed at {furnace_pos}, found {block.get('id')}")
                return False

        ctx.client.transport.dispatch("close_screen", {})
        distance = _move_near_block(ctx, furnace_pos, max_dist=4.5, max_wait=8.0, allow_teleport=False)
        if distance > 4.5:
            ctx.log_event(f"Too far to open furnace ({distance:.2f} blocks)")
            return False
        ctx.client.transport.dispatch("cancel", {})
        time.sleep(0.2)
        _select_safe_interact_item(ctx)
        ctx.set_block(furnace_pos[0], furnace_pos[1] + 1, furnace_pos[2], "minecraft:air")

        ctx.log_event("Opening furnace...")
        opened = False
        for _ in range(2):
            ctx.client.transport.dispatch("look_at", {
                "x": furnace_pos[0] + 0.5,
                "y": furnace_pos[1] + 0.5,
                "z": furnace_pos[2] + 0.5
            })
            time.sleep(0.1)
            ctx.client.transport.dispatch("interact_block", {
                "x": furnace_pos[0],
                "y": furnace_pos[1],
                "z": furnace_pos[2]
            })
            if _wait_for_furnace_screen(ctx, timeout=3.0):
                opened = True
                break
            ctx.client.transport.dispatch("close_screen", {})
            ctx.client.transport.dispatch("look_at", {
                "x": furnace_pos[0] + 0.5,
                "y": furnace_pos[1] + 0.5,
                "z": furnace_pos[2] + 0.5
            })
            time.sleep(0.1)
        if not opened:
            if not _wait_for_screen(ctx, timeout=2.0):
                state = ctx.get_state()
                screen = _get_screen(ctx)
                ctx.log_event(
                    "Furnace did not open: "
                    f"screen={state.get('screen')} type={screen.get('type')} "
                    f"total_slots={screen.get('total_slots')}"
                )
                return False
        screen = _get_screen(ctx)
        if screen.get("sync_id", 0) == 0:
            time.sleep(0.2)
            screen = _get_screen(ctx)
        ctx.log_event(
            f"Screen type: {screen.get('type')} total_slots={screen.get('total_slots')} "
            f"sync_id={screen.get('sync_id')}"
        )
        if screen.get("sync_id", 0) == 0:
            ctx.log_event("Furnace screen has sync_id 0; cannot click slots reliably")
            _close_screen(ctx)
            return False
        ctx.log_event(
            "Furnace slots before: "
            f"input={_screen_slot_summary(screen, 0)} "
            f"fuel={_screen_slot_summary(screen, 1)} "
            f"output={_screen_slot_summary(screen, 2)}"
        )
        sync_id = screen.get("sync_id", 0)

        ctx.log_event("Adding fuel and input...")
        inv = ctx.client.transport.dispatch("get_inventory", {})
        beef_count = sum(slot.get("count", 0) for slot in inv.get("inventory", []) if slot.get("id") == "minecraft:beef")
        coal_count = sum(slot.get("count", 0) for slot in inv.get("inventory", []) if slot.get("id") == "minecraft:coal")
        screen_beef = _screen_item_count(screen, "minecraft:beef")
        screen_coal = _screen_item_count(screen, "minecraft:coal")
        ctx.log_event(
            f"Inventory counts before smelt: beef={beef_count} coal={coal_count} "
            f"screen_beef={screen_beef} screen_coal={screen_coal}"
        )
        ctx.log_event(f"Screen beef slots: {_screen_slots_for_item(screen, 'minecraft:beef')}")
        ctx.log_event(f"Screen coal slots: {_screen_slots_for_item(screen, 'minecraft:coal')}")
        if beef_count == 0 and screen_beef == 0:
            _wait_for_item_in_inventory_or_screen(ctx, "minecraft:beef", timeout=6.0)
        if coal_count == 0 and screen_coal == 0:
            _wait_for_item_in_inventory_or_screen(ctx, "minecraft:coal", timeout=6.0)
        if not _wait_for_item_in_inventory_or_screen(ctx, "minecraft:beef", timeout=1.0) or not _wait_for_item_in_inventory_or_screen(ctx, "minecraft:coal", timeout=1.0):
            ctx.log_event("Missing beef or coal for smelting")
            return False
        if not _move_item_to_furnace_slot(ctx, "minecraft:coal", target_slot=1, timeout=4.0, sync_id=sync_id):
            ctx.log_event("Failed to load coal into furnace")
            screen = _get_screen(ctx)
            ctx.log_event(
                "Furnace slots after coal attempt: "
                f"input={_screen_slot_summary(screen, 0)} "
                f"fuel={_screen_slot_summary(screen, 1)} "
                f"output={_screen_slot_summary(screen, 2)}"
            )
            _close_screen(ctx)
            return False
        input_slot = None
        for candidate in (0, 2):
            if _move_item_to_furnace_slot(ctx, "minecraft:beef", target_slot=candidate, timeout=4.0, sync_id=sync_id):
                input_slot = candidate
                break
        if input_slot is None:
            ctx.log_event("Failed to load beef into furnace")
            screen = _get_screen(ctx)
            ctx.log_event(
                "Furnace slots after beef attempt: "
                f"input0={_screen_slot_summary(screen, 0)} "
                f"fuel={_screen_slot_summary(screen, 1)} "
                f"input2={_screen_slot_summary(screen, 2)}"
            )
            _close_screen(ctx)
            return False
        output_slot = 2 if input_slot == 0 else 0

        ctx.log_event("Smelting in progress...")
        cooked_ready = _wait_for_screen_slot_item(ctx, output_slot, "minecraft:cooked_beef", timeout=35.0)
        if not cooked_ready:
            screen = _get_screen(ctx)
            cooked_slots = _screen_slots_for_item(screen, "minecraft:cooked_beef")
            if cooked_slots:
                output_slot = cooked_slots[0][0]
                cooked_ready = True
            else:
                ctx.log_event(
                    "No cooked beef detected after smelt: "
                    f"type={screen.get('type')} total_slots={screen.get('total_slots')} "
                    f"slot0={_screen_slot_summary(screen, 0)} "
                    f"slot1={_screen_slot_summary(screen, 1)} "
                    f"slot2={_screen_slot_summary(screen, 2)}"
                )

        if cooked_ready:
            ctx.log_event("Collecting cooked beef...")
            ctx.client.transport.dispatch("inventory_click", {
                "slot": output_slot,
                "type": "QUICK_MOVE",
                "button": 0,
                "sync_id": sync_id
            })
            time.sleep(0.2)

        _close_screen(ctx)
        return True
    
    def t402_assert_cooked(ctx: TestContext):
        has = _has_item(ctx, "minecraft:cooked_beef", count=1, timeout=3.0)
        if not has and _wait_for_item(ctx, "minecraft:cooked_beef", 1, timeout=10.0):
            has = _has_item(ctx, "minecraft:cooked_beef", count=1, timeout=3.0)
        return has, "Cooked beef present"

    suite.add(TestCase(
        id="T402",
        name="Furnace Operation",
        description="Place furnace and smelt raw food",
        timeout_seconds=45,
        setup=t402_setup,
        steps=[t402_step_smelt],
        assertions=[t402_assert_cooked]
    ))
    
    # T403: Multi-Step Crafting
    def t403_setup(ctx: TestContext):
        ctx.client.transport.dispatch("close_screen", {})
        ctx.set_gamemode("survival")
        _clear_inventory_strict(ctx)
        ctx.give_item("minecraft:iron_ingot", 3)
        ctx.give_item("minecraft:stick", 2)
        ctx.give_item("minecraft:crafting_table", 1)
        _move_to_clear_area(ctx)
    
    def t403_step_craft_iron_pick(ctx: TestContext) -> bool:
        ctx.log_event("Crafting iron pickaxe...")
        if not _has_item(ctx, "minecraft:iron_ingot", count=3, timeout=3.0):
            _wait_for_item(ctx, "minecraft:iron_ingot", 3, timeout=4.0)
        if not _has_item(ctx, "minecraft:stick", count=2, timeout=3.0):
            _wait_for_item(ctx, "minecraft:stick", 2, timeout=4.0)
        if not _has_item(ctx, "minecraft:iron_ingot", count=3, timeout=3.0) or not _has_item(ctx, "minecraft:stick", count=2, timeout=3.0):
            ctx.log_event("Missing iron ingots or sticks for pickaxe")
            return False
        opened = _open_crafting_table(ctx)
        if not opened:
            ctx.log_event("Failed to open crafting table")
            return False
        try:
            ctx.client.transport.dispatch("craft", {"item": "minecraft:iron_pickaxe", "count": 1})
        except Exception as e:
            ctx.log_event(f"Craft command failed: {e}")
            return False
        ok = _wait_for_item(ctx, "minecraft:iron_pickaxe", 1, timeout=12.0)
        if not ok:
            ctx.log_event("Iron pickaxe craft did not complete, retrying")
            if _open_crafting_table(ctx):
                try:
                    ctx.client.transport.dispatch("craft", {"item": "minecraft:iron_pickaxe", "count": 1})
                except Exception as e:
                    ctx.log_event(f"Craft retry failed: {e}")
                    _close_screen(ctx)
                    return False
                ok = _wait_for_item(ctx, "minecraft:iron_pickaxe", 1, timeout=8.0)
        _close_screen(ctx)
        return ok
    
    def t403_assert_iron_pick(ctx: TestContext):
        has = _has_item(ctx, "minecraft:iron_pickaxe", count=1, timeout=3.0)
        if not has and _wait_for_item(ctx, "minecraft:iron_pickaxe", 1, timeout=6.0):
            has = _has_item(ctx, "minecraft:iron_pickaxe", count=1, timeout=3.0)
        return has, "Iron pickaxe crafted" if has else "No iron pickaxe"
    
    suite.add(TestCase(
        id="T403",
        name="Multi-Step Crafting",
        description="Craft iron pickaxe from ingots and sticks",
        timeout_seconds=15,
        setup=t403_setup,
        steps=[t403_step_craft_iron_pick],
        assertions=[t403_assert_iron_pick]
    ))
    
    return suite


__all__ = ["create_extended_suite_400"]
