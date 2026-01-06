"""
Extended Suite 500: Combat & Survival (Granular Action Tests)
T500-T505: Melee, Critical Hit, Shield, Ranged, Health, Progressive Waves
"""

import time
from test_base import TestCase, TestSuite, TestContext

from tests.utils.mc_harness import (
    prepare_test_world,
    teardown_test_world,
    clear_box,
    build_floor,
    tp,
    wait_for_item_decrease,
    wait_for_pathing_stop,
    select_hotbar_item,
    get_entities,
    wait_for_entity,
    get_entity_by_id,
    summon_near,
    distance_to_entity,
    build_arena,
    within_bounds,
    spawn_wave,
    nearest_entity,
)


def create_extended_suite_500() -> TestSuite:
    """Suite 500: Combat & Survival - Granular action tests."""
    suite = TestSuite("Suite_500_Combat", "Granular combat action tests")
    suite_state = {}
    arena_state = {}

    anchors = {
        "T500": (0, 80, 500),
        "T501": (200, 80, 500),
        "T502": (400, 80, 500),
        "T503": (600, 80, 500),
        "T504": (800, 80, 500),
        "T505": (1000, 80, 500),
    }

    def _state(test_id: str) -> dict:
        return suite_state.setdefault(test_id, {})

    def _setup_bounds(ctx: TestContext, test_id: str, anchor: tuple, size: int = 20):
        ax, ay, az = anchor
        bounds = {
            "min_x": ax - size,
            "min_y": ay - 5,
            "min_z": az - size,
            "max_x": ax + size,
            "max_y": ay + 10,
            "max_z": az + size,
        }
        _state(test_id)["bounds"] = bounds
        clear_box(ctx, bounds)
        tp(ctx, ax, ay, az)
        build_floor(ctx, ax - 6, ay - 1, az - 6, ax + 6, az + 6)

    def _kite_away(ctx: TestContext, bounds: dict, targets: list, step: int = 4):
        if not targets:
            return
        cx, _, cz = bounds["center"]
        avg_x = sum(ent.get("position", {}).get("x", cx) for ent in targets) / len(targets)
        avg_z = sum(ent.get("position", {}).get("z", cz) for ent in targets) / len(targets)
        px, py, pz = ctx.get_position()
        dx = px - avg_x
        dz = pz - avg_z
        length = (dx * dx + dz * dz) ** 0.5 or 1.0
        move_x = px + (dx / length) * step
        move_z = pz + (dz / length) * step
        move_x = max(bounds["min_x"] + 2, min(bounds["max_x"] - 2, move_x))
        move_z = max(bounds["min_z"] + 2, min(bounds["max_z"] - 2, move_z))
        ctx.client.transport.dispatch("goto", {"x": int(move_x), "y": int(py), "z": int(move_z)})
        time.sleep(0.6)
        ctx.client.transport.dispatch("cancel", {})

    def _move_towards(ctx: TestContext, bounds: dict, target: dict, step: int = 4):
        pos = target.get("position", {})
        tx = pos.get("x", bounds["center"][0])
        tz = pos.get("z", bounds["center"][2])
        px, py, pz = ctx.get_position()
        dx = tx - px
        dz = tz - pz
        length = (dx * dx + dz * dz) ** 0.5
        if length <= 0.5:
            return
        move_x = px + (dx / length) * step
        move_z = pz + (dz / length) * step
        move_x = max(bounds["min_x"] + 2, min(bounds["max_x"] - 2, move_x))
        move_z = max(bounds["min_z"] + 2, min(bounds["max_z"] - 2, move_z))
        ctx.client.transport.dispatch("goto", {"x": int(move_x), "y": int(py), "z": int(move_z)})
        time.sleep(0.6)
        ctx.client.transport.dispatch("cancel", {})

    def _farthest_corner(bounds: dict, from_pos: tuple) -> tuple:
        corners = [
            (bounds["min_x"] + 2, bounds["center"][1], bounds["min_z"] + 2),
            (bounds["min_x"] + 2, bounds["center"][1], bounds["max_z"] - 2),
            (bounds["max_x"] - 2, bounds["center"][1], bounds["min_z"] + 2),
            (bounds["max_x"] - 2, bounds["center"][1], bounds["max_z"] - 2),
        ]
        fx, _, fz = from_pos
        return max(corners, key=lambda pos: (pos[0] - fx) ** 2 + (pos[2] - fz) ** 2)

    def _retreat_and_heal(ctx: TestContext, bounds: dict, enemy_type: str, safe_threshold: float) -> bool:
        for _ in range(6):
            targets = [ent for ent in get_entities(ctx, radius=20) if ent.get("type") == enemy_type]
            _kite_away(ctx, bounds, targets, step=8)
            time.sleep(0.6)
            nearest = nearest_entity(ctx, enemy_type, radius=20)
            if nearest is None:
                break
            if distance_to_entity(ctx, nearest) > 6.5:
                break
        nearest = nearest_entity(ctx, enemy_type, radius=20)
        if nearest is not None and distance_to_entity(ctx, nearest) <= 6.0:
            pos = nearest.get("position", bounds["center"])
            corner = _farthest_corner(bounds, (pos.get("x", bounds["center"][0]), 0, pos.get("z", bounds["center"][2])))
            ctx.client.transport.dispatch("goto", {"x": int(corner[0]), "y": int(corner[1]), "z": int(corner[2])})
            time.sleep(1.0)
            ctx.client.transport.dispatch("cancel", {})
            time.sleep(0.4)
            nearest = nearest_entity(ctx, enemy_type, radius=20)
            if nearest is not None and distance_to_entity(ctx, nearest) <= 5.0:
                ctx.log_event("Healing under pressure; continuing retreat while eating")
        if ctx.count_item("minecraft:apple") <= 0:
            ctx.log_event("No apples available to heal")
            return False
        start_time = time.time()
        last_health = ctx.get_state().get("health", 20)
        last_gain_time = time.time()
        while ctx.get_state().get("health", 20) <= safe_threshold:
            if time.time() - start_time > 30:
                ctx.log_event("Healing timeout")
                return False
            nearest = nearest_entity(ctx, enemy_type, radius=20)
            if nearest is not None and distance_to_entity(ctx, nearest) <= 6.0:
                _kite_away(ctx, bounds, [nearest], step=7)
                time.sleep(0.4)
            state = ctx.get_state()
            health = state.get("health", 20)
            hunger = state.get("hunger", 20)
            if health > last_health:
                last_health = health
                last_gain_time = time.time()
            elif health < last_health:
                last_health = health
            if time.time() - last_gain_time > 12:
                ctx.log_event("Health not rising while healing")
                return False
            if hunger < 18:
                if not select_hotbar_item(ctx, "minecraft:apple"):
                    ctx.log_event("Apple not available in hotbar")
                    return False
                ctx.client.transport.dispatch("use_item", {"duration_ms": 1600})
                time.sleep(2.0)
            else:
                time.sleep(1.5)
            if ctx.count_item("minecraft:apple") <= 0:
                ctx.log_event("Ran out of apples while healing")
                return False
        return True

    # T500: Melee Attack
    def t500_setup(ctx: TestContext):
        prepare_test_world(ctx)
        _setup_bounds(ctx, "T500", anchors["T500"])
        ctx.set_gamemode("survival")
        ctx.run_command("difficulty normal")
        ctx.run_command("time set night")
        ctx.clear_inventory()
        ctx.give_item("minecraft:iron_sword", 1)
        ctx.give_item("minecraft:cooked_beef", 16)
        summon_near(ctx, "minecraft:zombie", dx=1, dy=1, dz=0, nbt="{NoAI:1b}")
        wait_for_entity(ctx, "minecraft:zombie", radius=12, timeout=3.0)
        ctx.snapshot("start")

    def t500_step_attack(ctx: TestContext) -> bool:
        ctx.log_event("Attacking zombie...")
        target = wait_for_entity(ctx, "minecraft:zombie", radius=12, timeout=3.0)
        if not target:
            ctx.log_event("No zombie found; re-summoning")
            summon_near(ctx, "minecraft:zombie", dx=1, dy=1, dz=0, nbt="{NoAI:1b}")
            target = wait_for_entity(ctx, "minecraft:zombie", radius=12, timeout=3.0)
        if target:
            target_id = target.get("id")
            for _ in range(6):
                current = get_entity_by_id(ctx, target_id, radius=12)
                if not current:
                    return True
                distance = distance_to_entity(ctx, current)
                if distance > 3.0:
                    ctx.log_event(f"Zombie too far to attack ({distance:.2f} blocks)")
                    return False
                pos = current.get("position", {})
                ctx.client.transport.dispatch("look_at", {
                    "x": pos.get("x", 0) + 0.5,
                    "y": pos.get("y", 0) + 0.5,
                    "z": pos.get("z", 0) + 0.5
                })
                result = ctx.client.transport.dispatch("attack_entity", {"entity_id": target_id})
                if result.get("status") not in (None, "ok", "success"):
                    ctx.log_event(f"Attack failed: {result}")
                    return False
                time.sleep(0.5)
            return get_entity_by_id(ctx, target_id, radius=12) is None
        ctx.log_event("No zombie found to attack")
        return False

    def t500_assert_alive(ctx: TestContext):
        state = ctx.get_state()
        health = state.get("health", 0)
        return health > 0, f"Player health: {health}"

    def t500_teardown(ctx: TestContext):
        teardown_test_world(ctx, bounds=_state("T500").get("bounds"))

    suite.add(TestCase(
        id="T500",
        name="Melee Attack",
        description="Attack hostile mob with sword",
        timeout_seconds=15,
        setup=t500_setup,
        steps=[t500_step_attack],
        assertions=[t500_assert_alive],
        teardown=t500_teardown
    ))

    # T501: Critical Hit
    def t501_setup(ctx: TestContext):
        prepare_test_world(ctx)
        _setup_bounds(ctx, "T501", anchors["T501"])
        ctx.set_gamemode("survival")
        ctx.run_command("difficulty normal")
        ctx.run_command("time set night")
        ctx.clear_inventory()
        ctx.give_item("minecraft:iron_sword", 1)
        summon_near(ctx, "minecraft:zombie", dx=1, dy=1, dz=0, nbt="{NoAI:1b}")
        wait_for_entity(ctx, "minecraft:zombie", radius=12, timeout=3.0)

    def t501_step_crit(ctx: TestContext) -> bool:
        ctx.log_event("Performing critical hit (jump + attack)...")
        target = wait_for_entity(ctx, "minecraft:zombie", radius=12, timeout=3.0)
        if not target:
            ctx.log_event("No zombie found for critical hit")
            return False
        start_health = target.get("health", None)
        if distance_to_entity(ctx, target) > 2.5:
            ctx.log_event("Zombie too far for critical hit")
            return False
        pos = target.get("position", {})
        ctx.client.transport.dispatch("look_at", {
            "x": pos.get("x", 0) + 0.5,
            "y": pos.get("y", 0) + 0.5,
            "z": pos.get("z", 0) + 0.5
        })
        ctx.client.transport.dispatch("chat", {"message": "#jump"})
        time.sleep(0.35)
        result = ctx.client.transport.dispatch("attack_entity", {"entity_id": target.get("id")})
        if result.get("status") not in (None, "ok", "success"):
            ctx.log_event(f"Attack failed: {result}")
            return False
        time.sleep(0.5)
        updated = get_entity_by_id(ctx, target.get("id"), radius=12)
        if updated is None:
            return True
        end_health = updated.get("health", start_health)
        return start_health is None or end_health < start_health

    def t501_teardown(ctx: TestContext):
        teardown_test_world(ctx, bounds=_state("T501").get("bounds"))

    suite.add(TestCase(
        id="T501",
        name="Critical Hit",
        description="Attack while falling for critical damage",
        timeout_seconds=10,
        setup=t501_setup,
        steps=[t501_step_crit],
        assertions=[],
        teardown=t501_teardown
    ))

    # T502: Shield Block
    def t502_setup(ctx: TestContext):
        prepare_test_world(ctx)
        _setup_bounds(ctx, "T502", anchors["T502"])
        ctx.set_gamemode("survival")
        ctx.run_command("difficulty normal")
        ctx.run_command("time set night")
        ctx.clear_inventory()
        ctx.give_item("minecraft:shield", 1)
        summon_near(ctx, "minecraft:zombie", dx=1, dy=1, dz=0)
        wait_for_entity(ctx, "minecraft:zombie", radius=12, timeout=3.0)

    def t502_step_block(ctx: TestContext) -> bool:
        ctx.log_event("Blocking with shield...")
        if not select_hotbar_item(ctx, "minecraft:shield"):
            ctx.log_event("Shield not found to block")
            return False
        target = wait_for_entity(ctx, "minecraft:zombie", radius=12, timeout=2.0)
        if not target:
            ctx.log_event("No zombie found to block")
            return False
        if distance_to_entity(ctx, target) > 2.5:
            ctx.log_event("Zombie too far to block")
            return False
        pos = target.get("position", {})
        ctx.client.transport.dispatch("look_at", {
            "x": pos.get("x", 0) + 0.5,
            "y": pos.get("y", 0) + 0.5,
            "z": pos.get("z", 0) + 0.5
        })
        ctx.client.transport.dispatch("attack_entity", {"entity_id": target.get("id")})
        time.sleep(0.2)
        start_health = ctx.get_state().get("health", 20)
        ctx.client.transport.dispatch("use_item", {"duration_ms": 1500})
        time.sleep(1.6)
        end_health = ctx.get_state().get("health", 20)
        return end_health >= start_health

    def t502_teardown(ctx: TestContext):
        teardown_test_world(ctx, bounds=_state("T502").get("bounds"))

    suite.add(TestCase(
        id="T502",
        name="Shield Block",
        description="Block incoming damage with shield",
        timeout_seconds=10,
        setup=t502_setup,
        steps=[t502_step_block],
        assertions=[],
        teardown=t502_teardown
    ))

    # T503: Ranged Attack
    def t503_setup(ctx: TestContext):
        prepare_test_world(ctx)
        _setup_bounds(ctx, "T503", anchors["T503"])
        ctx.set_gamemode("survival")
        ctx.run_command("difficulty normal")
        ctx.run_command("time set night")
        ctx.clear_inventory()
        ctx.give_item("minecraft:iron_helmet", 1)
        ctx.give_item("minecraft:iron_chestplate", 1)
        ctx.give_item("minecraft:iron_leggings", 1)
        ctx.give_item("minecraft:iron_boots", 1)
        ctx.give_item("minecraft:shield", 1)
        ctx.give_item("minecraft:bow", 1)
        ctx.give_item("minecraft:arrow", 32)
        ctx.client.transport.dispatch("equip", {"slot": "head", "item": "minecraft:iron_helmet"})
        ctx.client.transport.dispatch("equip", {"slot": "chest", "item": "minecraft:iron_chestplate"})
        ctx.client.transport.dispatch("equip", {"slot": "legs", "item": "minecraft:iron_leggings"})
        ctx.client.transport.dispatch("equip", {"slot": "feet", "item": "minecraft:iron_boots"})
        ctx.client.transport.dispatch("equip", {"slot": "offhand", "item": "minecraft:shield"})
        summon_near(ctx, "minecraft:zombie", dx=6, dy=1, dz=0, nbt="{NoAI:1b}")
        wait_for_entity(ctx, "minecraft:zombie", radius=20, timeout=3.0)
        ctx.snapshot("start")
        if ctx.snapshots:
            ctx.snapshots[-1]["arrow_count"] = ctx.count_item("minecraft:arrow")

    def t503_step_shoot(ctx: TestContext) -> bool:
        ctx.log_event("Shooting arrow...")
        ctx.set_gamemode("survival")
        if not select_hotbar_item(ctx, "minecraft:bow"):
            ctx.log_event("Bow not found to shoot")
            return False
        target = wait_for_entity(ctx, "minecraft:zombie", radius=16, timeout=2.0)
        if not target:
            ctx.log_event("No zombie found to shoot")
            return False
        target_id = target.get("id")
        start_health = target.get("health", None)

        def _shoot_and_check() -> bool:
            pos = target.get("position", {})
            ctx.client.transport.dispatch("look_at", {
                "x": pos.get("x", 0) + 0.5,
                "y": pos.get("y", 0) + 0.5,
                "z": pos.get("z", 0) + 0.5
            })
            time.sleep(0.2)
            ctx.client.transport.dispatch("use_item", {"duration_ms": 1800})
            time.sleep(2.4)
            updated = get_entity_by_id(ctx, target_id, radius=16)
            if updated is None:
                return True
            end_health = updated.get("health", start_health)
            if start_health is None or end_health < start_health:
                return True
            arrows_after = ctx.count_item("minecraft:arrow")
            return arrows_after < ctx.snapshots[-1].get("arrow_count", arrows_after)

        for _ in range(3):
            if _shoot_and_check():
                return True
            ctx.log_event("Arrow did not damage target; retrying")
        return False

    def t503_assert_arrow(ctx: TestContext):
        start_count = 32
        if ctx.snapshots:
            start_count = ctx.snapshots[-1].get("arrow_count", start_count)
        wait_for_item_decrease(ctx, "minecraft:arrow", start_count, timeout=4.0)
        count = ctx.count_item("minecraft:arrow")
        return count < start_count, f"Arrows remaining: {count}"

    def t503_teardown(ctx: TestContext):
        teardown_test_world(ctx, bounds=_state("T503").get("bounds"))

    suite.add(TestCase(
        id="T503",
        name="Ranged Attack",
        description="Shoot arrow with bow",
        timeout_seconds=10,
        setup=t503_setup,
        steps=[t503_step_shoot],
        assertions=[t503_assert_arrow],
        teardown=t503_teardown
    ))

    # T504: Health Management
    def t504_setup(ctx: TestContext):
        prepare_test_world(ctx)
        _setup_bounds(ctx, "T504", anchors["T504"])
        ctx.set_gamemode("survival")
        ctx.clear_inventory()
        ctx.give_item("minecraft:cooked_beef", 8)
        ctx.give_item("minecraft:golden_apple", 1)
        ctx.client.transport.dispatch("chat", {"message": "/effect give @p instant_damage 1 0"})
        time.sleep(0.5)
        ctx.snapshot("start")

    def t504_step_heal(ctx: TestContext) -> bool:
        state = ctx.get_state()
        health = state.get("health", 20)
        ctx.log_event(f"Current health: {health}")
        if health < 20:
            ctx.log_event("Eating to heal...")
            if select_hotbar_item(ctx, "minecraft:cooked_beef"):
                ctx.client.transport.dispatch("use_item", {"duration_ms": 1500})
                time.sleep(2)
        return True

    def t504_teardown(ctx: TestContext):
        teardown_test_world(ctx, bounds=_state("T504").get("bounds"))

    suite.add(TestCase(
        id="T504",
        name="Health Management",
        description="Heal damage by eating food",
        timeout_seconds=15,
        setup=t504_setup,
        steps=[t504_step_heal],
        assertions=[],
        teardown=t504_teardown
    ))

    # T505: Progressive Waves
    def t505_setup(ctx: TestContext):
        prepare_test_world(ctx)
        _setup_bounds(ctx, "T505", anchors["T505"], size=40)
        ctx.set_gamemode("survival")
        ctx.run_command("difficulty normal")
        ctx.clear_inventory()
        ctx.give_item("minecraft:iron_sword", 1)
        ctx.give_item("minecraft:iron_helmet", 1)
        ctx.give_item("minecraft:iron_chestplate", 1)
        ctx.give_item("minecraft:iron_leggings", 1)
        ctx.give_item("minecraft:iron_boots", 1)
        ctx.give_item("minecraft:shield", 1)
        ctx.give_item("minecraft:apple", 64)
        ctx.client.transport.dispatch("equip", {"slot": "head", "item": "minecraft:iron_helmet"})
        ctx.client.transport.dispatch("equip", {"slot": "chest", "item": "minecraft:iron_chestplate"})
        ctx.client.transport.dispatch("equip", {"slot": "legs", "item": "minecraft:iron_leggings"})
        ctx.client.transport.dispatch("equip", {"slot": "feet", "item": "minecraft:iron_boots"})
        ctx.client.transport.dispatch("equip", {"slot": "offhand", "item": "minecraft:shield"})
        select_hotbar_item(ctx, "minecraft:iron_sword")
        ctx.set_health(20)
        center = tuple(int(v) for v in ctx.get_position())
        arena_state.clear()
        arena_state.update(build_arena(ctx, center))
        ctx.snapshot("arena_ready")

    def t505_step_waves(ctx: TestContext) -> bool:
        enemy_type = "minecraft:zombie"
        heal_threshold = 12.0
        safe_threshold = 16.0
        bounds = arena_state
        if not bounds:
            ctx.log_event("Arena bounds not set")
            return False
        if not within_bounds(ctx, bounds):
            ctx.log_event("Player not inside arena at start")
            return False
        for wave in range(1, 9):
            ctx.log_event(f"Starting wave {wave}")
            spawn_wave(ctx, bounds, enemy_type, wave)
            start_wave = time.time()
            last_engage = time.time()
            while time.time() - start_wave < 90:
                if not within_bounds(ctx, bounds):
                    ctx.log_event("Left arena bounds")
                    return False
                if ctx.get_state().get("is_dead"):
                    ctx.log_event("Player died")
                    return False
                if not ctx.has_item("minecraft:iron_sword", 1):
                    ctx.log_event("Missing sword during combat")
                    return False
                health = ctx.get_state().get("health", 20)
                if health <= heal_threshold:
                    if not _retreat_and_heal(ctx, bounds, enemy_type, safe_threshold):
                        return False
                    select_hotbar_item(ctx, "minecraft:iron_sword")
                    last_engage = time.time()
                    continue
                targets = [ent for ent in get_entities(ctx, radius=25) if ent.get("type") == enemy_type]
                if not targets:
                    ctx.log_event(f"Wave {wave} cleared")
                    break
                if time.time() - last_engage > 10:
                    ctx.log_event("Failed to engage enemies in time")
                    return False
                target = min(targets, key=lambda ent: distance_to_entity(ctx, ent))
                distance = distance_to_entity(ctx, target)
                if distance > 3.5:
                    _move_towards(ctx, bounds, target, step=4)
                pos = target.get("position", {})
                ctx.client.transport.dispatch("look_at", {
                    "x": pos.get("x", 0) + 0.5,
                    "y": pos.get("y", 0) + 0.5,
                    "z": pos.get("z", 0) + 0.5
                })
                target_id = target.get("id")
                try:
                    ctx.client.transport.dispatch("attack_entity", {"entity_id": target_id})
                except Exception as e:
                    if "Entity not found" in str(e):
                        ctx.log_event(f"Target vanished: {target_id}")
                        continue
                    raise
                last_engage = time.time()
                if len(targets) >= 2:
                    _kite_away(ctx, bounds, targets, step=4)
                time.sleep(0.4)
            else:
                ctx.log_event(f"Wave {wave} timeout")
                return False
        return True

    def t505_teardown(ctx: TestContext):
        teardown_test_world(ctx, bounds=_state("T505").get("bounds"))

    suite.add(TestCase(
        id="T505",
        name="Progressive Waves",
        description="Fight 1..8 mobs with kiting and healing discipline",
        timeout_seconds=720,
        setup=t505_setup,
        steps=[t505_step_waves],
        teardown=t505_teardown,
        assertions=[]
    ))

    return suite


__all__ = ["create_extended_suite_500"]
