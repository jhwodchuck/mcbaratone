from types import SimpleNamespace

import pytest

from baritone_client.common import inventory, resources


class RecordingTransport:
    def __init__(self, break_response=None):
        self.calls = []
        self.break_response = break_response or {"started": True}

    def dispatch(self, route, payload):
        self.calls.append((route, payload))
        if route == "break_block":
            return self.break_response
        return {}


def test_approach_discovered_log_stops_beside_it_then_breaks(monkeypatch):
    transport = RecordingTransport()
    client = SimpleNamespace(transport=transport)
    approaches = []
    monkeypatch.setattr(
        resources,
        "goto",
        lambda _client, x, y, z, **kwargs: approaches.append(
            (x, y, z, kwargs["tolerance"])
        )
        or True,
    )
    monkeypatch.setattr(resources, "select_item", lambda *_args, **_kwargs: True)

    assert resources._approach_and_break_log(client, (-71, 63, -114))
    assert approaches == [(-71, 63, -114, 3.0)]
    assert (
        "break_block",
        {"x": -71, "y": 63, "z": -114},
    ) in transport.calls


def test_discovered_log_is_not_broken_when_approach_fails(monkeypatch):
    transport = RecordingTransport()
    client = SimpleNamespace(transport=transport)
    monkeypatch.setattr(resources, "goto", lambda *_args, **_kwargs: False)

    assert not resources._approach_and_break_log(client, (-71, 63, -114))
    assert not any(route == "break_block" for route, _payload in transport.calls)


def test_safe_nearby_stone_avoids_block_directly_below_player():
    class StoneTransport(RecordingTransport):
        def dispatch(self, route, payload):
            self.calls.append((route, payload))
            if route == "get_state":
                return {"block_position": {"x": 10, "y": 62, "z": 20}}
            if route == "find_blocks":
                return {
                    "found": [
                        {"x": 10, "y": 61, "z": 20, "distance": 1.0},
                        {"x": 11, "y": 62, "z": 20, "distance": 1.0},
                    ]
                }
            return {}

    client = SimpleNamespace(transport=StoneTransport())

    assert resources._find_safe_nearby_stone(client) == (11, 62, 20)


def test_safe_nearby_ore_prefers_exposed_side_face():
    class OreTransport(RecordingTransport):
        def dispatch(self, route, payload):
            self.calls.append((route, payload))
            if route == "get_state":
                return {"block_position": {"x": -23, "y": 60, "z": -120}}
            if route == "find_blocks":
                return {
                    "found": [
                        {"x": -23, "y": 59, "z": -120, "distance": 1.0},
                        {"x": -24, "y": 60, "z": -121, "distance": 1.4},
                    ]
                }
            return {}

    client = SimpleNamespace(transport=OreTransport())

    assert resources._find_safe_nearby_ore(
        client, ["minecraft:coal_ore"]
    ) == (-24, 60, -121)


def test_safe_nearby_ore_skips_excluded_positions():
    class OreTransport(RecordingTransport):
        def dispatch(self, route, payload):
            self.calls.append((route, payload))
            if route == "get_state":
                return {"block_position": {"x": -23, "y": 60, "z": -120}}
            if route == "find_blocks":
                return {
                    "found": [
                        {"x": -24, "y": 60, "z": -121, "distance": 1.4},
                        {"x": -24, "y": 59, "z": -120, "distance": 1.0},
                    ]
                }
            return {}

    client = SimpleNamespace(transport=OreTransport())

    assert resources._find_safe_nearby_ore(
        client,
        ["minecraft:coal_ore"],
        excluded_positions={(-24, 60, -121)},
    ) == (-24, 59, -120)


def test_exact_stone_break_selects_pickaxe_and_verifies_air(monkeypatch):
    class StoneTransport(RecordingTransport):
        def __init__(self):
            super().__init__()
            self.block_reads = 0

        def dispatch(self, route, payload):
            self.calls.append((route, payload))
            if route == "get_block":
                self.block_reads += 1
                return {
                    "id": "minecraft:stone"
                    if self.block_reads == 1
                    else "minecraft:air"
                }
            return {"started": True}

    transport = StoneTransport()
    client = SimpleNamespace(transport=transport)
    approached = []
    selected = []
    monkeypatch.setattr(
        resources,
        "goto",
        lambda _client, x, y, z, **_kwargs: approached.append((x, y, z)) or True,
    )
    monkeypatch.setattr(
        resources,
        "select_item",
        lambda _client, item_id, **_kwargs: selected.append(item_id)
        or item_id == "minecraft:wooden_pickaxe",
    )
    monkeypatch.setattr(resources.time, "sleep", lambda _seconds: None)

    assert resources._approach_and_break_stone(client, (11, 62, 20))
    assert approached == [(11, 62, 20)]
    assert selected[-1] == "minecraft:wooden_pickaxe"
    assert (
        "break_block",
        {"x": 11, "y": 62, "z": 20},
    ) in transport.calls


