from tests.functional.test_base import FunctionalCase, FunctionalResult
from tests.functional.shared import inventory_ops


class MinimalContext:
    def __init__(self):
        self.events = []
        self.snapshots = []
        self.start_time = 0.0

    def log_event(self, event):
        self.events.append(event)

    def snapshot(self, label):
        self.snapshots.append(label)

    def get_state(self):
        return {"health": 20, "is_dead": False}


def test_failed_assertion_fails_functional_case():
    case = FunctionalCase(
        id="T_ASSERT_FAIL",
        name="Assertion failure regression",
        description="A false assertion must not be reported as PASS",
        assertions=[lambda ctx: (False, "expected failure")],
    )
    result, message, events = case.run(MinimalContext())
    assert result is FunctionalResult.FAIL
    assert message == "expected failure"
    assert any("ASSERT 0 FAILED" in event for event in events)


def test_successful_assertion_passes_functional_case():
    case = FunctionalCase(
        id="T_ASSERT_PASS",
        name="Assertion success regression",
        description="A true assertion is reported as PASS",
        assertions=[lambda ctx: (True, "verified")],
    )
    result, message, events = case.run(MinimalContext())
    assert result is FunctionalResult.PASS
    assert message == "All steps and assertions passed"
    assert any("ASSERT 0 PASSED" in event for event in events)


def test_crafting_table_open_reuses_nearby_existing_table(monkeypatch):
    class Transport:
        def dispatch(self, route, payload):
            if route == "find_blocks":
                return {
                    "found": [
                        {"x": -8, "y": 79, "z": -121, "distance": 2.0}
                    ]
                }
            return {}

    class Context:
        client = type("Client", (), {"transport": Transport()})()

        def get_position(self):
            return (-7, 79, -120)

        def log_event(self, _event):
            return None

    monkeypatch.setattr(
        inventory_ops,
        "move_near",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("an in-range table must open from the current safe tile")
        ),
    )
    monkeypatch.setattr(
        inventory_ops,
        "block_id_at",
        lambda _ctx, x, y, z: (
            "minecraft:crafting_table"
            if (x, y, z) == (-8, 79, -121)
            else "minecraft:air"
        ),
    )
    monkeypatch.setattr(
        inventory_ops, "do_open_container", lambda _ctx, pos, timeout: pos == (-8, 79, -121)
    )
    monkeypatch.setattr(
        inventory_ops,
        "bot_place_block",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("existing table must be reused")
        ),
    )

    assert inventory_ops.ensure_crafting_table_open(Context())


