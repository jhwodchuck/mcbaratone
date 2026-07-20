from types import SimpleNamespace

from baritone_client.automator.phases import iron_age
from baritone_client.common import resources


def test_iron_phase_stabilizes_low_hunger_before_mining(monkeypatch):
    class Transport:
        def dispatch(self, route, payload):
            if route == "get_state":
                return {"food_level": 6}
            return {}

    client = SimpleNamespace(transport=Transport())
    calls = []
    monkeypatch.setattr(
        iron_age,
        "eat_until_hunger",
        lambda _client, minimum_food: calls.append(minimum_food) or True,
    )

    assert iron_age.FoodAndIronHandler()._stabilize_hunger(client)
    assert calls == [14]


def test_iron_phase_resume_counts_existing_ingots_before_mining(monkeypatch):
    client = SimpleNamespace()
    gathered = []
    counts = {
        "minecraft:iron_ingot": 17,
        "minecraft:raw_iron": 0,
    }
    monkeypatch.setattr(iron_age, "count_item", lambda _client, item: counts.get(item, 0))
    monkeypatch.setattr(
        iron_age,
        "gather_ores",
        lambda *_args, **_kwargs: gathered.append(True) or True,
    )

    handler = iron_age.FoodAndIronHandler()
    assert handler._mine_initial_iron(client)
    assert handler._smelt_iron(client)
    assert not gathered


def test_iron_phase_recovers_critical_health_before_mining(monkeypatch):
    class Transport:
        def dispatch(self, route, _payload):
            if route == "get_state":
                return {"health": 6.0, "food_level": 16}
            return {}

    client = SimpleNamespace(transport=Transport())
    calls = []
    monkeypatch.setattr(
        iron_age,
        "recover_health",
        lambda *_args, **_kwargs: calls.append("recover") or False,
    )
    monkeypatch.setattr(
        iron_age,
        "acquire_emergency_food",
        lambda *_args, **_kwargs: calls.append("acquire") or True,
    )

    assert iron_age.FoodAndIronHandler()._stabilize_hunger(client)
    assert calls == ["recover", "acquire"]


def test_y_descent_uses_small_goals_and_disables_fatal_fall_settings(monkeypatch):
    class Transport:
        def __init__(self):
            self.calls = []
            self.states = iter((
                {
                    "block_position": {"x": 10, "y": 30, "z": 5},
                    "health": 20,
                    "food_level": 20,
                    "velocity": {"y": 0},
                },
                {
                    "block_position": {"x": 11, "y": 29, "z": 5},
                    "health": 20,
                    "food_level": 20,
                    "velocity": {"y": 0},
                    "is_pathing": False,
                },
                {
                    "block_position": {"x": 11, "y": 29, "z": 5},
                    "health": 20,
                    "food_level": 20,
                    "velocity": {"y": 0},
                },
            ))

        def dispatch(self, route, payload):
            self.calls.append((route, payload))
            if route == "get_state":
                return next(self.states)
            if route == "get_block":
                if payload["y"] == 28:
                    return {"id": "minecraft:stone"}
                return {"id": "minecraft:air"}
            return {}

    transport = Transport()
    client = SimpleNamespace(transport=transport)
    monkeypatch.setattr(resources.time, "sleep", lambda _seconds: None)

    assert resources.go_to_y_level(client, 26, timeout=10)
    messages = [payload["message"] for route, payload in transport.calls if route == "chat"]
    assert "#set allowParkour false" in messages
    assert "#set allowDownward false" in messages
    assert "#set maxFallHeightNoWater 3" in messages
    goto_payloads = [payload for route, payload in transport.calls if route == "goto"]
    assert goto_payloads[0] == {"x": 11, "y": 29, "z": 5}