def test_path_wait_observes_start_and_completion(monkeypatch):
    class PathTransport(RecordingTransport):
        def __init__(self):
            super().__init__()
            self.states = iter((False, True, True, False))

        def dispatch(self, route, payload):
            self.calls.append((route, payload))
            if route == "get_state":
                return {"is_pathing": next(self.states)}
            return {}

    client = SimpleNamespace(transport=PathTransport())
    clock = iter(value * 0.5 for value in range(20))
    monkeypatch.setattr(resources.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(resources.time, "sleep", lambda _seconds: None)

    assert resources._wait_for_path_completion(client, timeout=10.0)


def test_craft_with_table_repairs_locally_without_following_saved_waypoint(monkeypatch):
    from baritone_client.common import base

    client = SimpleNamespace(transport=RecordingTransport())
    opens = iter((False, True))
    counts = {
        "minecraft:crafting_table": 1,
        # A stone axe cannot be crafted from thin air; carry its recipe stone
        # so the stone-material gate (a later fix) passes as it would live.
        "minecraft:cobblestone": 3,
    }

    def fake_craft(_client, item_id, count):
        counts[item_id] = counts.get(item_id, 0) + count
        return True

    monkeypatch.setattr(
        resources,
        "count_item",
        lambda _client, item_id: counts.get(item_id, 0),
    )
    monkeypatch.setattr(resources, "ensure_tool_sticks", lambda *_args: True)
    monkeypatch.setattr(resources, "_wait_for_path_completion", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(resources, "craft", fake_craft)
    monkeypatch.setattr(base, "open_crafting_table", lambda *_args, **_kwargs: next(opens))
    placements = []
    monkeypatch.setattr(
        base,
        "place_crafting_table",
        lambda _client, x, y, z: placements.append((x, y, z)) or True,
    )
    monkeypatch.setattr(resources.time, "sleep", lambda _seconds: None)

    assert resources._craft_with_table(client, "minecraft:stone_axe", 1)
    assert placements == [(2, 0, 0)]
    assert (
        "chat",
        {"message": "#goto crafting_table"},
    ) not in client.transport.calls


def test_prepare_safe_furnace_fuel_consolidates_wood_via_raw_plank_helper(monkeypatch):
    client = SimpleNamespace(transport=RecordingTransport())
    counts = {"minecraft:oak_planks": 0}

    def fake_count(_client, item_id):
        return counts.get(item_id, 0)

    def fake_gather_wood(_client, count, timeout):
        # Existing behavior keeps the gather frontier bounded and daylight-safe.
        assert count == 2
        assert timeout == 300
        return True

    def fake_ensure_raw_planks(_client, required_planks):
        assert required_planks == 8
        counts["minecraft:oak_planks"] = 8
        return True

    monkeypatch.setattr(resources, "count_item", fake_count)
    monkeypatch.setattr(resources, "manage_inventory", lambda _client: None)
    monkeypatch.setattr(resources, "gather_wood", fake_gather_wood)
    monkeypatch.setattr(
        inventory,
        "_ensure_raw_planks",
        fake_ensure_raw_planks,
    )

    assert resources._prepare_safe_furnace_fuel(client, 11) == "minecraft:oak_planks"


def test_prepare_safe_furnace_fuel_fails_when_raw_plank_conversion_fails(monkeypatch):
    client = SimpleNamespace(transport=RecordingTransport())
    monkeypatch.setattr(resources, "count_item", lambda _client, _item: 0)
    monkeypatch.setattr(resources, "manage_inventory", lambda _client: None)
    monkeypatch.setattr(resources, "gather_wood", lambda *_a, **_k: True)
    monkeypatch.setattr(
        inventory,
        "_ensure_raw_planks",
        lambda *_a, **_k: False,
    )

    assert resources._prepare_safe_furnace_fuel(client, 11) is None


def test_craft_with_table_uses_raw_plank_helper_for_table_planks(monkeypatch):
    from baritone_client.common import base

    client = SimpleNamespace(transport=RecordingTransport())
    counts = {"minecraft:crafting_table": 0, "minecraft:oak_planks": 0}
    ensure_calls = []

    def fake_count(_client, item_id):
        return counts.get(item_id, 0)

    def fake_ensure_raw_planks(_client, required_planks):
        ensure_calls.append(required_planks)
        counts["minecraft:oak_planks"] = 8
        return True

    def fake_craft(_client, item_id, qty):
        counts[item_id] = counts.get(item_id, 0) + qty
        return True

    placements = []
    monkeypatch.setattr(resources, "count_item", fake_count)
    monkeypatch.setattr(resources, "ensure_tool_sticks", lambda *_a, **_k: True)
    monkeypatch.setattr(resources, "ensure_stone_material", lambda *_a, **_k: True)
    monkeypatch.setattr(base, "open_crafting_table", lambda *_a, **_k: False)
    monkeypatch.setattr(resources, "craft", fake_craft)
    monkeypatch.setattr(
        inventory,
        "_ensure_raw_planks",
        fake_ensure_raw_planks,
    )
    monkeypatch.setattr(resources.time, "sleep", lambda _s: None)
    monkeypatch.setattr(resources, "get_player_pos", lambda _c: (2, 0, 0))
    monkeypatch.setattr(resources, "_wait_for_path_completion", lambda *_a, **_k: True)
    monkeypatch.setattr(
        base,
        "place_crafting_table",
        lambda _client, x, y, z: placements.append((x, y, z)) or True,
    )

    assert resources._craft_with_table(client, "minecraft:flint_and_steel", 1)
    assert ensure_calls == [4]
    assert counts["minecraft:crafting_table"] == 1


def test_ore_gather_recounts_drops_when_pathing_stops(monkeypatch):
    class OreTransport(RecordingTransport):
        def dispatch(self, route, payload):
            self.calls.append((route, payload))
            if route == "get_state":
                return {"is_pathing": False}
            return {}

    client = SimpleNamespace(transport=OreTransport())
    recounts = iter((0, 0, 15))
    monkeypatch.setattr(
        resources,
        "count_item",
        lambda _client, item_id: (
            1
            if item_id == "minecraft:stone_pickaxe"
            else next(recounts)
            if item_id == "minecraft:raw_iron"
            else 0
        ),
    )
    monkeypatch.setattr(
        "baritone_client.common.combat.defend_or_flee", lambda _client: False
    )
    monkeypatch.setattr(
        resources,
        "remaining_pickaxe_durability",
        lambda *_args, **_kwargs: 100,
    )
    monkeypatch.setattr(resources.time, "sleep", lambda _seconds: None)

    assert resources.gather_ores(client, "iron", count=15, timeout=30)


def test_pickaxe_durability_uses_damage_from_raw_inventory():
    class InventoryTransport(RecordingTransport):
        def dispatch(self, route, payload):
            self.calls.append((route, payload))
            if route == "get_inventory":
                return {
                    "inventory": [
                        {
                            "id": "minecraft:iron_pickaxe",
                            "count": 1,
                            "damage": 188,
                        },
                        {
                            "id": "minecraft:stone_pickaxe",
                            "count": 1,
                            "damage": 31,
                        },
                    ],
                    "offhand": [],
                }
            return {}

    client = SimpleNamespace(transport=InventoryTransport())

    assert resources.remaining_pickaxe_durability(
        client, ["minecraft:iron_pickaxe"]
    ) == 62
    assert resources.remaining_pickaxe_durability(client) == 162


def test_replacing_mine_process_cancels_before_starting(monkeypatch):
    transport = RecordingTransport()
    client = SimpleNamespace(transport=transport)
    sleeps = []
    monkeypatch.setattr(resources.time, "sleep", sleeps.append)

    resources._start_mine_process(client, ["minecraft:iron_ore"], 12)

    assert transport.calls == [
        ("cancel", {}),
        ("mine", {"blocks": ["minecraft:iron_ore"], "quantity": 12}),
    ]
    assert sleeps == [0.75, 0.25]


def test_ore_gather_retry_bounded_to_three_safe_exact_targets(monkeypatch):
    class OreTransport(RecordingTransport):
        def __init__(self):
            super().__init__()
            self.states = iter([{"is_pathing": False, "food_level": 20, "block_position": {"x": 0, "y": 64, "z": 0}}] * 20)

        def dispatch(self, route, payload):
            self.calls.append((route, payload))
            if route == "get_state":
                return next(self.states)
            return {}

    transport = OreTransport()
    client = SimpleNamespace(transport=transport)
    sleeps = []

    attempts = []
    monkeypatch.setattr(
        resources,
        "count_item",
        lambda _client, item_id: 0,
    )
    monkeypatch.setattr(
        resources,
        "ensure_supplies",
        lambda *_args, **_kwargs: type("Result", (), {"success": True})(),
    )

    def staged_fallback_target(_client, block_types, radius, excluded_positions=None):
        attempts.append((radius, tuple(sorted(excluded_positions or ()))))
        if (5, 64, 2) in (excluded_positions or ()):
            return None
        return (5, 64, 2)

    monkeypatch.setattr(resources, "_find_safe_nearby_ore", staged_fallback_target)
    monkeypatch.setattr(
        resources,
        "_approach_and_break_stone",
        lambda _client, _target: False,
    )
    monkeypatch.setattr(resources.time, "sleep", sleeps.append)
    monkeypatch.setattr(
        "baritone_client.common.combat.defend_or_flee",
        lambda _client: False,
    )

    assert not resources.gather_ores(client, "iron", count=1, timeout=30)

    assert attempts == [
        (8, ()),
        (10, ((5, 64, 2),)),
        (12, ((5, 64, 2),)),
    ]
    assert [
        route
        for route, _ in transport.calls
        if route in {"cancel", "mine", "break_block"}
    ] == [
        "cancel",
        "mine",
        "cancel",
        "mine",
        "cancel",
        "mine",
        "cancel",
    ]
    assert 0.75 in sleeps
    assert 0.25 in sleeps


def test_smelting_strategy_translates_shortfall_to_absolute_target(monkeypatch):
    client = SimpleNamespace()
    calls = []
    monkeypatch.setattr(
        resources,
        "count_item",
        lambda _client, item_id: 5 if item_id == "minecraft:iron_ingot" else 0,
    )
    monkeypatch.setattr(
        resources,
        "_smelt_with_furnace",
        lambda _client, item_id, target: calls.append((item_id, target)) or True,
    )

    assert resources._smelt_requirement_shortfall(
        client, "minecraft:iron_ingot", 7
    )
    assert calls == [("minecraft:iron_ingot", 12)]


def test_smelting_fuel_prefers_carried_planks_without_coal(monkeypatch):
    counts = {"minecraft:birch_planks": 22}
    monkeypatch.setattr(
        resources,
        "count_item",
        lambda _client, item_id: counts.get(item_id, 0),
    )

    assert resources._select_furnace_fuel(object(), 32) == "minecraft:birch_planks"


def test_safe_smelting_fuel_gathers_wood_and_crafts_planks(monkeypatch):
    counts = {
        "minecraft:birch_log": 3,
        "minecraft:birch_planks": 0,
    }
    gathered = []
    crafted = []
    client = SimpleNamespace(
        transport=SimpleNamespace(dispatch=lambda *_args, **_kwargs: {})
    )
    monkeypatch.setattr(
        resources,
        "count_item",
        lambda _client, item_id: counts.get(item_id, 0),
    )
    monkeypatch.setattr(resources, "manage_inventory", lambda _client: None)

    def gather(_client, count, timeout=0):
        gathered.append((count, timeout))
        counts["minecraft:birch_log"] = count
        return True

    def craft(_client, item_id, count):
        crafted.append((item_id, count))
        counts["minecraft:birch_planks"] += count
        return True

    monkeypatch.setattr(resources, "gather_wood", gather)
    monkeypatch.setattr(resources, "craft", craft)

    assert resources._prepare_safe_furnace_fuel(client, 32) == "minecraft:birch_planks"
    assert gathered == [(6, 300)]
    assert crafted == [("minecraft:birch_planks", 22)]


def test_safe_smelting_consolidates_split_plank_families(monkeypatch):
    counts = {
        "minecraft:birch_planks": 6,
        "minecraft:jungle_planks": 8,
        "minecraft:jungle_log": 2,
    }
    client = SimpleNamespace(
        transport=SimpleNamespace(dispatch=lambda *_args, **_kwargs: {})
    )
    monkeypatch.setattr(
        resources, "count_item", lambda _client, item: counts.get(item, 0)
    )
    monkeypatch.setattr(resources, "manage_inventory", lambda _client: True)
    monkeypatch.setattr(resources, "gather_wood", lambda *_args, **_kwargs: True)

    def craft(_client, item_id, count):
        counts[item_id] = counts.get(item_id, 0) + count
        return True

    monkeypatch.setattr(resources, "craft", craft)

    assert resources._prepare_safe_furnace_fuel(client, 15) == (
        "minecraft:jungle_planks"
    )


def test_smelter_prepares_fuel_before_locating_furnace(monkeypatch):
    from baritone_client.common import base, harness_ops

    events = []
    smelted = {"done": False}

    class SmeltTransport:
        def dispatch(self, route, _payload):
            if route == "get_state":
                return {"block_position": {"x": 0, "y": 0, "z": 0}}
            if route == "get_block":
                return {"id": "minecraft:furnace"}
            return {}

    client = SimpleNamespace(transport=SmeltTransport())

    def item_count(_client, item_id):
        if item_id == "minecraft:iron_ingot":
            return 37 if smelted["done"] else 5
        if item_id == "minecraft:raw_iron":
            return 32
        if item_id == "minecraft:furnace":
            return 1
        return 0

    monkeypatch.setattr(resources, "count_item", item_count)
    monkeypatch.setattr(
        resources,
        "_prepare_safe_furnace_fuel",
        lambda *_args: events.append("fuel") or "minecraft:birch_planks",
    )
    monkeypatch.setattr(
        resources,
        "find_nearby_block",
        lambda *_args, **_kwargs: events.append("find") or (1, 0, 0),
    )
    monkeypatch.setattr(
        resources,
        "goto",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("must not goto the furnace block")
        ),
    )
    monkeypatch.setattr(
        base,
        "place_furnace",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("existing furnace should be reused")
        ),
    )
    monkeypatch.setattr(harness_ops, "available", lambda: True)

    def smelt(*_args, **_kwargs):
        events.append("smelt")
        smelted["done"] = True
        return True

    monkeypatch.setattr(harness_ops, "smelt_in_furnace", smelt)

    assert resources._smelt_with_furnace(client, "minecraft:iron_ingot", 37)
    assert events == ["fuel", "find", "smelt"]