def test_smelt_polls_furnace_instead_of_sleeping_through_bridge_idle(monkeypatch):
    class Transport:
        def __init__(self):
            self.screen_reads = 0

        def dispatch(self, route, _payload):
            if route == "get_screen":
                self.screen_reads += 1
                return {
                    "slots": [
                        {"slot": 0, "id": "minecraft:air", "count": 0},
                        {"slot": 1, "id": "minecraft:coal", "count": 1},
                        {"slot": 2, "id": "minecraft:iron_ingot", "count": 2},
                        {"slot": 3, "id": "minecraft:raw_iron", "count": 2},
                        {"slot": 4, "id": "minecraft:coal", "count": 1},
                    ]
                }
            return {}

    class Context:
        def __init__(self):
            self.client = type("Client", (), {"transport": Transport()})()
            self.ingots = 0

        def count_item(self, item_id):
            return self.ingots if item_id == "minecraft:iron_ingot" else 0

        def log_event(self, _event):
            return None

    ctx = Context()
    sleeps = []

    monkeypatch.setattr(inventory_ops, "block_id_at", lambda *_args: "minecraft:furnace")
    monkeypatch.setattr(inventory_ops, "do_open_container", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(inventory_ops, "do_close_container", lambda *_args: None)
    monkeypatch.setattr(inventory_ops.time, "sleep", sleeps.append)

    def click(_ctx, slot, _action, _button=0):
        if slot == 2:
            ctx.ingots = 2

    monkeypatch.setattr(inventory_ops, "safe_inventory_click", click)

    assert inventory_ops.smelt_in_furnace(
        ctx,
        (1, 2, 3),
        "minecraft:raw_iron",
        "minecraft:coal",
        "minecraft:iron_ingot",
        2,
    )
    assert ctx.client.transport.screen_reads >= 2
    assert max(sleeps) <= 0.3


def test_manual_iron_chestplate_uses_verified_eight_ingot_pattern(monkeypatch):
    class Transport:
        def __init__(self):
            self.screen_reads = 0

        def dispatch(self, route, _payload):
            if route != "get_screen":
                return {}
            self.screen_reads += 1
            output = (
                "minecraft:iron_chestplate"
                if self.screen_reads >= 11
                else "minecraft:air"
            )
            return {
                "slots": [
                    {"slot": 0, "id": output, "count": int(output != "minecraft:air")},
                    *[
                        {"slot": slot, "id": "minecraft:air", "count": 0}
                        for slot in range(1, 10)
                    ],
                    {
                        "slot": 10,
                        "id": "minecraft:iron_ingot",
                        "count": 8,
                    },
                ]
            }

    class Context:
        def __init__(self):
            self.client = type("Client", (), {"transport": Transport()})()
            self.events = []

        def log_event(self, event):
            self.events.append(event)

        def has_item(self, item_id):
            return item_id == "minecraft:iron_chestplate"

    ctx = Context()
    clicks = []
    monkeypatch.setattr(
        inventory_ops,
        "safe_inventory_click",
        lambda _ctx, slot, action, button=0: clicks.append((slot, action, button)),
    )
    monkeypatch.setattr(inventory_ops, "do_close_container", lambda _ctx: None)
    monkeypatch.setattr(inventory_ops.time, "sleep", lambda _seconds: None)

    assert inventory_ops._craft_armor_manual_generic(
        ctx,
        "minecraft:iron_ingot",
        "chestplate",
        "minecraft:iron_chestplate",
    )
    placed_slots = [
        slot
        for slot, action, button in clicks
        if action == "PICKUP" and button == 1
    ]
    assert placed_slots == [1, 3, 4, 5, 6, 7, 8, 9]
    assert (0, "QUICK_MOVE", 0) in clicks


def test_manual_stone_axe_prefers_atomic_place_recipe(monkeypatch):
    calls = []

    class Transport:
        def dispatch(self, route, payload):
            calls.append((route, payload))
            if route == "get_screen":
                return {
                    "type": "CraftingScreenHandler",
                    "slots": [
                        {"slot": slot, "id": "minecraft:air", "count": 0}
                        for slot in range(46)
                    ],
                }
            if route == "place_recipe":
                return {"data": {"crafted": True}}
            raise AssertionError(f"unexpected fallback route: {route}")

    class Context:
        client = type("Client", (), {"transport": Transport()})()

        def log_event(self, _event):
            return None

    assert inventory_ops.craft_stone_axe_manual(Context())
    assert calls[0] == ("get_screen", {})
    assert calls[1:] == [
        (
            "place_recipe",
            {
                "placements": [
                    {"selector": "minecraft:cobblestone", "grid_slot": 1},
                    {"selector": "minecraft:cobblestone", "grid_slot": 2},
                    {"selector": "minecraft:cobblestone", "grid_slot": 4},
                    {"selector": "minecraft:stick", "grid_slot": 5},
                    {"selector": "minecraft:stick", "grid_slot": 8},
                ],
                "expected_output": "minecraft:stone_axe",
                "expected_count": 1,
                "crafts": 1,
            },
        )
    ]


def test_generic_manual_recipe_places_each_ingredient_and_verifies_output(monkeypatch):
    class Transport:
        def dispatch(self, route, _payload):
            if route != "get_screen":
                return {}
            return {
                "type": "class_1714",
                "slots": [
                    {
                        "slot": 0,
                        "id": "minecraft:book",
                        "count": 1,
                    },
                    *[
                        {"slot": slot, "id": "minecraft:air", "count": 0}
                        for slot in range(1, 10)
                    ],
                    {
                        "slot": 10,
                        "id": "minecraft:paper",
                        "count": 3,
                    },
                    {
                        "slot": 11,
                        "id": "minecraft:leather",
                        "count": 1,
                    },
                    *[
                        {"slot": slot, "id": "minecraft:air", "count": 0}
                        for slot in range(12, 46)
                    ],
                ],
            }

    class Context:
        def __init__(self):
            self.client = type("Client", (), {"transport": Transport()})()
            self.books = 0

        def log_event(self, _event):
            return None

        def count_item(self, item_id):
            return self.books if item_id == "minecraft:book" else 0

    ctx = Context()
    clicks = []

    def click(_ctx, slot, action, button=0):
        clicks.append((slot, action, button))
        if slot == 0 and action == "QUICK_MOVE":
            ctx.books = 1

    monkeypatch.setattr(inventory_ops, "safe_inventory_click", click)
    monkeypatch.setattr(inventory_ops, "do_close_container", lambda _ctx: None)
    monkeypatch.setattr(inventory_ops.time, "sleep", lambda _seconds: None)

    assert inventory_ops.craft_recipe_manual(
        ctx,
        "minecraft:book",
        [
            ("minecraft:paper", 1),
            ("minecraft:paper", 2),
            ("minecraft:paper", 3),
            ("minecraft:leather", 4),
        ],
    )
    right_click_targets = [
        slot
        for slot, action, button in clicks
        if action == "PICKUP" and button == 1
    ]
    assert right_click_targets == [1, 2, 3, 4]
    assert (0, "QUICK_MOVE", 0) in clicks


def test_full_inventory_discards_only_approved_stack_for_crafting_output(monkeypatch):
    slots = [
        {"slot": slot, "id": "minecraft:cobblestone", "count": 64}
        for slot in range(46)
    ]
    slots[0] = {"slot": 0, "id": "minecraft:air", "count": 0}
    slots[9] = {"slot": 9, "id": "minecraft:dirt", "count": 64}

    class Transport:
        def dispatch(self, route, payload):
            if route == "get_screen":
                return {"type": "PlayerScreenHandler", "slots": slots}
            if route == "inventory_click":
                assert payload == {
                    "slot": 9,
                    "type": "THROW",
                    "button": 1,
                }
                slots[9] = {"slot": 9, "id": "minecraft:air", "count": 0}
            return {}

    class Context:
        client = type("Client", (), {"transport": Transport()})()

        def __init__(self):
            self.events = []

        def log_event(self, event):
            self.events.append(event)

    monkeypatch.setattr(inventory_ops.time, "sleep", lambda _seconds: None)
    ctx = Context()

    assert inventory_ops.ensure_player_crafting_output_space(ctx)
    assert any("dropping low-value stack minecraft:dirt" in event for event in ctx.events)


def test_full_valuable_inventory_refuses_to_discard_for_crafting(monkeypatch):
    slots = [
        {"slot": slot, "id": "minecraft:diamond", "count": 64}
        for slot in range(46)
    ]
    slots[0] = {"slot": 0, "id": "minecraft:air", "count": 0}

    class Transport:
        def dispatch(self, route, _payload):
            if route == "get_screen":
                return {"type": "PlayerScreenHandler", "slots": slots}
            if route == "inventory_click":
                raise AssertionError("valuable inventory must not be discarded")
            return {}

    class Context:
        client = type("Client", (), {"transport": Transport()})()

        def __init__(self):
            self.events = []

        def log_event(self, event):
            self.events.append(event)

    ctx = Context()

    assert not inventory_ops.ensure_player_crafting_output_space(ctx)
    assert any("no approved low-value stack" in event for event in ctx.events)


def test_full_inventory_discards_one_redundant_cobble_stack(monkeypatch):
    slots = [
        {"slot": slot, "id": "minecraft:iron_ingot", "count": 64}
        for slot in range(46)
    ]
    slots[0] = {"slot": 0, "id": "minecraft:air", "count": 0}
    slots[10] = {"slot": 10, "id": "minecraft:cobblestone", "count": 54}
    slots[11] = {"slot": 11, "id": "minecraft:cobblestone", "count": 64}
    clicks = []

    class Transport:
        def dispatch(self, route, payload):
            if route == "get_screen":
                return {"type": "CraftingScreenHandler", "slots": slots}
            if route == "inventory_click":
                clicks.append(payload)
                slots[int(payload["slot"])] = {
                    "slot": int(payload["slot"]), "id": "minecraft:air", "count": 0
                }
            return {}

    class Context:
        client = type("Client", (), {"transport": Transport()})()

        def log_event(self, _event):
            return None

    monkeypatch.setattr(inventory_ops.time, "sleep", lambda _seconds: None)

    assert inventory_ops.ensure_crafting_output_space(Context())
    assert clicks == [{"slot": 10, "type": "THROW", "button": 1}]


def test_table_recipe_reserves_real_player_slot_before_atomic_craft(monkeypatch):
    slots = [
        {"slot": slot, "id": "minecraft:air", "count": 0}
        for slot in range(46)
    ]
    for slot in range(10, 46):
        slots[slot] = {"slot": slot, "id": "minecraft:diamond", "count": 64}
    slots[10] = {"slot": 10, "id": "minecraft:wildflowers", "count": 35}
    clicks = []

    class Transport:
        def dispatch(self, route, payload):
            if route == "get_screen":
                return {"type": "CraftingScreenHandler", "slots": slots}
            if route == "inventory_click":
                clicks.append(payload)
                slots[10] = {"slot": 10, "id": "minecraft:air", "count": 0}
            return {}

    class Context:
        client = type("Client", (), {"transport": Transport()})()

        def log_event(self, _event):
            return None

    monkeypatch.setattr(inventory_ops.time, "sleep", lambda _seconds: None)

    assert inventory_ops.ensure_crafting_output_space(Context())
    assert clicks == [{"slot": 10, "type": "THROW", "button": 1}]


def test_manual_crafting_table_recovers_from_full_inventory(monkeypatch):
    slots = [
        {"slot": slot, "id": "minecraft:diamond", "count": 64}
        for slot in range(46)
    ]
    slots[0] = {"slot": 0, "id": "minecraft:air", "count": 0}
    for slot in range(1, 9):
        slots[slot] = {"slot": slot, "id": "minecraft:air", "count": 0}
    slots[9] = {"slot": 9, "id": "minecraft:dirt", "count": 64}
    slots[10] = {
        "slot": 10,
        "id": "minecraft:oak_planks",
        "count": 4,
    }

    screen_reads = {"count": 0}
    close_requests = {"count": 0}

    class Transport:
        def dispatch(self, route, _payload):
            if route == "get_screen":
                screen_reads["count"] += 1
                if screen_reads["count"] <= 2:
                    return {
                        "type": "ChestMenu",
                        "slots": [
                            {"slot": slot, "id": "minecraft:air", "count": 0}
                            for slot in range(63)
                        ],
                    }
                return {"type": "PlayerScreenHandler", "slots": slots}
            if route == "close_screen":
                close_requests["count"] += 1
            return {}

    class Context:
        client = type("Client", (), {"transport": Transport()})()

        def __init__(self):
            self.events = []

        def log_event(self, event):
            self.events.append(event)

        def has_item(self, item_id):
            return any(
                slot.get("id") == item_id and slot.get("count", 0) > 0
                for slot in slots[9:45]
            )

        def wait_for_item(self, item_id, _count, timeout):
            _ = timeout
            return self.has_item(item_id)

    clicks = []

    def click(_ctx, slot, action, button=0):
        clicks.append((slot, action, button))
        if (slot, action, button) == (9, "THROW", 1):
            slots[9] = {"slot": 9, "id": "minecraft:air", "count": 0}
        elif slot in (1, 2, 3, 4) and action == "PICKUP" and button == 1:
            slots[slot] = {
                "slot": slot,
                "id": "minecraft:oak_planks",
                "count": 1,
            }
            if all(slots[index]["id"].endswith("_planks") for index in range(1, 5)):
                slots[0] = {
                    "slot": 0,
                    "id": "minecraft:crafting_table",
                    "count": 1,
                }
        elif (slot, action, button) == (0, "QUICK_MOVE", 0):
            slots[0] = {"slot": 0, "id": "minecraft:air", "count": 0}
            slots[9] = {
                "slot": 9,
                "id": "minecraft:crafting_table",
                "count": 1,
            }

    monkeypatch.setattr(inventory_ops, "safe_inventory_click", click)
    monkeypatch.setattr(inventory_ops, "do_close_container", lambda _ctx: None)
    monkeypatch.setattr(inventory_ops.time, "sleep", lambda _seconds: None)

    assert inventory_ops.craft_crafting_table_manual(Context())
    assert screen_reads["count"] >= 3
    assert close_requests["count"] == 2
    assert (9, "THROW", 1) in clicks
    assert (0, "QUICK_MOVE", 0) in clicks


def test_manual_plank_craft_reserves_output_slot_before_atomic_recipe(monkeypatch):
    slots = [
        {"slot": slot, "id": "minecraft:diamond", "count": 64}
        for slot in range(46)
    ]
    slots[0] = {"slot": 0, "id": "minecraft:air", "count": 0}
    for slot in range(1, 9):
        slots[slot] = {"slot": slot, "id": "minecraft:air", "count": 0}
    slots[9] = {"slot": 9, "id": "minecraft:dirt", "count": 4}
    slots[10] = {"slot": 10, "id": "minecraft:dark_oak_log", "count": 42}
    calls = []

    class Transport:
        def dispatch(self, route, payload):
            calls.append((route, payload))
            if route == "get_screen":
                return {"type": "PlayerScreenHandler", "slots": slots}
            if route == "inventory_click":
                assert payload == {"slot": 9, "type": "THROW", "button": 1}
                slots[9] = {"slot": 9, "id": "minecraft:air", "count": 0}
                return {}
            if route == "place_recipe":
                assert slots[9]["id"] == "minecraft:air"
                slots[10]["count"] -= 1
                slots[9] = {
                    "slot": 9,
                    "id": "minecraft:dark_oak_planks",
                    "count": 4,
                }
                return {"crafted": True}
            return {}

    class Context:
        client = type("Client", (), {"transport": Transport()})()

        def log_event(self, _event):
            return None

        def count_item(self, item_id):
            return sum(
                int(slot.get("count", 0))
                for slot in slots
                if slot.get("id") == item_id
            )

    monkeypatch.setattr(inventory_ops.time, "sleep", lambda _seconds: None)

    assert inventory_ops.craft_planks_manual(
        Context(), "minecraft:dark_oak_planks", output_count=4
    )
    assert calls.index(
        ("inventory_click", {"slot": 9, "type": "THROW", "button": 1})
    ) < next(index for index, call in enumerate(calls) if call[0] == "place_recipe")


def test_out_of_reach_container_is_approached_before_giving_up(monkeypatch):
    """A container just outside reach must be walked to, not abandoned.

    Live 2026-08-01: Bot07 stood 6.1 blocks from its own furnace -- barely
    past the ~4.5 block reach -- and logged 419 retries with zero blocks
    placed. do_open_container returned False on the distance check without
    ever moving, so every retry failed at exactly the same distance.
    """

    class Transport:
        def dispatch(self, _route, _payload=None):
            return {}

    class Context:
        def __init__(self):
            self.events = []
            # Starts out of reach; move_near closes the gap.
            self.position = (-182.0, 104.0, -383.0)
            self.client = type("C", (), {"transport": Transport()})()

        def log_event(self, event):
            self.events.append(event)

        def get_position(self):
            return self.position

    ctx = Context()
    approached = []

    def fake_move_near(_ctx, x, y, z, timeout=20.0):
        approached.append((x, y, z))
        _ctx.position = (x + 1.0, y, z)  # now within reach
        return True

    monkeypatch.setattr(inventory_ops, "block_id_at", lambda *_a: "minecraft:furnace")
    monkeypatch.setattr(inventory_ops, "move_near", fake_move_near)
    monkeypatch.setattr(inventory_ops, "close_screen", lambda *_a, **_k: None)
    monkeypatch.setattr(inventory_ops.time, "sleep", lambda _s: None)
    monkeypatch.setattr(
        inventory_ops, "robust_interact_block", lambda *_a, **_k: False
    )

    # The open itself fails against this stub transport; the reach gate is
    # what matters -- it must no longer short-circuit before moving.
    inventory_ops.do_open_container(ctx, (-188, 104, -382), timeout=0.1)

    assert approached == [(-188, 104, -382)], "must walk to the container"
    assert not any("Still too far" in e for e in ctx.events)


def test_container_still_out_of_reach_after_approach_gives_up(monkeypatch):
    """If the approach cannot close the gap, fail rather than loop."""

    class Context:
        def __init__(self):
            self.events = []
            self.position = (-182.0, 104.0, -383.0)
            self.client = None

        def log_event(self, event):
            self.events.append(event)

        def get_position(self):
            return self.position

    ctx = Context()

    monkeypatch.setattr(inventory_ops, "block_id_at", lambda *_a: "minecraft:furnace")
    # Pathing fails: position never changes.
    monkeypatch.setattr(
        inventory_ops, "move_near", lambda *_a, **_k: False
    )

    assert inventory_ops.do_open_container(ctx, (-188, 104, -382), timeout=0.1) is False
    assert any("Still too far" in e for e in ctx.events)


def test_catalog_container_probe_can_limit_open_attempts(monkeypatch):
    """A blocked catalog landmark should not hold the bot for four cycles."""

    class Transport:
        def dispatch(self, route, _payload=None):
            if route == "get_screen":
                return {
                    "data": {
                        "type": "PlayerScreenHandler",
                        "sync_id": 0,
                        "total_slots": 46,
                    }
                }
            return {}

    class Context:
        client = type("Client", (), {"transport": Transport()})()

        def __init__(self):
            self.events = []

        def log_event(self, event):
            self.events.append(event)

        def get_position(self):
            return (1.0, 65.0, 1.0)

    interactions = []
    monkeypatch.setattr(
        inventory_ops, "block_id_at", lambda *_args: "minecraft:chest"
    )
    monkeypatch.setattr(inventory_ops, "close_screen", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(inventory_ops.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        inventory_ops,
        "robust_interact_block",
        lambda *_args, **_kwargs: interactions.append(True) or False,
    )

    assert not inventory_ops.do_open_container(
        Context(),
        (1, 65, 1),
        timeout=0.01,
        attempts=1,
    )
    assert interactions == [True]


def test_adjacent_support_finds_a_lateral_neighbour():
    """A roof interior has air below but a solid block beside it.

    Aiming at the target's own empty centre (the old below-only behaviour)
    aims at nothing; the placement must aim at the real neighbour.
    """
    from tests.functional.shared import block_ops

    class Context:
        def get_block(self, x, y, z):
            # Only the block to the west is solid -- e.g. the roof block
            # placed immediately before this one.
            if (x, y, z) == (9, 70, 5):
                return {"id": "minecraft:oak_planks"}
            return {"id": "minecraft:air"}

        client = None

    assert block_ops.adjacent_support(Context(), 10, 70, 5) == (9, 70, 5)


def test_adjacent_support_prefers_the_block_below():
    from tests.functional.shared import block_ops

    class Context:
        def get_block(self, x, y, z):
            if (x, y, z) in {(10, 69, 5), (9, 70, 5)}:
                return {"id": "minecraft:stone"}
            return {"id": "minecraft:air"}

        client = None

    assert block_ops.adjacent_support(Context(), 10, 70, 5) == (10, 69, 5)


def test_adjacent_support_ignores_liquids():
    """Water is replaceable, so the bridge does not count it as support."""
    from tests.functional.shared import block_ops

    class Context:
        def get_block(self, x, y, z):
            return {"id": "minecraft:water"}

        client = None

    assert block_ops.adjacent_support(Context(), 10, 70, 5) is None


def test_adjacent_support_returns_none_when_floating():
    from tests.functional.shared import block_ops

    class Context:
        def get_block(self, x, y, z):
            return {"id": "minecraft:air"}

        client = None

    assert block_ops.adjacent_support(Context(), 10, 70, 5) is None


def test_build_hollow_box_delegates_to_survival_placement(monkeypatch):
    """Must use block_ops (real placement), never the /fill world helpers."""
    from baritone_client.common import harness_ops

    calls = []
    monkeypatch.setattr(harness_ops, "_load",
                        lambda: {"bot_build_hollow_box": lambda *a, **k: calls.append(a) or True})
    monkeypatch.setattr(harness_ops, "make_ctx", lambda _c: "CTX")

    assert harness_ops.build_hollow_box(object(), 0, 64, 0, 6, 67, 6, "minecraft:oak_planks") is True
    assert calls and calls[0][1:] == (0, 64, 0, 6, 67, 6, "minecraft:oak_planks")


def test_deposit_across_chests_visits_every_chest(monkeypatch):
    """Single-chest deposit strands a bot once that chest fills."""
    from baritone_client.common import harness_ops

    seen = {}
    monkeypatch.setattr(harness_ops, "_load", lambda: {
        "deposit_inventory_to_supply_chest":
            lambda _ctx, positions, meta: seen.update(positions=positions) or True
    })
    monkeypatch.setattr(harness_ops, "make_ctx", lambda _c: "CTX")

    assert harness_ops.deposit_across_chests(object(), [(1, 2, 3), (4, 5, 6)]) is True
    assert seen["positions"] == [(1, 2, 3), (4, 5, 6)]


def test_deposit_across_chests_rejects_an_empty_list(monkeypatch):
    from baritone_client.common import harness_ops

    monkeypatch.setattr(harness_ops, "_load", lambda: {
        "deposit_inventory_to_supply_chest":
            lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("must not be called"))
    })
    assert harness_ops.deposit_across_chests(object(), []) is False


def test_no_cheat_helpers_are_exposed():
    """world.py's fill/tp/give_item must never reach the survival controller."""
    from baritone_client.common import harness_ops

    for banned in ("fill", "clear_box", "tp", "teleport", "give_item",
                   "set_block", "set_health_full", "kill_nearby_entities",
                   "build_flat_pad", "set_gamerules_for_test"):
        assert not hasattr(harness_ops, banned), f"cheat helper exposed: {banned}"


def test_crafting_space_sheds_bulk_that_is_not_cobblestone():
    """Deep underground the bulk is deepslate and tuff, not cobblestone.

    Live A1 2026-09-04 at (-442, -54, -601): 36 of 36 slots and no crafting
    table could be placed, because the only stack this path would sacrifice
    was cobblestone and there was none left. The run sat repeating
    "all methods failed for minecraft:crafting_table (have 0, wanted 1)".
    """
    from types import SimpleNamespace

    from tests.functional.shared import inventory_ops

    thrown = []
    events = []
    slots = [
        {"slot": 9, "id": "minecraft:cobbled_deepslate", "count": 64},
        {"slot": 10, "id": "minecraft:tuff", "count": 55},
        {"slot": 11, "id": "minecraft:diamond_pickaxe", "count": 1},
        {"slot": 12, "id": "minecraft:bread", "count": 16},
    ]
    screen = {"data": {"type": "PlayerScreenHandler", "slots": slots}}
    ctx = SimpleNamespace(
        log_event=events.append,
        client=SimpleNamespace(
            transport=SimpleNamespace(dispatch=lambda _r, _p=None: screen)
        ),
    )
    original = inventory_ops.safe_inventory_click
    try:
        inventory_ops.safe_inventory_click = (
            lambda _ctx, slot, _t, _b: thrown.append(slot) or True
        )
        assert inventory_ops.ensure_crafting_output_space(ctx, screen)
    finally:
        inventory_ops.safe_inventory_click = original

    # The largest bulk stack goes; the pickaxe and the food never do.
    assert thrown == [9], thrown
    assert not any("no approved low-value stack" in e for e in events), events


def test_crafting_space_never_sheds_tools_or_food():
    from types import SimpleNamespace

    from tests.functional.shared import inventory_ops

    thrown = []
    slots = [
        {"slot": 9, "id": "minecraft:diamond_pickaxe", "count": 1},
        {"slot": 10, "id": "minecraft:bread", "count": 16},
        {"slot": 11, "id": "minecraft:obsidian", "count": 9},
    ]
    screen = {"data": {"type": "PlayerScreenHandler", "slots": slots}}
    ctx = SimpleNamespace(
        log_event=lambda _e: None,
        client=SimpleNamespace(
            transport=SimpleNamespace(dispatch=lambda _r, _p=None: screen)
        ),
    )
    original = inventory_ops.safe_inventory_click
    try:
        inventory_ops.safe_inventory_click = (
            lambda _ctx, slot, _t, _b: thrown.append(slot) or True
        )
        assert not inventory_ops.ensure_crafting_output_space(ctx, screen)
    finally:
        inventory_ops.safe_inventory_click = original
    assert thrown == [], thrown