def test_y_descent_cancels_each_exact_break_before_starting_the_next(monkeypatch):
    class Transport:
        def __init__(self):
            self.calls = []
            self.broken = set()
            self.states = iter(
                (
                    {
                        "block_position": {"x": 10, "y": 30, "z": 5},
                        "health": 20,
                        "food_level": 20,
                    },
                    {
                        "block_position": {"x": 11, "y": 29, "z": 5},
                        "health": 20,
                        "food_level": 20,
                    },
                    {
                        "block_position": {"x": 11, "y": 29, "z": 5},
                        "health": 20,
                        "food_level": 20,
                    },
                )
            )

        def dispatch(self, route, payload):
            self.calls.append((route, payload))
            if route == "get_state":
                return next(self.states)
            if route == "get_block":
                position = (payload["x"], payload["y"], payload["z"])
                if payload["y"] == 28:
                    return {"id": "minecraft:stone"}
                return {
                    "id": "minecraft:air"
                    if position in self.broken
                    else "minecraft:stone"
                }
            if route == "break_block":
                self.broken.add((payload["x"], payload["y"], payload["z"]))
            return {}

    transport = Transport()
    client = SimpleNamespace(transport=transport)
    monkeypatch.setattr(resources, "count_item", lambda *_args, **_kwargs: 1)
    monkeypatch.setattr(resources.time, "sleep", lambda _seconds: None)

    assert resources.go_to_y_level(client, 26, timeout=10)
    control_routes = [
        route
        for route, _payload in transport.calls
        if route in {"break_block", "goto", "cancel"}
    ]
    assert control_routes[:6] == [
        "break_block",
        "cancel",
        "break_block",
        "cancel",
        "goto",
        "cancel",
    ]


def test_iron_phase_resume_accepts_already_crafted_essentials(monkeypatch):
    client = SimpleNamespace()
    gathered = []
    counts = {
        "minecraft:iron_pickaxe": 1,
        "minecraft:bucket": 1,
        "minecraft:iron_ingot": 11,
        "minecraft:raw_iron": 0,
    }
    monkeypatch.setattr(iron_age, "count_item", lambda _client, item: counts.get(item, 0))
    monkeypatch.setattr(
        iron_age,
        "gather_ores",
        lambda *_args, **kwargs: gathered.append(
            (_args[1], kwargs.get("count", 0))
        ) or True,
    )

    handler = iron_age.FoodAndIronHandler()
    assert handler._mine_initial_iron(client)
    assert handler._smelt_iron(client)
    assert gathered == [("iron", 4)]


def test_mine_initial_iron_withdraws_owned_supplies_before_mining(monkeypatch):
    client = SimpleNamespace()
    gathered = []
    captured_withdrawals = []
    counts = {
        "minecraft:iron_pickaxe": 1,
        "minecraft:bucket": 1,
        "minecraft:iron_ingot": 0,
        "minecraft:raw_iron": 0,
    }

    def count_item(_client, item_id):
        return counts.get(item_id, 0)

    def withdraw_required(_client, chest_pos, requirements):
        captured_withdrawals.append((tuple(chest_pos), dict(requirements)))
        if "minecraft:raw_iron" in requirements:
            need = max(0, 15 - (counts["minecraft:iron_ingot"] + counts["minecraft:raw_iron"]))
            counts["minecraft:raw_iron"] = min(64, counts["minecraft:raw_iron"] + min(requirements["minecraft:raw_iron"], need))
        if "minecraft:iron_ingot" in requirements:
            need = max(0, 15 - (counts["minecraft:iron_ingot"] + counts["minecraft:raw_iron"]))
            counts["minecraft:iron_ingot"] = min(64, counts["minecraft:iron_ingot"] + min(requirements["minecraft:iron_ingot"], need))
        if "minecraft:bucket" in requirements:
            counts["minecraft:bucket"] = max(counts["minecraft:bucket"], requirements["minecraft:bucket"])
        return 1

    handler = iron_age.FoodAndIronHandler()
    handler.state = SimpleNamespace(
        custom_data={"structures": {"starter_house": {"supply_chest": (-524, 66, 665)}}}
    )
    monkeypatch.setattr(iron_age, "count_item", count_item)
    monkeypatch.setattr(iron_age, "withdraw_required_from_chest", withdraw_required)
    monkeypatch.setattr(
        iron_age,
        "gather_ores",
        lambda *_args, **_kwargs: gathered.append(True) or True,
    )

    assert handler._mine_initial_iron(client)
    assert not gathered
    assert captured_withdrawals == [
        ((-524, 66, 665), {"minecraft:raw_iron": 15}),
    ]