def test_outdoor_gatherer_waits_at_night_boundary(monkeypatch):
    transport = RecordingTransport()
    client = SimpleNamespace(transport=transport)
    waited = []
    monkeypatch.setattr(
        "baritone_client.common.base.wait_for_safe_daylight",
        lambda _client: waited.append(True) or True,
    )

    assert resources._ensure_outdoor_daylight(client, {"world_time": 13000})
    assert waited == [True]
    assert ("cancel", {}) in transport.calls


def test_outdoor_gatherer_does_not_pause_during_day(monkeypatch):
    transport = RecordingTransport()
    client = SimpleNamespace(transport=transport)
    monkeypatch.setattr(
        "baritone_client.common.base.wait_for_safe_daylight",
        lambda _client: (_ for _ in ()).throw(AssertionError("daylight must not wait")),
    )

    assert resources._ensure_outdoor_daylight(client, {"world_time": 9000})
    assert not transport.calls


def test_wood_gather_checks_night_before_starting_mine_process(monkeypatch):
    class NightTransport(RecordingTransport):
        def dispatch(self, route, payload):
            self.calls.append((route, payload))
            if route == "get_state":
                return {"world_time": 18000}
            return {}

    transport = NightTransport()
    client = SimpleNamespace(transport=transport)
    monkeypatch.setattr(resources, "count_item", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(resources, "_ensure_outdoor_daylight", lambda *_args: False)

    assert not resources.gather_wood(client, count=6)
    assert not any(route == "mine" for route, _payload in transport.calls)


def test_bounded_wood_gather_stops_at_expedition_radius(monkeypatch):
    positions = iter((0, 200))

    class RadiusTransport(RecordingTransport):
        def dispatch(self, route, payload):
            self.calls.append((route, payload))
            if route == "get_state":
                return {
                    "world_time": 1000,
                    "health": 20,
                    "food_level": 20,
                    "block_position": {
                        "x": next(positions, 200),
                        "y": 64,
                        "z": 0,
                    },
                }
            return {}

    transport = RadiusTransport()
    client = SimpleNamespace(transport=transport)
    monkeypatch.setattr(resources, "count_item", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(resources, "_ensure_outdoor_daylight", lambda *_args: True)
    monkeypatch.setattr(resources, "_start_mine_process", lambda *_args: None)

    assert not resources.gather_wood(
        client,
        count=3,
        max_distance_from_origin=160.0,
    )
    assert ("cancel", {}) in transport.calls


def test_missing_mining_pickaxe_is_replaced(monkeypatch):
    transport = RecordingTransport()
    client = SimpleNamespace(transport=transport)
    monkeypatch.setattr(resources, "count_item", lambda *_args, **_kwargs: 0)
    replacements = []
    monkeypatch.setattr(
        resources,
        "ensure_supplies",
        lambda _client, requirements: replacements.append(requirements)
        or SimpleNamespace(success=True),
    )

    assert resources._ensure_mining_pickaxe(client)
    assert replacements == [{"minecraft:wooden_pickaxe": 1}]


def test_missing_mining_pickaxe_waits_for_daylight_before_replacement(monkeypatch):
    class NightTransport(RecordingTransport):
        def dispatch(self, route, payload):
            self.calls.append((route, payload))
            if route == "get_state":
                return {"world_time": 13000}
            return {}

    transport = NightTransport()
    client = SimpleNamespace(transport=transport)
    monkeypatch.setattr(resources, "count_item", lambda *_args, **_kwargs: 0)
    waited = []
    monkeypatch.setattr(
        "baritone_client.common.base.wait_for_safe_daylight",
        lambda _client: waited.append(True) or True,
    )
    replacements = []
    monkeypatch.setattr(
        resources,
        "ensure_supplies",
        lambda _client, requirements: replacements.append(requirements)
        or SimpleNamespace(success=True),
    )

    assert resources._ensure_mining_pickaxe(client)
    assert waited == [True]
    assert replacements == [{"minecraft:wooden_pickaxe": 1}]
    assert ("cancel", {}) in transport.calls


def test_missing_mining_pickaxe_does_not_craft_when_night_wait_fails(monkeypatch):
    class NightTransport(RecordingTransport):
        def dispatch(self, route, payload):
            self.calls.append((route, payload))
            if route == "get_state":
                return {"world_time": 18000}
            return {}

    client = SimpleNamespace(transport=NightTransport())
    monkeypatch.setattr(resources, "count_item", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(
        "baritone_client.common.base.wait_for_safe_daylight",
        lambda _client: False,
    )
    monkeypatch.setattr(
        resources,
        "ensure_supplies",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("replacement must not start without daylight")
        ),
    )

    assert not resources._ensure_mining_pickaxe(client)


def test_missing_mining_pickaxe_prefers_stone_when_cobble_is_available(monkeypatch):
    transport = RecordingTransport()
    client = SimpleNamespace(transport=transport)

    def inventory_count(_client, item_id):
        return 3 if item_id == "minecraft:cobblestone" else 0

    monkeypatch.setattr(resources, "count_item", inventory_count)
    replacements = []
    monkeypatch.setattr(
        resources,
        "ensure_supplies",
        lambda _client, requirements: replacements.append(requirements)
        or SimpleNamespace(success=True),
    )

    assert resources._ensure_mining_pickaxe(client)
    assert replacements == [{"minecraft:stone_pickaxe": 1}]


def test_resume_active_furnace_collects_interrupted_batch(monkeypatch):
    class FurnaceTransport:
        def __init__(self):
            self.screen_reads = 0
            self.clicks = []

        def dispatch(self, route, payload):
            if route == "get_screen":
                self.screen_reads += 1
                if self.screen_reads == 1:
                    contents = (
                        {"slot": 0, "id": "minecraft:raw_iron", "count": 1},
                        {"slot": 1, "id": "minecraft:birch_planks", "count": 1},
                        {"slot": 2, "id": "minecraft:air", "count": 0},
                    )
                elif self.screen_reads == 2:
                    contents = (
                        {"slot": 0, "id": "minecraft:air", "count": 0},
                        {"slot": 1, "id": "minecraft:air", "count": 0},
                        {"slot": 2, "id": "minecraft:iron_ingot", "count": 1},
                    )
                else:
                    contents = tuple(
                        {"slot": slot, "id": "minecraft:air", "count": 0}
                        for slot in range(3)
                    )
                return {"sync_id": 9, "total_slots": 39, "slots": list(contents)}
            if route == "inventory_click":
                self.clicks.append(payload)
            if route == "get_block":
                return {"id": "minecraft:furnace", "state": {"lit": "true"}}
            return {}

    transport = FurnaceTransport()
    client = SimpleNamespace(transport=transport)
    monkeypatch.setattr("baritone_client.common.harness_ops.available", lambda: True)
    monkeypatch.setattr(
        "baritone_client.common.harness_ops.open_container",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(resources.time, "sleep", lambda _seconds: None)

    assert resources.resume_active_furnace(
        client,
        (-7, 79, -121),
        "minecraft:raw_iron",
        "minecraft:iron_ingot",
    )
    assert transport.clicks == [
        {
            "slot": 2,
            "type": "QUICK_MOVE",
            "button": 0,
            "sync_id": 9,
        }
    ]


def test_stone_pickaxe_prep_gathers_missing_cobblestone(monkeypatch):
    client = SimpleNamespace(transport=RecordingTransport())
    counts = {"minecraft:cobblestone": 0, "minecraft:cobbled_deepslate": 0}
    gathered = []

    def fake_gather_stone(_client, count=16, **_kwargs):
        gathered.append(count)
        counts["minecraft:cobblestone"] = count
        return True

    monkeypatch.setattr(resources, "count_item", lambda _c, item: counts.get(item, 0))
    monkeypatch.setattr(resources, "gather_stone", fake_gather_stone)

    assert resources.ensure_stone_material(client, "minecraft:stone_pickaxe", 1)
    assert gathered == [3]


def test_stone_material_prep_is_noop_for_non_stone_recipes(monkeypatch):
    client = SimpleNamespace(transport=RecordingTransport())
    gathered = []
    monkeypatch.setattr(resources, "count_item", lambda _c, _item: 0)
    monkeypatch.setattr(
        resources, "gather_stone", lambda *_a, **_k: gathered.append(True) or True
    )

    assert resources.ensure_stone_material(client, "minecraft:iron_pickaxe", 1)
    assert gathered == []


def test_stone_material_prep_accepts_cobbled_deepslate(monkeypatch):
    client = SimpleNamespace(transport=RecordingTransport())
    counts = {"minecraft:cobblestone": 0, "minecraft:cobbled_deepslate": 3}
    gathered = []
    monkeypatch.setattr(resources, "count_item", lambda _c, item: counts.get(item, 0))
    monkeypatch.setattr(
        resources, "gather_stone", lambda *_a, **_k: gathered.append(True) or True
    )

    assert resources.ensure_stone_material(client, "minecraft:stone_pickaxe", 1)
    assert gathered == []


def test_stone_material_prep_fails_closed_when_mining_fails(monkeypatch):
    client = SimpleNamespace(transport=RecordingTransport())
    monkeypatch.setattr(resources, "count_item", lambda _c, _item: 0)
    monkeypatch.setattr(resources, "gather_stone", lambda *_a, **_k: False)

    assert not resources.ensure_stone_material(client, "minecraft:stone_pickaxe", 1)


def test_craft_with_table_stops_when_stone_material_unavailable(monkeypatch):
    # The live FOOD_AND_IRON blocker: stick prep succeeds, cobblestone is zero,
    # and the craft used to be attempted anyway (failing forever). The stone
    # preparation gate must fail closed BEFORE any table interaction.
    client = SimpleNamespace(transport=RecordingTransport())
    monkeypatch.setattr(resources, "count_item", lambda _c, _item: 0)
    monkeypatch.setattr(resources, "ensure_tool_sticks", lambda *_a, **_k: True)
    monkeypatch.setattr(resources, "ensure_stone_material", lambda *_a, **_k: False)
    crafted = []
    monkeypatch.setattr(
        resources, "craft", lambda *_a, **_k: crafted.append(True) or True
    )

    assert not resources._craft_with_table(client, "minecraft:stone_pickaxe", 1)
    assert crafted == []
    assert not any(route == "interact_block" for route, _p in client.transport.calls)


def test_every_manual_grid_recipe_has_an_ensure_supplies_strategy():
    # ensure_supplies skips items without a handler and burns its whole
    # timeout; every manually craftable progression recipe must be reachable
    # (end_game requires {"minecraft:ender_eye": 12} through this exact path).
    from baritone_client.common.inventory import _MANUAL_GRID_RECIPES

    missing = [
        item_id
        for item_id in _MANUAL_GRID_RECIPES
        if item_id not in resources.DEFAULT_REQUIREMENT_STRATEGIES
    ]
    assert missing == []


def test_gathering_capacity_guard_requests_three_free_slots(monkeypatch):
    requested = []
    monkeypatch.setattr(resources, "free_inventory_slots", lambda _client: 0)
    monkeypatch.setattr(
        resources,
        "manage_inventory",
        lambda _client, minimum_free_slots: requested.append(minimum_free_slots) or True,
    )

    assert resources._reserve_gathering_inventory(object())
    assert requested == [3]


def test_gathering_capacity_prefers_checkpointed_storage(monkeypatch):
    slots = {"free": 0}
    state = SimpleNamespace(custom_data={})
    client = SimpleNamespace(transport=RecordingTransport(), _automation_state=state)
    monkeypatch.setattr(
        resources, "free_inventory_slots", lambda _client: slots["free"]
    )
    monkeypatch.setattr(
        inventory,
        "resolve_storage_location",
        lambda _client, state=None, verify=True: (1, 65, 1),
    )

    def deposit(_client, _pos, state=None):
        slots["free"] = 5
        return 4

    monkeypatch.setattr(inventory, "deposit_excess_to_chest", deposit)
    monkeypatch.setattr(
        resources,
        "manage_inventory",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("durable storage should run before ground disposal")
        ),
    )

    assert resources._reserve_gathering_inventory(client)


def test_satisfied_stone_target_does_not_discard_inventory(monkeypatch):
    client = SimpleNamespace(transport=RecordingTransport())
    monkeypatch.setattr(
        resources,
        "count_item",
        lambda _client, item_id: 32 if item_id == "minecraft:cobblestone" else 0,
    )
    monkeypatch.setattr(
        resources,
        "_reserve_gathering_inventory",
        lambda _client: (_ for _ in ()).throw(
            AssertionError("already-satisfied gathering must not clean inventory")
        ),
    )

    assert resources.gather_stone(client, count=32)


def test_stone_gathering_does_not_resume_underwater_mine_after_surfacing(
    monkeypatch,
):
    transport = RecordingTransport()
    client = SimpleNamespace(transport=transport, _submersion_ticks=1)
    monkeypatch.setattr(resources, "count_item", lambda _c, _i: 0)
    monkeypatch.setattr(resources, "_reserve_gathering_inventory", lambda _c: True)
    monkeypatch.setattr(resources, "_ensure_mining_pickaxe", lambda _c: True)
    monkeypatch.setattr(resources, "free_inventory_slots", lambda _c: 10)

    def surface(_client):
        _client._submersion_ticks = 0
        return True

    monkeypatch.setattr(
        "baritone_client.common.combat.defend_or_flee",
        surface,
    )

    assert not resources.gather_stone(client, count=9, timeout=30)
    mining = [call for call in transport.calls if call[0] == "mine"]
    assert len(mining) == 1
    assert transport.calls[-1] == ("cancel", {})


def test_descend_to_stone_layer_tunnels_down_from_surface(monkeypatch):
    transport = RecordingTransport()
    client = SimpleNamespace(transport=transport)
    # Pre-descent read (Y=101), then progress reads that show the tunnel
    # staircasing down until it reaches the stone-mining depth.
    states = iter(
        [
            {"block_position": {"x": 3, "y": 101, "z": -11}},  # initial
            {"block_position": {"x": 3, "y": 92, "z": -11}},   # progress
            {"block_position": {"x": 2, "y": 78, "z": -10}},   # reached depth
        ]
    )
    monkeypatch.setattr(
        resources, "_read_state_optional", lambda *_a, **_k: next(states)
    )
    monkeypatch.setattr(resources, "_ensure_mining_pickaxe", lambda _c: True)
    monkeypatch.setattr(resources.time, "sleep", lambda _s: None)

    assert resources._descend_to_stone_layer(client)
    # A tunnel is issued toward target_y = max(50, 101-25) = 76 at the same X/Z.
    tunnels = [p for r, p in transport.calls if r == "tunnel"]
    assert tunnels == [{"x": 3, "y": 76, "z": -11, "radius": 2}]
    # The broad mine is cancelled before the descent tunnel is issued.
    assert ("cancel", {}) in transport.calls


def test_descend_to_stone_layer_is_noop_when_already_deep(monkeypatch):
    transport = RecordingTransport()
    client = SimpleNamespace(transport=transport)
    monkeypatch.setattr(
        resources,
        "_read_state_optional",
        lambda *_a, **_k: {"block_position": {"x": 0, "y": 52, "z": 0}},
    )

    # Already within a couple blocks of the floor -- descending exposes nothing new.
    assert resources._descend_to_stone_layer(client) is False
    assert [r for r, _ in transport.calls if r == "tunnel"] == []


def test_descend_to_stone_layer_fails_when_no_downward_progress(monkeypatch):
    transport = RecordingTransport()
    client = SimpleNamespace(transport=transport)
    # Stays at Y=100 forever -> tunnel never makes progress -> failure.
    monkeypatch.setattr(
        resources,
        "_read_state_optional",
        lambda *_a, **_k: {"block_position": {"x": 0, "y": 100, "z": 0}},
    )
    monkeypatch.setattr(resources, "_ensure_mining_pickaxe", lambda _c: True)
    monkeypatch.setattr(resources.time, "sleep", lambda _s: None)
    # Force the timeout to expire immediately after the first poll.
    clock = iter([0, 0, 999])
    monkeypatch.setattr(resources.time, "time", lambda: next(clock))

    assert resources._descend_to_stone_layer(client) is False


def test_stranded_gatherer_descends_before_relocating(monkeypatch):
    """The recovery ladder must try tunnelling down to stone before giving up
    or relocating to the (also-surface) base."""
    transport = RecordingTransport()
    client = SimpleNamespace(transport=transport)

    # Never accumulates cobble, so the loop keeps hitting the stall recovery.
    monkeypatch.setattr(resources, "count_item", lambda _c, _i: 0)
    monkeypatch.setattr(resources, "_reserve_gathering_inventory", lambda _c: True)
    monkeypatch.setattr(resources, "_ensure_mining_pickaxe", lambda _c: True)
    monkeypatch.setattr(resources, "free_inventory_slots", lambda _c: 10)
    monkeypatch.setattr(
        resources, "_read_state_optional", lambda *_a, **_k: {"is_pathing": False}
    )
    monkeypatch.setattr(resources, "_find_safe_nearby_stone", lambda _c, **_k: None)
    monkeypatch.setattr(
        "baritone_client.common.combat.defend_or_flee", lambda _c: False
    )
    monkeypatch.setattr(resources.time, "sleep", lambda _s: None)

    order = []
    monkeypatch.setattr(
        resources,
        "_descend_to_stone_layer",
        lambda _c, **_k: order.append("descend") or False,
    )
    monkeypatch.setattr(
        resources,
        "_relocate_to_checkpointed_stone_source",
        lambda _c, **_k: order.append("relocate") or False,
    )

    assert not resources.gather_stone(client, count=9, timeout=5)
    # Descent is attempted, and strictly before relocation.
    assert order[0] == "descend"
    assert order.index("descend") < order.index("relocate")


def test_high_altitude_stone_fallback_returns_to_checkpointed_storage(monkeypatch):
    class Transport:
        def __init__(self):
            self.calls = []

        def dispatch(self, route, payload):
            self.calls.append((route, payload))
            if route == "get_state":
                return {"block_position": {"x": -16, "y": 113, "z": -19}}
            return {}

    transport = Transport()
    automation_state = SimpleNamespace(custom_data={})
    client = SimpleNamespace(
        transport=transport,
        _automation_state=automation_state,
    )
    destinations = []
    monkeypatch.setattr(
        inventory,
        "resolve_storage_location",
        lambda *_args, **_kwargs: (-78, 64, 35),
    )
    monkeypatch.setattr(
        resources,
        "goto",
        lambda _client, x, y, z, **kwargs: destinations.append((x, y, z, kwargs))
        or True,
    )

    assert resources._relocate_to_checkpointed_stone_source(client)
    assert destinations == [
        (-78, 64, 35, {"timeout": 240, "tolerance": 3.0})
    ]


def test_choose_descent_offset_picks_first_open_cardinal_direction():
    class ProbeTransport(RecordingTransport):
        def dispatch(self, route, payload):
            self.calls.append((route, payload))
            if route == "get_block":
                # (+1,0) is solid; (-1,0) is open -> must pick (-1, 0).
                if payload == {"x": 3 + 3, "y": 101, "z": -11}:
                    return {"id": "minecraft:stone"}
                if payload == {"x": 3 - 3, "y": 101, "z": -11}:
                    return {"id": "minecraft:air"}
                return {"id": "minecraft:stone"}
            return {}

    client = SimpleNamespace(transport=ProbeTransport())

    assert resources._choose_descent_offset(client, 3, 101, -11) == (-1, 0)


def test_choose_descent_offset_none_when_fully_enclosed():
    client = SimpleNamespace(transport=RecordingTransport())  # get_block -> {}

    assert resources._choose_descent_offset(client, 3, 101, -11) is None


def test_descend_to_stone_layer_aims_laterally_off_a_narrow_pillar(monkeypatch):
    transport = RecordingTransport()
    client = SimpleNamespace(transport=transport)
    states = iter(
        [
            {"block_position": {"x": 3, "y": 101, "z": -11}},
            {"block_position": {"x": 3, "y": 92, "z": -11}},
            {"block_position": {"x": -1, "y": 76, "z": -11}},
        ]
    )
    monkeypatch.setattr(
        resources, "_read_state_optional", lambda *_a, **_k: next(states)
    )
    monkeypatch.setattr(resources, "_ensure_mining_pickaxe", lambda _c: True)
    monkeypatch.setattr(resources.time, "sleep", lambda _s: None)
    # Open to the west (-1, 0); everything else solid.
    monkeypatch.setattr(
        resources,
        "_choose_descent_offset",
        lambda _c, _px, _py, _pz, **_k: (-1, 0),
    )

    assert resources._descend_to_stone_layer(client)
    tunnels = [p for r, p in transport.calls if r == "tunnel"]
    # target_x = 3 + (-1)*8 = -5, same target_y=76, same z.
    assert tunnels == [{"x": -5, "y": 76, "z": -11, "radius": 2}]


def test_manual_column_descend_breaks_one_block_at_a_time_when_supported(monkeypatch):
    """A genuinely solid column (support always present) descends step by
    step using the ``dig_block`` command (bridge 1.0.27+), NOT ``break_block``
    (Baritone builder process, same underfoot refusal) nor ``attack_block``
    (single swing, no progress) -- both verified live to break nothing."""
    transport = RecordingTransport()
    client = SimpleNamespace(transport=transport)
    positions = iter([101, 100, 99, 98])  # settles one lower after each break

    # The bridge's dig_block breaks the block over the following ticks; model
    # that as "air after the dig_block for that cell has been issued".
    dug = set()

    def dispatch(route, payload):
        transport.calls.append((route, payload))
        if route == "get_block":
            key = (payload["x"], payload["y"], payload["z"])
            if key in dug:
                return {"id": "minecraft:air"}
            return {"id": "minecraft:cobblestone" if payload["y"] <= 100 else "minecraft:air"}
        if route == "get_state":
            return {"block_position": {"x": 3, "y": next(positions), "z": -11}}
        if route == "dig_block":
            dug.add((payload["x"], payload["y"], payload["z"]))
            return {"started": True}
        return {}

    transport.dispatch = dispatch
    monkeypatch.setattr(resources, "select_item", lambda *_a, **_k: True)
    monkeypatch.setattr(resources, "_ensure_mining_pickaxe", lambda _c: True)
    monkeypatch.setattr(resources.time, "sleep", lambda _s: None)

    assert resources._manual_column_descend(client, target_y=98, max_steps=5)
    assert any(route == "dig_block" for route, _ in transport.calls)
    assert not any(route == "break_block" for route, _ in transport.calls)
    assert not any(route == "attack_block" for route, _ in transport.calls)


def test_manual_column_descend_stops_before_an_uncontrolled_fall(monkeypatch):
    """If the block two below is open, breaking would exceed a 1-block drop
    -- must stop rather than risk it."""
    transport = RecordingTransport()
    client = SimpleNamespace(transport=transport)

    def dispatch(route, payload):
        transport.calls.append((route, payload))
        if route == "get_state":
            return {"block_position": {"x": 3, "y": 101, "z": -11}}
        if route == "get_block":
            # Y=100 (feet-1) is solid; Y=99 (feet-2, the support) is open air.
            return {"id": "minecraft:cobblestone" if payload["y"] == 100 else "minecraft:air"}
        return {}

    transport.dispatch = dispatch
    monkeypatch.setattr(resources, "_ensure_mining_pickaxe", lambda _c: True)

    assert resources._manual_column_descend(client, target_y=70) is False
    assert not any(route == "break_block" for route, _ in transport.calls)


def test_manual_column_descend_refuses_lava(monkeypatch):
    transport = RecordingTransport()
    client = SimpleNamespace(transport=transport)

    def dispatch(route, payload):
        transport.calls.append((route, payload))
        if route == "get_state":
            return {"block_position": {"x": 3, "y": 101, "z": -11}}
        if route == "get_block":
            return {"id": "minecraft:lava"}
        return {}

    transport.dispatch = dispatch
    monkeypatch.setattr(resources, "_ensure_mining_pickaxe", lambda _c: True)

    assert resources._manual_column_descend(client, target_y=70) is False
    assert not any(route == "break_block" for route, _ in transport.calls)


def test_manual_column_descend_allows_a_safe_short_fall_over_a_small_gap(monkeypatch):
    """A pillar sitting over a <=3-block air gap must still descend: the drop
    is damage-free, so refusing it would needlessly strand the bot."""
    transport = RecordingTransport()
    client = SimpleNamespace(transport=transport)
    # Feet at 101; Y=100 solid (feet-1), Y=99/98 air gap, Y=97 solid landing
    # (a 3-block fall -> no damage). After the break the player lands at 98.
    positions = iter([101, 98])
    dug = set()

    def dispatch(route, payload):
        transport.calls.append((route, payload))
        if route == "get_block":
            y = payload["y"]
            key = (payload["x"], y, payload["z"])
            if key in dug:
                return {"id": "minecraft:air"}
            if y in (100, 97):
                return {"id": "minecraft:cobblestone"}
            return {"id": "minecraft:air"}  # 99, 98 gap
        if route == "get_state":
            return {"block_position": {"x": 3, "y": next(positions), "z": -11}}
        if route == "dig_block":
            dug.add((payload["x"], payload["y"], payload["z"]))
            return {"started": True}
        return {}

    transport.dispatch = dispatch
    monkeypatch.setattr(resources, "select_item", lambda *_a, **_k: True)
    monkeypatch.setattr(resources, "_ensure_mining_pickaxe", lambda _c: True)
    monkeypatch.setattr(resources.time, "sleep", lambda _s: None)

    assert resources._manual_column_descend(client, target_y=97, max_steps=3)
    assert any(route == "dig_block" for route, _ in transport.calls)


def test_manual_column_descend_refuses_a_long_fall(monkeypatch):
    """A drop with no solid landing within the safe-fall window must stop."""
    transport = RecordingTransport()
    client = SimpleNamespace(transport=transport)

    def dispatch(route, payload):
        transport.calls.append((route, payload))
        if route == "get_state":
            return {"block_position": {"x": 3, "y": 101, "z": -11}}
        if route == "get_block":
            # Only feet-1 solid; a deep void below (no landing within 3).
            return {"id": "minecraft:cobblestone" if payload["y"] == 100 else "minecraft:air"}
        return {}

    transport.dispatch = dispatch
    monkeypatch.setattr(resources, "_ensure_mining_pickaxe", lambda _c: True)

    assert resources._manual_column_descend(client, target_y=70) is False
    assert not any(route == "dig_block" for route, _ in transport.calls)


def test_ensure_supplies_raises_on_death_instead_of_looping_forever(monkeypatch):
    """Regression: ensure_supplies had no death/health check at all, so a
    bootstrap loop (e.g. _ensure_mining_pickaxe -> ensure_supplies({"minecraft
    :wooden_pickaxe": 1})) kept retrying a gather_wood/craft chain that
    internally refuses on low health, with nothing in THIS loop ever noticing
    the player was dead. Confirmed live: Bot10 looped "Ensuring supplies" for
    its full 600s while get_state().is_dead was already True."""
    transport = RecordingTransport()
    client = SimpleNamespace(transport=transport)

    def dispatch(route, payload):
        transport.calls.append((route, payload))
        if route == "get_state":
            return {"health": 0.0, "food_level": 14, "is_dead": True}
        return {}

    transport.dispatch = dispatch
    monkeypatch.setattr(resources, "count_item", lambda _c, _item: 0)

    with pytest.raises(resources.PlayerDeathDetected):
        resources.ensure_supplies(client, {"minecraft:wooden_pickaxe": 1})
    assert ("cancel", {}) in transport.calls


def test_ensure_supplies_recovers_health_before_gathering(monkeypatch):
    transport = RecordingTransport()
    client = SimpleNamespace(transport=transport)

    def dispatch(route, payload):
        transport.calls.append((route, payload))
        if route == "get_state":
            return {"health": 8.0, "food_level": 14, "is_dead": False}
        return {}

    transport.dispatch = dispatch
    # Never satisfied: keeps the loop iterating so we can observe the
    # recovery call without needing to simulate a full successful craft.
    monkeypatch.setattr(resources, "count_item", lambda _c, _item: 0)
    monkeypatch.setattr(resources.time, "sleep", lambda _s: None)
    recovered = []

    def fake_recover_health(*_a, **_k):
        recovered.append(True)
        # False -> ensure_supplies itself raises SurvivalRecoveryRequired,
        # which stops the test loop deterministically via the real code path.
        return False

    monkeypatch.setattr(
        "baritone_client.common.combat.recover_health", fake_recover_health
    )

    with pytest.raises(resources.SurvivalRecoveryRequired):
        resources.ensure_supplies(client, {"minecraft:stick": 1})

    assert recovered == [True]


def test_ensure_supplies_yields_when_health_cannot_recover(monkeypatch):
    transport = RecordingTransport()
    client = SimpleNamespace(transport=transport)

    def dispatch(route, payload):
        transport.calls.append((route, payload))
        if route == "get_state":
            return {"health": 6.0, "food_level": 14, "is_dead": False}
        return {}

    transport.dispatch = dispatch
    monkeypatch.setattr(resources, "count_item", lambda _c, _item: 0)
    monkeypatch.setattr(
        "baritone_client.common.combat.recover_health", lambda *_a, **_k: False
    )

    with pytest.raises(resources.SurvivalRecoveryRequired):
        resources.ensure_supplies(client, {"minecraft:wooden_pickaxe": 1})
