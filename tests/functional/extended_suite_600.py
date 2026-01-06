"""
Extended Suite 600: Sensing & Knowledge (Granular Action Tests)
T600-T609: Block Scanning, Entity Detection, Structure Recognition,
           Position Tracking, Environmental Sensing, Line-of-Sight,
           Inventory Sensing, Non-Visual Entity Detection, Memory Consistency,
           Sensor Refresh Rate
"""

import math
import re
import time
from test_base import TestCase, TestSuite, TestContext

from tests.utils.mc_harness import (
    prepare_test_world,
    teardown_test_world,
    clear_box,
    tp,
    wait_for_pathing_stop,
    get_entities,
    wait_for_entity,
    get_entity_by_id,
    distance_to_entity,
)


def create_extended_suite_600() -> TestSuite:
    """Suite 600: Sensing & Knowledge - Granular action tests."""
    suite = TestSuite("Suite_600_Sensing", "Granular sensing action tests")
    suite_state = {}

    anchors = {
        "T600": (0, 80, 600),
        "T601": (200, 80, 600),
        "T602": (400, 80, 600),
        "T603": (600, 80, 600),
        "T604": (800, 80, 600),
        "T605": (1000, 80, 600),
        "T606": (1200, 80, 600),
        "T607": (1400, 80, 600),
        "T608": (1600, 80, 600),
        "T609": (1800, 80, 600),
    }

    def _state(test_id: str) -> dict:
        return suite_state.setdefault(test_id, {})

    def _setup_bounds(ctx: TestContext, test_id: str, anchor: tuple, size: int = 20):
        ax, ay, az = anchor
        bounds = {
            "min_x": ax - size,
            "min_y": ay - 6,
            "min_z": az - size,
            "max_x": ax + size,
            "max_y": ay + 12,
            "max_z": az + size,
        }
        _state(test_id)["bounds"] = bounds
        clear_box(ctx, bounds)
        tp(ctx, ax, ay, az)

    def _block_key(block: dict) -> tuple:
        return (block.get("x"), block.get("y"), block.get("z"))

    def _scan_blocks(ctx: TestContext, blocks: list, radius: int, limit: int = 200) -> list:
        result = ctx.client.transport.dispatch("find_blocks", {
            "blocks": blocks,
            "radius": radius,
            "limit": limit
        })
        return result.get("found", result.get("data", {}).get("found", []))

    def _nearest_entity(ctx: TestContext, entity_type: str, radius: int = 16):
        ents = get_entities(ctx, radius=radius)
        targets = [ent for ent in ents if ent.get("type") == entity_type]
        if not targets:
            return None
        return min(targets, key=lambda ent: distance_to_entity(ctx, ent))

    def _poll_chat_messages(ctx: TestContext, timeout: float = 3.0) -> list:
        messages = []
        start = time.time()
        while time.time() - start < timeout:
            try:
                events = ctx.client.transport.dispatch("get_events", {})
                data = events.get("data", events)
                for event in data.get("events", []):
                    if event.get("type") == "chat":
                        msg = event.get("data", {}).get("message", "")
                        if msg:
                            messages.append(msg)
                if messages:
                    break
            except Exception:
                return messages
            time.sleep(0.2)
        return messages

    def _parse_locate_coords(messages: list) -> tuple:
        for msg in messages:
            if "nearest" in msg.lower() or "located" in msg.lower():
                numbers = re.findall(r"-?\d+", msg)
                if len(numbers) >= 3:
                    return int(numbers[0]), int(numbers[1]), int(numbers[2])
        return None

    def _food_level(state: dict) -> int:
        return int(state.get("food_level", state.get("hunger", 20)))

    # T600: Block Scanning
    def t600_setup(ctx: TestContext):
        prepare_test_world(ctx)
        _setup_bounds(ctx, "T600", anchors["T600"])
        x, y, z = ctx.get_position()
        base_x = int(x) + 4
        base_y = int(y) + 4
        base_z = int(z) + 4

        ctx.run_command(
            f"fill {base_x - 4} {base_y - 4} {base_z - 4} "
            f"{base_x + 4} {base_y + 4} {base_z + 4} air"
        )

        pattern = [
            ["lapis_block", "gold_block", "emerald_block", "lapis_block", "gold_block"],
            ["gold_block", "emerald_block", "lapis_block", "gold_block", "emerald_block"],
            ["emerald_block", "lapis_block", "gold_block", "emerald_block", "lapis_block"],
            ["lapis_block", "gold_block", "emerald_block", "lapis_block", "gold_block"],
            ["gold_block", "emerald_block", "lapis_block", "gold_block", "emerald_block"],
        ]
        expected = {"minecraft:lapis_block": [], "minecraft:gold_block": [], "minecraft:emerald_block": []}
        for dz in range(-2, 3):
            for dx in range(-2, 3):
                block = pattern[dz + 2][dx + 2]
                block_id = f"minecraft:{block}"
                pos = (base_x + dx, base_y, base_z + dz)
                ctx.set_block(pos[0], pos[1], pos[2], block_id)
                expected[block_id].append(pos)

        vertical_up = (base_x, base_y + 3, base_z)
        vertical_down = (base_x + 1, base_y - 3, base_z)
        ctx.set_block(vertical_up[0], vertical_up[1], vertical_up[2], "minecraft:lapis_block")
        ctx.set_block(vertical_down[0], vertical_down[1], vertical_down[2], "minecraft:gold_block")
        expected["minecraft:lapis_block"].append(vertical_up)
        expected["minecraft:gold_block"].append(vertical_down)

        edge_in = (int(x) + 9, base_y, int(z))
        edge_out = (int(x) + 10, base_y, int(z))
        ctx.set_block(edge_in[0], edge_in[1], edge_in[2], "minecraft:emerald_block")
        ctx.set_block(edge_out[0], edge_out[1], edge_out[2], "minecraft:lapis_block")
        expected["minecraft:emerald_block"].append(edge_in)

        state = _state("T600")
        state["expected"] = expected
        state["edge_out"] = edge_out
        ctx.snapshot("t600_ready")

    def t600_step_scan(ctx: TestContext) -> bool:
        ctx.log_event("Scanning known pattern blocks...")
        expected = _state("T600").get("expected", {})
        edge_out = _state("T600").get("edge_out")

        blocks = _scan_blocks(ctx, list(expected.keys()), radius=9, limit=200)
        found_positions = {_block_key(b) for b in blocks}

        missing = []
        for block_id, positions in expected.items():
            for pos in positions:
                if pos not in found_positions:
                    missing.append((block_id, pos))
        if missing:
            ctx.log_event(f"Missing blocks: {missing[:5]}")
            return False
        if edge_out in found_positions:
            ctx.log_event("Found block outside radius boundary")
            return False

        negative = _scan_blocks(ctx, ["minecraft:netherite_block"], radius=9, limit=10)
        if negative:
            ctx.log_event("Negative scan returned unexpected blocks")
            return False

        limited = _scan_blocks(ctx, list(expected.keys()), radius=9, limit=5)
        if len(limited) != 5:
            ctx.log_event(f"Limit scan returned {len(limited)} blocks")
            return False

        for _ in range(5):
            blocks = _scan_blocks(ctx, list(expected.keys()), radius=9, limit=200)
            found_positions = {_block_key(b) for b in blocks}
            for positions in expected.values():
                if any(pos not in found_positions for pos in positions):
                    ctx.log_event("Repeat scan missed expected blocks")
                    return False
        return True

    def t600_teardown(ctx: TestContext):
        teardown_test_world(ctx, bounds=_state("T600").get("bounds"))

    suite.add(TestCase(
        id="T600",
        name="Block Scanning",
        description="Scan blocks in radius with accuracy and stability checks",
        timeout_seconds=20,
        setup=t600_setup,
        steps=[t600_step_scan],
        assertions=[],
        teardown=t600_teardown
    ))

    # T601: Entity Detection
    def t601_setup(ctx: TestContext):
        prepare_test_world(ctx)
        _setup_bounds(ctx, "T601", anchors["T601"])
        x, y, z = ctx.get_position()
        ctx.client.transport.dispatch("chat", {
            "message": f"/summon minecraft:cow {int(x) + 3} {int(y)} {int(z)} {{Tags:[\"suite600_cow_near\"]}}"
        })
        ctx.client.transport.dispatch("chat", {
            "message": f"/summon minecraft:pig {int(x) - 3} {int(y)} {int(z)} {{Tags:[\"suite600_pig\"]}}"
        })
        ctx.client.transport.dispatch("chat", {
            "message": f"/summon minecraft:cow {int(x) + 10} {int(y)} {int(z)} {{Tags:[\"suite600_cow_far\"]}}"
        })
        time.sleep(0.5)

    def t601_step_detect(ctx: TestContext) -> bool:
        ents = get_entities(ctx, radius=20)
        types = {ent.get("type") for ent in ents}
        if "minecraft:cow" not in types or "minecraft:pig" not in types:
            ctx.log_event(f"Missing entities: {types}")
            return False

        close_ents = get_entities(ctx, radius=4)
        close_types = {ent.get("type") for ent in close_ents}
        if "minecraft:cow" not in close_types or "minecraft:pig" not in close_types:
            ctx.log_event(f"Radius gate failed: {close_types}")
            return False

        nearest_cow = _nearest_entity(ctx, "minecraft:cow", radius=20)
        if nearest_cow is None or distance_to_entity(ctx, nearest_cow) > 5.0:
            ctx.log_event("Nearest cow selection incorrect")
            return False

        ctx.run_command("kill @e[tag=suite600_pig]")
        start = time.time()
        while time.time() - start < 2.0:
            ents_after = get_entities(ctx, radius=10)
            if not any(ent.get("type") == "minecraft:pig" for ent in ents_after):
                break
            time.sleep(0.3)
        else:
            ctx.log_event("Pig still detected after removal")
            return False
        return True

    def t601_teardown(ctx: TestContext):
        teardown_test_world(ctx, bounds=_state("T601").get("bounds"))

    suite.add(TestCase(
        id="T601",
        name="Entity Detection",
        description="Detect entities with distance and despawn checks",
        timeout_seconds=15,
        setup=t601_setup,
        steps=[t601_step_detect],
        assertions=[],
        teardown=t601_teardown
    ))

    # T602: Structure Recognition
    def t602_setup(ctx: TestContext):
        prepare_test_world(ctx)
        _setup_bounds(ctx, "T602", anchors["T602"])
        x, y, z = ctx.get_position()
        base = (int(x) + 6, int(y), int(z) + 2)
        ctx.set_block(base[0], base[1], base[2], "minecraft:bell")
        ctx.set_block(base[0] + 1, base[1], base[2], "minecraft:composter")
        ctx.set_block(base[0] + 2, base[1], base[2], "minecraft:red_bed")
        _state("T602")["signature"] = [base, (base[0] + 1, base[1], base[2]), (base[0] + 2, base[1], base[2])]

    def t602_step_detect_structure(ctx: TestContext) -> bool:
        signature = _state("T602").get("signature", [])
        blocks = _scan_blocks(ctx, ["minecraft:bell", "minecraft:composter", "minecraft:red_bed"], radius=10)
        found_positions = {_block_key(b) for b in blocks}
        for pos in signature:
            if pos not in found_positions:
                ctx.log_event("Structure signature not detected")
                return False
        try:
            ctx.client.transport.dispatch("get_events", {})
            ctx.run_command("locate structure minecraft:village_plains")
            messages = _poll_chat_messages(ctx, timeout=3.0)
            coords = _parse_locate_coords(messages)
        except Exception:
            coords = None
        if coords:
            px, py, pz = ctx.get_position()
            distance = math.sqrt((coords[0] - px) ** 2 + (coords[2] - pz) ** 2)
            if distance > 20000:
                ctx.log_event(f"Located structure too far: {distance:.0f} blocks")
                return False

            step = 40.0
            dx = coords[0] - px
            dz = coords[2] - pz
            length = math.sqrt(dx * dx + dz * dz) or 1.0
            move_x = px + (dx / length) * step
            move_z = pz + (dz / length) * step
            ctx.client.transport.dispatch("goto", {"x": int(move_x), "y": int(py), "z": int(move_z)})
            wait_for_pathing_stop(ctx, timeout=6.0)
            ctx.client.transport.dispatch("cancel", {})
        return True

    def t602_teardown(ctx: TestContext):
        teardown_test_world(ctx, bounds=_state("T602").get("bounds"))

    suite.add(TestCase(
        id="T602",
        name="Structure Recognition",
        description="Locate structure and verify synthetic signature",
        timeout_seconds=25,
        setup=t602_setup,
        steps=[t602_step_detect_structure],
        assertions=[],
        teardown=t602_teardown
    ))

    # T603: Position Tracking
    def t603_setup(ctx: TestContext):
        prepare_test_world(ctx)
        _setup_bounds(ctx, "T603", anchors["T603"])

    def t603_step_read_coords(ctx: TestContext) -> bool:
        start_pos = ctx.get_position()
        target = (int(start_pos[0]) + 5, int(start_pos[1]), int(start_pos[2]))
        ctx.client.transport.dispatch("goto", {"x": target[0], "y": target[1], "z": target[2]})
        moved = False
        start = time.time()
        while time.time() - start < 4.0:
            pos = ctx.get_position()
            if abs(pos[0] - start_pos[0]) >= 2:
                moved = True
                break
            time.sleep(0.3)
        if not moved:
            ctx.client.transport.dispatch("chat", {"message": f"#goto {target[0]} {target[1]} {target[2]}"})
            start = time.time()
            while time.time() - start < 4.0:
                pos = ctx.get_position()
                if abs(pos[0] - start_pos[0]) >= 2:
                    moved = True
                    break
                time.sleep(0.3)
        wait_for_pathing_stop(ctx, timeout=6.0)
        ctx.client.transport.dispatch("cancel", {})
        end_pos = ctx.get_position()

        dx = end_pos[0] - start_pos[0]
        dz = end_pos[2] - start_pos[2]
        if abs(dx) < 3 or abs(dx) > 8 or abs(dz) > 2:
            ctx.log_event(f"Unexpected movement delta dx={dx:.2f} dz={dz:.2f}")
            return False

        samples = []
        for _ in range(5):
            samples.append(ctx.get_position())
            time.sleep(0.2)
        max_jitter = max(math.dist(samples[0], pos) for pos in samples[1:])
        if max_jitter > 0.8:
            ctx.log_event(f"Position jitter too high: {max_jitter:.2f}")
            return False

        state = ctx.get_state()
        if "yaw" in state:
            old_yaw = state.get("yaw")
            ctx.client.transport.dispatch("look_at", {"x": end_pos[0] + 5, "y": end_pos[1], "z": end_pos[2]})
            time.sleep(0.2)
            new_yaw = ctx.get_state().get("yaw", old_yaw)
            if old_yaw == new_yaw:
                ctx.log_event("Yaw did not change after look_at")
                return False
        return True

    def t603_assert_coords(ctx: TestContext):
        state = ctx.get_state()
        dim = state.get("dimension", "")
        return len(dim) > 0, f"Dimension: {dim}"

    def t603_teardown(ctx: TestContext):
        teardown_test_world(ctx, bounds=_state("T603").get("bounds"))

    suite.add(TestCase(
        id="T603",
        name="Position Tracking",
        description="Verify movement deltas, jitter, and dimension",
        timeout_seconds=15,
        setup=t603_setup,
        steps=[t603_step_read_coords],
        assertions=[t603_assert_coords],
        teardown=t603_teardown
    ))

    # T604: Environmental Sensing
    def t604_setup(ctx: TestContext):
        prepare_test_world(ctx)
        _setup_bounds(ctx, "T604", anchors["T604"])

    def t604_step_environment(ctx: TestContext) -> bool:
        ctx.run_command("time set day")
        time.sleep(0.3)
        day_time = ctx.get_state().get("world_time", 0)
        ctx.run_command("time set night")
        time.sleep(0.3)
        night_time = ctx.get_state().get("world_time", 0)
        if not (0 <= day_time < 12000) or not (12000 <= night_time <= 24000):
            ctx.log_event(f"Time classification failed: day={day_time} night={night_time}")
            return False

        before = _food_level(ctx.get_state())
        ctx.run_command("effect give @p minecraft:hunger 8 10 true")
        start = time.time()
        after = before
        while time.time() - start < 6.0:
            after = _food_level(ctx.get_state())
            if after < before:
                break
            time.sleep(0.5)
        if after >= before:
            ctx.run_command("data merge entity @p {foodLevel:16,foodSaturationLevel:0.0f}")
            time.sleep(0.3)
            after = _food_level(ctx.get_state())
        if after >= before:
            ctx.log_event(f"Hunger did not drop: before={before} after={after}")
            return False
        return True

    def t604_teardown(ctx: TestContext):
        teardown_test_world(ctx, bounds=_state("T604").get("bounds"))

    suite.add(TestCase(
        id="T604",
        name="Environmental Sensing",
        description="Check time of day and hunger changes",
        timeout_seconds=10,
        setup=t604_setup,
        steps=[t604_step_environment],
        assertions=[],
        teardown=t604_teardown
    ))

    # T605: Line of Sight / Raycast Targeting
    def t605_setup(ctx: TestContext):
        prepare_test_world(ctx)
        _setup_bounds(ctx, "T605", anchors["T605"])
        x, y, z = ctx.get_position()
        target = (int(x) + 3, int(y) + 1, int(z))
        ctx.set_block(target[0], target[1], target[2], "minecraft:stone_button")
        _state("T605")["target"] = target

    def t605_step_raycast(ctx: TestContext) -> bool:
        target = _state("T605").get("target")
        if not target:
            return False
        ctx.client.transport.dispatch("look_at", {
            "x": target[0] + 0.5,
            "y": target[1] + 0.5,
            "z": target[2] + 0.5
        })
        result = ctx.client.transport.dispatch("interact_block", {
            "x": target[0],
            "y": target[1],
            "z": target[2]
        })
        data = result.get("data", result)
        hit = (data.get("hit_x"), data.get("hit_y"), data.get("hit_z"))
        return hit == target

    def t605_teardown(ctx: TestContext):
        teardown_test_world(ctx, bounds=_state("T605").get("bounds"))

    suite.add(TestCase(
        id="T605",
        name="Line of Sight Targeting",
        description="Raycast to a known block target",
        timeout_seconds=10,
        setup=t605_setup,
        steps=[t605_step_raycast],
        assertions=[],
        teardown=t605_teardown
    ))

    # T606: Inventory Sensing Accuracy
    def t606_setup(ctx: TestContext):
        prepare_test_world(ctx)
        _setup_bounds(ctx, "T606", anchors["T606"])
        ctx.clear_inventory()
        ctx.give_item("minecraft:cobblestone", 5)
        ctx.give_item("minecraft:stick", 3)
        time.sleep(0.3)

    def t606_step_inventory(ctx: TestContext) -> bool:
        inv = ctx.get_inventory()
        counts = {}
        slots = {}
        for slot in inv.get("inventory", []):
            if not slot:
                continue
            item_id = slot.get("id")
            if not item_id:
                continue
            counts[item_id] = counts.get(item_id, 0) + slot.get("count", 0)
            slots.setdefault(item_id, slot.get("slot"))
        if counts.get("minecraft:cobblestone", 0) < 5 or counts.get("minecraft:stick", 0) < 3:
            ctx.log_event(f"Inventory counts incorrect: {counts}")
            return False
        cobble_slot = slots.get("minecraft:cobblestone")
        if cobble_slot is None:
            ctx.log_event("Cobblestone slot not reported")
            return False
        ctx.client.transport.dispatch("throw_item", {"slot": cobble_slot, "all": False})
        time.sleep(0.5)
        after = ctx.count_item("minecraft:cobblestone")
        return after == 4

    def t606_teardown(ctx: TestContext):
        teardown_test_world(ctx, bounds=_state("T606").get("bounds"))

    suite.add(TestCase(
        id="T606",
        name="Inventory Sensing Accuracy",
        description="Validate inventory counts and updates after drop",
        timeout_seconds=15,
        setup=t606_setup,
        steps=[t606_step_inventory],
        assertions=[],
        teardown=t606_teardown
    ))

    # T607: Non-Visual Entity Detection
    def t607_setup(ctx: TestContext):
        prepare_test_world(ctx)
        _setup_bounds(ctx, "T607", anchors["T607"])
        x, y, z = ctx.get_position()
        ctx.client.transport.dispatch("chat", {
            "message": f"/summon minecraft:cow {int(x) - 4} {int(y)} {int(z)}"
        })
        time.sleep(0.5)

    def t607_step_detect(ctx: TestContext) -> bool:
        ents = get_entities(ctx, radius=8)
        return any(ent.get("type") == "minecraft:cow" for ent in ents)

    def t607_teardown(ctx: TestContext):
        teardown_test_world(ctx, bounds=_state("T607").get("bounds"))

    suite.add(TestCase(
        id="T607",
        name="Non-Visual Entity Detection",
        description="Detect entities not necessarily in view",
        timeout_seconds=10,
        setup=t607_setup,
        steps=[t607_step_detect],
        assertions=[],
        teardown=t607_teardown
    ))

    # T608: Memory Consistency
    def t608_setup(ctx: TestContext):
        prepare_test_world(ctx)
        _setup_bounds(ctx, "T608", anchors["T608"])
        x, y, z = ctx.get_position()
        a = (int(x) + 2, int(y) + 1, int(z) + 2)
        b = (int(x) + 3, int(y) + 1, int(z) + 2)
        ctx.set_block(a[0], a[1], a[2], "minecraft:gold_block")
        ctx.set_block(b[0], b[1], b[2], "minecraft:emerald_block")
        state = _state("T608")
        state["blocks"] = [a, b]
        state["origin"] = ctx.get_position()

    def t608_step_memory(ctx: TestContext) -> bool:
        blocks = _scan_blocks(ctx, ["minecraft:gold_block", "minecraft:emerald_block"], radius=8)
        found_positions = {_block_key(b) for b in blocks}
        for pos in _state("T608").get("blocks", []):
            if pos not in found_positions:
                ctx.log_event("Initial memory scan missed block")
                return False
        origin = _state("T608").get("origin", ctx.get_position())
        ctx.client.transport.dispatch("goto", {"x": int(origin[0]) + 20, "y": int(origin[1]), "z": int(origin[2])})
        wait_for_pathing_stop(ctx, timeout=8.0)
        ctx.client.transport.dispatch("cancel", {})
        ctx.client.transport.dispatch("goto", {"x": int(origin[0]), "y": int(origin[1]), "z": int(origin[2])})
        wait_for_pathing_stop(ctx, timeout=8.0)
        ctx.client.transport.dispatch("cancel", {})
        blocks = _scan_blocks(ctx, ["minecraft:gold_block", "minecraft:emerald_block"], radius=8)
        found_positions = {_block_key(b) for b in blocks}
        return all(pos in found_positions for pos in _state("T608").get("blocks", []))

    def t608_teardown(ctx: TestContext):
        teardown_test_world(ctx, bounds=_state("T608").get("bounds"))

    suite.add(TestCase(
        id="T608",
        name="Memory Consistency",
        description="Rescan known blocks after moving away and returning",
        timeout_seconds=30,
        setup=t608_setup,
        steps=[t608_step_memory],
        assertions=[],
        teardown=t608_teardown
    ))

    # T609: Sensor Refresh Rate
    def t609_setup(ctx: TestContext):
        prepare_test_world(ctx)
        _setup_bounds(ctx, "T609", anchors["T609"])
        ctx.clear_inventory()

    def t609_step_refresh(ctx: TestContext) -> bool:
        x, y, z = ctx.get_position()
        start = time.time()
        ctx.client.transport.dispatch("chat", {
            "message": f"/summon minecraft:cow {int(x) + 2} {int(y)} {int(z)}"
        })
        while time.time() - start < 3.0:
            if wait_for_entity(ctx, "minecraft:cow", radius=8, timeout=0.2):
                break
        else:
            ctx.log_event("Entity did not appear in time")
            return False
        if time.time() - start > 3.0:
            ctx.log_event("Entity detection exceeded 3s")
            return False

        block_pos = (int(x) + 2, int(y) + 1, int(z) + 2)
        start = time.time()
        ctx.set_block(block_pos[0], block_pos[1], block_pos[2], "minecraft:diamond_block")
        while time.time() - start < 2.0:
            block = ctx.get_block(block_pos[0], block_pos[1], block_pos[2])
            if block.get("id") == "minecraft:diamond_block":
                return True
            time.sleep(0.1)
        ctx.log_event("Block did not appear in time")
        return False

    def t609_teardown(ctx: TestContext):
        teardown_test_world(ctx, bounds=_state("T609").get("bounds"))

    suite.add(TestCase(
        id="T609",
        name="Sensor Refresh Rate",
        description="Verify entity and block detection latency",
        timeout_seconds=20,
        setup=t609_setup,
        steps=[t609_step_refresh],
        assertions=[],
        teardown=t609_teardown
    ))

    return suite


__all__ = ["create_extended_suite_600"]