def test_mine_initial_iron_uses_checkpointed_chest_not_arbitrary_nearby_locations(monkeypatch):
    client = SimpleNamespace()
    captured_withdrawals = []
    counts = {
        "minecraft:iron_pickaxe": 1,
        "minecraft:bucket": 1,
        "minecraft:iron_ingot": 14,
        "minecraft:raw_iron": 0,
    }

    def count_item(_client, item_id):
        return counts.get(item_id, 0)

    def withdraw_required(_client, chest_pos, requirements):
        captured_withdrawals.append((tuple(chest_pos), dict(requirements)))
        for item_id, needed in requirements.items():
            counts[item_id] = max(
                count_item(_client, item_id), counts.get(item_id, 0) + needed
            )
        return 1

    handler = iron_age.FoodAndIronHandler()
    handler.state = SimpleNamespace(
        custom_data={"structures": {"starter_house": {"supply_chest": (-523, 66, 665)}}}
    )
    monkeypatch.setattr(
        iron_age,
        "resolve_storage_location",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("arbitrary storage location fallback must not be used")
        ),
    )
    monkeypatch.setattr(iron_age, "count_item", count_item)
    monkeypatch.setattr(iron_age, "withdraw_required_from_chest", withdraw_required)
    monkeypatch.setattr(
        resources,
        "gather_ores",
        lambda *_args, **_kwargs: False,
    )

    assert handler._mine_initial_iron(client)
    assert captured_withdrawals == [((-523, 66, 665), {"minecraft:raw_iron": 1})]


def test_mine_initial_iron_shortfall_transitions_once_and_retries(monkeypatch):
    transport = SimpleNamespace(dispatch=lambda route, payload: {})
    client = SimpleNamespace(transport=transport)
    gather_attempts = []
    descent_calls = []
    counts = {
        "minecraft:iron_pickaxe": 1,
        "minecraft:bucket": 1,
        "minecraft:iron_ingot": 0,
        "minecraft:raw_iron": 0,
    }

    monkeypatch.setattr(
        iron_age,
        "count_item",
        lambda _client, item_id: counts.get(item_id, 0),
    )
    monkeypatch.setattr(
        iron_age,
        "gather_ores",
        lambda *_args, **_kwargs: (gather_attempts.append(1) or len(gather_attempts) >= 2),
    )
    monkeypatch.setattr(
        iron_age,
        "go_to_y_level",
        lambda *_args, **_kwargs: descent_calls.append(_args[1]) or True,
    )

    assert iron_age.FoodAndIronHandler()._mine_initial_iron(client)
    assert gather_attempts == [1, 1]
    assert descent_calls == [-58]


def test_mine_initial_iron_transition_failure_returns_false_without_infinite_loop(monkeypatch):
    transport = SimpleNamespace(dispatch=lambda route, payload: {})
    client = SimpleNamespace(transport=transport)
    gather_attempts = []
    descent_calls = []
    counts = {
        "minecraft:iron_pickaxe": 1,
        "minecraft:bucket": 1,
        "minecraft:iron_ingot": 0,
        "minecraft:raw_iron": 0,
    }

    monkeypatch.setattr(
        iron_age,
        "count_item",
        lambda _client, item_id: counts.get(item_id, 0),
    )
    monkeypatch.setattr(
        iron_age,
        "gather_ores",
        lambda *_args, **_kwargs: gather_attempts.append(1) or False,
    )
    monkeypatch.setattr(
        iron_age,
        "go_to_y_level",
        lambda *_args, **_kwargs: descent_calls.append(_args[1]) or True,
    )

    assert not iron_age.FoodAndIronHandler()._mine_initial_iron(client)
    assert len(gather_attempts) == 2
    assert descent_calls == [-58]


def test_ensure_supplies_never_loots_owned_surface_storage(monkeypatch):
    class Transport:
        def dispatch(self, route, _payload):
            if route == "get_state":
                return {"block_position": {"x": 0, "y": 79, "z": 0}}
            return {}

    client = SimpleNamespace(transport=Transport())
    looted = []
    monkeypatch.setattr(resources, "count_item", lambda *_args: 1)
    monkeypatch.setattr(
        "baritone_client.common.base.loot_nearby_chests",
        lambda *_args, **_kwargs: looted.append(True),
    )

    result = resources.ensure_supplies(client, {"minecraft:iron_pickaxe": 1})
    assert result.success
    assert not looted


