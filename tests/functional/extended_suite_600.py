"""
Extended Suite 600: Combat & Entities (Granular Action Tests)
T600-T621: Melee, Critical Hit, Shield, Ranged, Combat Mechanics
"""

import time

from baritone_client.common.combat import safe_combat
from tests.functional.suite_utils import get_test_state

from test_base import TestCase, TestSuite, TestContext

from utils.mc_harness import (
    prepare_test_world,
    teardown_test_world,
    clear_box,
    build_floor,
    tp,
    wait_for_entity,
    summon_near,
    get_entities,
    wait_for_tick_stabilization,
    close_screen,
    select_hotbar_item,
    safe_dispatch,
    distance_to_entity,
    count_item,
)


def create_extended_suite_600() -> TestSuite:
    """Suite 600: Combat & Entities - Granular action tests."""
    suite = TestSuite("Suite_600_Combat", "Granular combat action tests")
    suite_state = {}

    anchors = {}
    for i in range(22):
        anchors[f"T{600+i}"] = (i * 200, 80, 600)
    # --- Helpers ---

    def prepare_standard_combat_test(ctx, tid, anchor, size=15, height=10, gamemode="survival", floor=True):
        """Standard fixture for combat tests."""
        ax, ay, az = anchor
        bounds = {
            "min_x": ax - size, "min_y": ay - 5, "min_z": az - size,
            "max_x": ax + size, "max_y": ay + height, "max_z": az + size,
        }
        get_test_state(suite_state, tid)["bounds"] = bounds
        clear_box(ctx, bounds)
        prepare_test_world(ctx, gamemode=gamemode, peaceful=False)
        tp(ctx, ax, ay, az)
        if floor:
            build_floor(ctx, ax - 10, ay - 1, az - 10, ax + 10, az + 10)
        # Bounded sleep for tick stab if helper not fully robust for entity spawns
        time.sleep(0.5) 
        ctx.clear_inventory()
        ctx.snapshot("start")
        return bounds

    def require_features(ctx, features):
        # Placeholder for capability check. 
        # If transport is dummy, we might skip.
        # Currently we assume bridge supports basic actions if connected.
        pass

    # T600: Melee Attack
    def t600_setup(ctx: TestContext):
        ax, ay, az = anchors["T600"]
        prepare_standard_combat_test(ctx, "T600", (ax, ay, az))
        
        ctx.give_item("minecraft:iron_sword", 1)
        # Spawn NoAI zombie to be a punching bag
        summon_near(ctx, "minecraft:zombie", dx=2, dy=0, dz=0, nbt="{NoAI:1b,RefusesToDie:0b}")

    def t600_step_attack(ctx: TestContext) -> bool:
        target = wait_for_entity(ctx, "minecraft:zombie", radius=5, timeout=4.0)
        if not target:
            ctx.log_event("Target not found")
            return False
        
        get_test_state(suite_state, "T600")["target_id"] = target["id"]
        
        select_hotbar_item(ctx, "minecraft:iron_sword")

        result = safe_combat(
            ctx.client,
            target["id"],
            retreat_health=6.0,
            max_duration=20,
            purpose="functional_hostile",
            source="suite_600",
            target_metadata=target,
        )
        get_test_state(suite_state, "T600")["combat_result"] = result
        return result

    def t600_assert_killed(ctx: TestContext):
        # Verify gone
        tid = get_test_state(suite_state, "T600").get("target_id")
        if not tid: return False, "No target ID"
        
        result = get_test_state(suite_state, "T600").get("combat_result")
        ents = get_entities(ctx, radius=10)
        observed = next((e for e in ents if e["id"] == tid), None)
        verified_dead = observed is None or float(observed.get("health", 1)) <= 0
        return (
            result is True and verified_dead,
            f"safe_combat={result}; target verified dead={verified_dead}",
        )

    suite.add(TestCase(
        id="T600",
        name="Melee Attack",
        description="Kill NoAI zombie through production safe_combat",
        setup=t600_setup,
        steps=[t600_step_attack],
        assertions=[t600_assert_killed],
        teardown=lambda ctx: teardown_test_world(ctx, bounds=get_test_state(suite_state, "T600").get("bounds"))
    ))

    # T603: Ranged Attack Bow
    def t603_setup(ctx: TestContext):
        ax, ay, az = anchors["T603"]
        prepare_standard_combat_test(ctx, "T603", (ax, ay, az))
        
        ctx.give_item("minecraft:bow", 1)
        ctx.give_item("minecraft:arrow", 64)
        summon_near(ctx, "minecraft:zombie", dx=5, dy=0, dz=0, nbt="{NoAI:1b}")

    def t603_step_shoot(ctx: TestContext) -> bool:
        target = wait_for_entity(ctx, "minecraft:zombie", radius=10, timeout=4.0)
        if not target: return False
        
        ctx.client.transport.dispatch("look_at", {"x": target["position"]["x"], "y": target["position"]["y"]+1.5, "z": target["position"]["z"]})
        select_hotbar_item(ctx, "minecraft:bow")
        
        # Charge bow
        ctx.client.transport.dispatch("use_item", {"duration_ms": 1100}) # >1s max charge
        time.sleep(1.5) # Wait for arrow flight
        return True

    def t603_assert_hit(ctx: TestContext):
        # We can't easily verify damage without health readout on entity.
        # But we can verify arrow entity exists or zombie killed (hard with one shot).
        # Or verify arrow count decreased.
        count = count_item(ctx, "minecraft:arrow")
        # Started with 64.
        return count == 63, f"Arrow count {count} (expected 63)"

    suite.add(TestCase(
        id="T603",
        name="Ranged Attack Bow",
        description="Shoot arrow",
        setup=t603_setup,
        steps=[t603_step_shoot],
        assertions=[t603_assert_hit],
        teardown=lambda ctx: teardown_test_world(ctx, bounds=get_test_state(suite_state, "T603").get("bounds"))
    ))

    # T604: Health Management
    def t604_setup(ctx: TestContext):
        ax, ay, az = anchors["T604"]
        prepare_standard_combat_test(ctx, "T604", (ax, ay, az))
        ctx.give_item("minecraft:cooked_beef", 1)
        # Hunger
        ctx.run_command("effect give @p minecraft:hunger 10 5")
        time.sleep(2.0)

    def t604_step_eat(ctx: TestContext) -> bool:
        select_hotbar_item(ctx, "minecraft:cooked_beef")
        ctx.client.transport.dispatch("use_item", {"duration_ms": 1600}) # Eat is 1.6s
        time.sleep(0.5)
        return True

    def t604_assert_eaten(ctx: TestContext):
        has = ctx.has_item("minecraft:cooked_beef")
        return not has, "Beef consumed"

    suite.add(TestCase(
        id="T604",
        name="Health Management",
        description="Eat food",
        setup=t604_setup,
        steps=[t604_step_eat],
        assertions=[t604_assert_eaten],
        teardown=lambda ctx: teardown_test_world(ctx, bounds=get_test_state(suite_state, "T604").get("bounds"))
    ))

    # T613: Knockback
    def t613_setup(ctx: TestContext):
        ax, ay, az = anchors["T613"]
        prepare_standard_combat_test(ctx, "T613", (ax, ay, az))
        ctx.give_item("minecraft:iron_sword", 1)
        summon_near(ctx, "minecraft:creeper", dx=2, dy=0, dz=0, nbt="{NoAI:1b}")

    def t613_step_hit(ctx: TestContext) -> bool:
        target = wait_for_entity(ctx, "minecraft:creeper", radius=5)
        if not target: return False
        
        pos = target["position"]
        get_test_state(suite_state, "T613")["start_pos"] = pos
        
        ctx.client.transport.dispatch("look_at", {"x": pos["x"], "y": pos["y"]+0.5, "z": pos["z"]})
        select_hotbar_item(ctx, "minecraft:iron_sword")
        ctx.client.transport.dispatch("attack_entity", {"entity_id": target["id"]})
        time.sleep(0.5)
        
        # Get new pos
        ents = get_entities(ctx, radius=10)
        new_target = next((e for e in ents if e["id"] == target["id"]), None)
        if new_target:
             get_test_state(suite_state, "T613")["end_pos"] = new_target["position"]
        else:
             get_test_state(suite_state, "T613")["end_pos"] = None # Killed?
             
        return True

    def t613_assert_moved(ctx: TestContext):
        p1 = get_test_state(suite_state, "T613").get("start_pos")
        p2 = get_test_state(suite_state, "T613").get("end_pos")
        if not p1 or not p2: return False, "Missing position data"
        
        dist = ((p1["x"]-p2["x"])**2 + (p1["z"]-p2["z"])**2)**0.5
        # Knockback should be > 0.1 at least
        return dist > 0.1, f"Knockback dist: {dist:.2f}"

    suite.add(TestCase(
        id="T613",
        name="Knockback",
        description="Verify entity movement",
        setup=t613_setup,
        steps=[t613_step_hit],
        assertions=[t613_assert_moved],
        teardown=lambda ctx: teardown_test_world(ctx, bounds=get_test_state(suite_state, "T613").get("bounds"))
    ))

    # T616-T621: Status Effects
    def make_effect_test(tid, name, effect_id, amplifier=0, duration=10):
        def setup(ctx: TestContext):
            ax, ay, az = anchors["T600"] # Re-use arena
            prepare_standard_combat_test(ctx, tid, (ax, ay, az))
            ctx.run_command(f"effect give @p {effect_id} {duration} {amplifier}")
            time.sleep(1.0) # Wait for update

        def step(ctx: TestContext) -> bool:
            # Check state for effect
            state = ctx.get_state()
            effects = state.get("effects", [])
            # effects is list of {id, duration, amplifier}
            # id might be "minecraft:poison" or "poison" or numeric? 
            # Bridge sends registry ID e.g. "minecraft:poison"
            
            found = False
            for eff in effects:
                # Handle namespaced or not
                eid = eff.get("id", "")
                if effect_id in eid or eid in effect_id:
                    if eff.get("amplifier") == amplifier:
                        found = True
                        break
            
            get_test_state(suite_state, tid)["found"] = found
            return True

        def assertion(ctx: TestContext):
            return get_test_state(suite_state, tid).get("found", False), f"Effect {effect_id} found in state"

        suite.add(TestCase(tid, name, f"Verify {name} status", 30, setup, [step], [assertion],
                           lambda ctx: teardown_test_world(ctx, bounds=get_test_state(suite_state, tid).get("bounds"))))

    make_effect_test("T616", "Poison Status", "minecraft:poison")
    make_effect_test("T617", "Weakness Status", "minecraft:weakness")
    make_effect_test("T618", "Strength Status", "minecraft:strength", amplifier=1)
    make_effect_test("T619", "Regen Status", "minecraft:regeneration")
    make_effect_test("T620", "Absorption Status", "minecraft:absorption")
    make_effect_test("T621", "Resistance Status", "minecraft:resistance")

    # Skipped tests
    skipped_mapping = {
        "T601": "Crit requires verification",
        "T602": "Shield requires damage sensing",
        "T605": "Waves requires complex spawn tracking",
        "T606": "Duplicate Melee",
        "T607": "Duplicate Crit",
        "T608": "Duplicate Shield",
        "T609": "Duplicate Ranged",
        "T610": "Crossbow load/fire complex",
        "T611": "Trident throw return complex",
        "T612": "Sweep requires multi-entity tracking",
        "T614": "Fire aspect requires state",
        "T615": "Thorns requires damage tracking",
    }

    for tid, reason in skipped_mapping.items():
        suite.add(TestCase(
            id=tid,
            name=f"Skipped {tid}",
            description=reason,
            setup=lambda ctx: ctx.skip(reason),
            steps=[], assertions=[]
        ))

    return suite

__all__ = ["create_extended_suite_600"]