def test_deep_mining_prep_replaces_nearly_broken_pickaxes(monkeypatch):
    client = SimpleNamespace()
    crafted = []
    durability = iter((40, 290, 540))

    def item_count(_client, item_id):
        if item_id == "minecraft:bucket":
            return 1
        if item_id == "minecraft:iron_pickaxe":
            return len(crafted) + 1
        return 0

    handler = iron_age.FoodAndIronHandler()
    monkeypatch.setattr(iron_age, "count_item", item_count)
    monkeypatch.setattr(
        iron_age,
        "remaining_pickaxe_durability",
        lambda *_args, **_kwargs: next(durability),
    )
    monkeypatch.setattr(
        handler,
        "_ensure_mining_workstation",
        lambda _client: True,
    )
    monkeypatch.setattr(
        iron_age,
        "_craft_with_table",
        lambda _client, item_id, target: crafted.append((item_id, target)) or True,
    )

    assert handler._craft_essential_iron(client)
    assert crafted == [
        ("minecraft:iron_pickaxe", 2),
        ("minecraft:iron_pickaxe", 3),
    ]


def test_craft_essential_iron_reuses_stone_pickaxe_durability_for_reconnect(monkeypatch):
    client = SimpleNamespace()
    durability = iter((180, 370))

    item_counts = {
        "minecraft:stone_pickaxe": 2,
        "minecraft:iron_pickaxe": 1,
        "minecraft:diamond_pickaxe": 0,
        "minecraft:netherite_pickaxe": 0,
        "minecraft:bucket": 1,
    }

    handler = iron_age.FoodAndIronHandler()
    monkeypatch.setattr(
        iron_age,
        "count_item",
        lambda _client, item_id: item_counts.get(item_id, 0),
    )
    monkeypatch.setattr(
        iron_age,
        "remaining_pickaxe_durability",
        lambda *_args, **_kwargs: next(durability),
    )
    monkeypatch.setattr(
        handler,
        "_ensure_mining_workstation",
        lambda _client: True,
    )

    crafted = []
    monkeypatch.setattr(
        iron_age,
        "_craft_with_table",
        lambda _client, item_id, target: crafted.append((item_id, target)) or True,
    )

    assert handler._craft_essential_iron(client)
    assert crafted == [("minecraft:iron_pickaxe", 4)]


def test_stone_only_pickaxes_do_not_pass_essential_iron_check(monkeypatch):
    client = SimpleNamespace()
    item_counts = {
        "minecraft:stone_pickaxe": 4,
        "minecraft:iron_pickaxe": 0,
        "minecraft:diamond_pickaxe": 0,
        "minecraft:netherite_pickaxe": 0,
        "minecraft:bucket": 1,
    }

    handler = iron_age.FoodAndIronHandler()
    monkeypatch.setattr(
        iron_age,
        "count_item",
        lambda _client, item_id: item_counts.get(item_id, 0),
    )
    monkeypatch.setattr(iron_age, "remaining_pickaxe_durability", lambda *_args, **_kwargs: 500)
    monkeypatch.setattr(
        handler,
        "_ensure_mining_workstation",
        lambda _client: True,
    )
    crafted = []
    monkeypatch.setattr(
        iron_age,
        "_craft_with_table",
        lambda _client, item_id, target: crafted.append((item_id, target)) or True,
    )

    assert not handler._craft_essential_iron(client)
    assert crafted and crafted[0][0] == "minecraft:iron_pickaxe"


def test_bulk_mining_stops_after_first_failed_resource(monkeypatch):
    class Transport:
        def __init__(self):
            self.calls = []

        def dispatch(self, route, payload):
            self.calls.append((route, payload))
            return {}

    client = SimpleNamespace(transport=Transport())
    gathered = []
    monkeypatch.setattr(
        iron_age,
        "gather_ores",
        lambda _client, ore_type, **_kwargs: gathered.append(ore_type) or False,
    )

    assert not iron_age.FoodAndIronHandler()._bulk_mine(client)
    assert gathered == ["diamond"]
    assert client.transport.calls[-1] == ("cancel", {})


def test_bulk_mining_prioritizes_diamond_then_collects_smelting_inputs(monkeypatch):
    client = SimpleNamespace(transport=SimpleNamespace(dispatch=lambda *_args: {}))
    gathered = []
    monkeypatch.setattr(
        iron_age,
        "gather_ores",
        lambda _client, ore_type, **kwargs: gathered.append(
            (ore_type, kwargs["count"])
        )
        or True,
    )

    assert iron_age.FoodAndIronHandler()._bulk_mine(client)
    assert gathered == [("diamond", 5), ("iron", 30)]


def test_bulk_mining_credits_finished_ingots_and_never_searches_for_coal(monkeypatch):
    client = SimpleNamespace(transport=SimpleNamespace(dispatch=lambda *_args: {}))
    gathered = []
    monkeypatch.setattr(
        iron_age,
        "count_item",
        lambda _client, item_id: 5 if item_id == "minecraft:iron_ingot" else 0,
    )
    monkeypatch.setattr(
        iron_age,
        "gather_ores",
        lambda _client, ore_type, **kwargs: gathered.append(
            (ore_type, kwargs["count"])
        )
        or True,
    )

    assert iron_age.FoodAndIronHandler()._bulk_mine(client)
    assert gathered == [("diamond", 5), ("iron", 25)]


def test_completed_deep_haul_skips_another_descent(monkeypatch):
    counts = {
        "minecraft:diamond": 5,
        "minecraft:raw_iron": 32,
        "minecraft:iron_ingot": 5,
    }
    monkeypatch.setattr(
        iron_age,
        "count_item",
        lambda _client, item_id: counts.get(item_id, 0),
    )
    monkeypatch.setattr(
        iron_age,
        "go_to_y_level",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("completed haul must not descend again")
        ),
    )

    assert iron_age.FoodAndIronHandler()._dig_staircase(SimpleNamespace())


def test_completed_diamond_and_iron_gear_skip_all_deep_mining(monkeypatch):
    counts = {
        "minecraft:diamond": 5,
        "minecraft:iron_ingot": 4,
        "minecraft:iron_pickaxe": 4,
        "minecraft:iron_sword": 1,
        "minecraft:iron_axe": 1,
        "minecraft:iron_shovel": 1,
        "minecraft:bucket": 2,
    }
    client = SimpleNamespace()
    monkeypatch.setattr(
        iron_age,
        "count_item",
        lambda _client, item_id: counts.get(item_id, 0),
    )
    monkeypatch.setattr(
        iron_age,
        "has_full_armor",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        iron_age,
        "go_to_y_level",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("completed progression gear must not descend again")
        ),
    )
    monkeypatch.setattr(
        iron_age,
        "gather_ores",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("completed progression gear must not mine again")
        ),
    )

    handler = iron_age.FoodAndIronHandler()
    assert handler._dig_staircase(client)
    assert handler._bulk_mine(client)


def test_return_to_base_uses_checkpointed_house_not_mutable_waypoint(monkeypatch):
    class Transport:
        def dispatch(self, route, _payload):
            if route == "get_state":
                return {"block_position": {"x": 32, "y": -47, "z": -50}}
            if route == "get_block":
                return {
                    "id": "minecraft:birch_door",
                    "state": {"open": "false"},
                }
            return {}

    client = SimpleNamespace(transport=Transport())
    state = SimpleNamespace(
        custom_data={
            "base_location": [-9, 78, -122],
            "structures": {"starter_house": {"origin": [-9, 78, -122]}},
        }
    )
    destinations = []
    monkeypatch.setattr(
        iron_age,
        "goto",
        lambda _client, x, y, z, **_kwargs: destinations.append((x, y, z))
        or True,
    )

    # Make the post-arrival verification report the staged outside position,
    # then the house interior after the doorway traversal.
    client.transport.dispatch = lambda route, _payload: {
        "block_position": {
            "x": -6 if len(destinations) >= 2 else 32,
            "y": 79 if len(destinations) >= 2 else -47,
            "z": -121 if len(destinations) >= 2 else -50,
        }
    } if route == "get_state" else (
        {"id": "minecraft:birch_door", "state": {"open": "false"}}
        if route == "get_block"
        else {}
    )
    monkeypatch.setattr(iron_age.time, "sleep", lambda _seconds: None)

    assert iron_age.FoodAndIronHandler()._return_to_base(client, state)
    assert destinations == [(-6, 79, -124), (-6, 79, -121)]


def test_armor_phase_fails_closed_until_full_set_is_equipped(monkeypatch):
    client = SimpleNamespace()
    monkeypatch.setattr(iron_age, "equip_best_armor", lambda _client: 3)
    monkeypatch.setattr(iron_age, "has_full_armor", lambda *_args, **_kwargs: False)

    assert not iron_age.FoodAndIronHandler()._equip_iron_armor(client)


def test_initial_pickaxe_is_skipped_when_iron_target_is_already_carried(monkeypatch):
    counts = {"minecraft:iron_ingot": 15}
    handler = iron_age.FoodAndIronHandler()
    monkeypatch.setattr(
        handler,
        "_withdraw_initial_iron_supplies",
        lambda _client: None,
    )
    monkeypatch.setattr(
        iron_age,
        "count_item",
        lambda _client, item_id: counts.get(item_id, 0),
    )
    monkeypatch.setattr(
        iron_age,
        "ensure_supplies",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("completed initial iron must not require another pickaxe")
        ),
    )

    assert handler._ensure_initial_mining_pickaxe(SimpleNamespace())


def test_initial_pickaxe_gathers_wood_once_after_bounded_craft_failure(monkeypatch):
    handler = iron_age.FoodAndIronHandler()
    monkeypatch.setattr(
        handler,
        "_withdraw_initial_iron_supplies",
        lambda _client: None,
    )
    monkeypatch.setattr(iron_age, "count_item", lambda *_args: 0)
    attempts = []
    monkeypatch.setattr(
        iron_age,
        "ensure_supplies",
        lambda _client, requirements, timeout: attempts.append((requirements, timeout))
        or SimpleNamespace(success=len(attempts) == 2),
    )
    wood = []
    monkeypatch.setattr(
        iron_age,
        "gather_wood",
        lambda _client, count, timeout: wood.append((count, timeout)) or True,
    )

    assert handler._ensure_initial_mining_pickaxe(SimpleNamespace())
    assert attempts == [
        ({"minecraft:stone_pickaxe": 1}, 120),
        ({"minecraft:stone_pickaxe": 1}, 120),
    ]
    assert wood == [(4, 180)]


def test_initial_smelt_defers_carried_raw_iron_when_deep_kit_exists(monkeypatch):
    counts = {
        "minecraft:iron_ingot": 5,
        "minecraft:raw_iron": 5,
        "minecraft:iron_pickaxe": 3,
        "minecraft:bucket": 2,
    }
    monkeypatch.setattr(
        iron_age,
        "count_item",
        lambda _client, item_id: counts.get(item_id, 0),
    )
    monkeypatch.setattr(
        iron_age,
        "ensure_supplies",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("initial resume must defer bulk smelting")
        ),
    )

    assert iron_age.FoodAndIronHandler()._smelt_iron(SimpleNamespace())


def test_forced_smelt_uses_verified_nearby_furnace_without_crafting_furnace(monkeypatch):
    inventory = {
        "minecraft:iron_ingot": 5,
        "minecraft:raw_iron": 32,
    }
    smelt_calls = []
    furnace_pos = (-523, 66, 664)

    def count_item(_client, item_id):
        return inventory.get(item_id, 0)

    def smelt_with_furnace(_client, item_id, qty, furnace_pos=None):
        smelt_calls.append((item_id, qty, tuple(furnace_pos) if furnace_pos else None))
        if item_id == "minecraft:iron_ingot":
            inventory["minecraft:iron_ingot"] = qty
        return True

    monkeypatch.setattr(iron_age, "count_item", count_item)
    monkeypatch.setattr(resources, "count_item", count_item)
    monkeypatch.setattr(iron_age, "find_nearby_block", lambda *_args, **_kwargs: furnace_pos)
    monkeypatch.setattr(iron_age, "resume_active_furnace", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        iron_age,
        "gather_stone",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("nearby verified furnace should be reused")
        ),
    )
    monkeypatch.setattr(
        resources,
        "_smelt_with_furnace",
        smelt_with_furnace,
    )

    def block_furnace_craft(item_id, *_args, **_kwargs):
        raise AssertionError(f"unexpected shared-resource craft attempt: {item_id}")

    monkeypatch.setattr(
        resources,
        "craft",
        block_furnace_craft,
    )
    monkeypatch.setattr(iron_age.time, "sleep", lambda _seconds: None)

    assert iron_age.FoodAndIronHandler()._smelt_iron(
        SimpleNamespace(),
        force=True,
    )
    assert smelt_calls == [("minecraft:iron_ingot", 37, furnace_pos)]
