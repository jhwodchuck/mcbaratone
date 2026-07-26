import inspect
from types import SimpleNamespace

import pytest

from baritone_client.automator.phases import iron_age
from baritone_client.common import resources
from baritone_client.common.tasks import SurvivalRecoveryRequired


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
    assert calls == [12]


def test_iron_phase_checks_cataloged_food_before_hunting(monkeypatch):
    handler = iron_age.FoodAndIronHandler()
    handler.state = SimpleNamespace(custom_data={}, checkpoint_dir="test-run")
    calls = []
    monkeypatch.setattr(
        iron_age,
        "withdraw_required_from_catalog",
        lambda _client, requirements, state=None, max_travel_distance=None: calls.append(
            (requirements, max_travel_distance)
        )
        or 1,
    )
    monkeypatch.setattr(
        iron_age,
        "eat_until_hunger",
        lambda _client, minimum_food: minimum_food == 12,
    )

    assert handler._recover_food_from_known_sources(SimpleNamespace())
    assert calls and calls[0][0]["minecraft:bread"] == 8
    assert calls[0][1] == 96.0


def test_expedition_pickaxe_restores_banked_iron_tool(monkeypatch):
    handler = iron_age.FoodAndIronHandler()
    handler.state = SimpleNamespace(custom_data={}, checkpoint_dir="test-run")
    durability = {"value": 0}
    withdrawals = []
    monkeypatch.setattr(
        iron_age,
        "remaining_pickaxe_durability",
        lambda _client, _items: durability["value"],
    )

    def withdraw(_client, requirements, state=None, **_kwargs):
        withdrawals.append(requirements)
        if "minecraft:iron_pickaxe" in requirements:
            durability["value"] = 200
        return 1

    monkeypatch.setattr(iron_age, "withdraw_required_from_catalog", withdraw)

    assert handler._ensure_expedition_pickaxe(SimpleNamespace())
    assert withdrawals == [{"minecraft:iron_pickaxe": 1}]


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
        def __init__(self):
            self.health = 6.0

        def dispatch(self, route, _payload):
            if route == "get_state":
                return {"health": self.health, "food_level": 16}
            return {}

    transport = Transport()
    client = SimpleNamespace(transport=transport)
    calls = []
    monkeypatch.setattr(
        iron_age,
        "recover_health",
        lambda *_args, **_kwargs: calls.append("recover") or False,
    )
    monkeypatch.setattr(
        iron_age,
        "acquire_emergency_food",
        lambda *_args, **_kwargs: calls.append("acquire")
        or setattr(transport, "health", 12.0)
        or True,
    )

    assert iron_age.FoodAndIronHandler()._stabilize_hunger(client)
    assert calls == ["recover", "acquire"]


def test_iron_phase_holds_when_health_recovery_stays_critical(monkeypatch):
    class Transport:
        def dispatch(self, route, _payload):
            if route == "get_state":
                return {"health": 6.2, "food_level": 7}
            return {}

    client = SimpleNamespace(transport=Transport())
    monkeypatch.setattr(iron_age, "recover_health", lambda *_a, **_k: False)
    monkeypatch.setattr(iron_age, "acquire_emergency_food", lambda *_a, **_k: False)

    with pytest.raises(SurvivalRecoveryRequired):
        iron_age.FoodAndIronHandler()._stabilize_hunger(client)


def test_armored_critical_recovery_tries_known_herd_before_blind_search(
    monkeypatch,
):
    class Transport:
        def __init__(self):
            self.health = 3.5
            self.food = 10

        def dispatch(self, route, _payload):
            if route == "get_state":
                return {"health": self.health, "food_level": self.food}
            return {}

    transport = Transport()
    client = SimpleNamespace(transport=transport)
    handler = iron_age.FoodAndIronHandler()
    order = []
    monkeypatch.setattr(iron_age, "has_full_armor", lambda *_a, **_k: True)
    monkeypatch.setattr(iron_age, "recover_health", lambda *_a, **_k: False)

    def known(_client):
        order.append("known")
        transport.health = 12.0
        transport.food = 12
        return True

    monkeypatch.setattr(handler, "_recover_food_from_known_sources", known)
    monkeypatch.setattr(
        iron_age,
        "acquire_emergency_food",
        lambda *_a, **_k: order.append("blind") or False,
    )

    assert handler._stabilize_hunger(client)
    assert order == ["known"]


def test_iron_phase_skips_hunt_on_minimum_food(monkeypatch):
    class Transport:
        def dispatch(self, route, _payload):
            if route == "get_state":
                return {"health": 20.0, "food_level": 12}
            return {}

    client = SimpleNamespace(transport=Transport())
    searches = []
    monkeypatch.setattr(iron_age, "eat_until_hunger", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        iron_age,
        "acquire_emergency_food",
        lambda _client, **kwargs: searches.append(kwargs) or True,
    )

    assert iron_age.FoodAndIronHandler()._stabilize_hunger(client)
    assert not searches


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
    assert "#set allowDownward true" in messages
    assert "#set allowDownward false" in messages
    assert messages[-1] == "#set allowDownward false"
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
    monkeypatch.setattr(
        handler, "_reserve_inventory_space", lambda *_args, **_kwargs: True
    )
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

    def withdraw_required(_client, requirements, state=None):
        captured_withdrawals.append(dict(requirements))
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
    monkeypatch.setattr(iron_age, "withdraw_required_from_catalog", withdraw_required)
    monkeypatch.setattr(
        iron_age,
        "gather_ores",
        lambda *_args, **_kwargs: gathered.append(True) or True,
    )

    assert handler._mine_initial_iron(client)
    assert not gathered
    assert captured_withdrawals == [{"minecraft:raw_iron": 15}]


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

    def withdraw_required(_client, requirements, state=None):
        captured_withdrawals.append(dict(requirements))
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
    monkeypatch.setattr(iron_age, "withdraw_required_from_catalog", withdraw_required)
    monkeypatch.setattr(
        resources,
        "gather_ores",
        lambda *_args, **_kwargs: False,
    )

    assert handler._mine_initial_iron(client)
    assert captured_withdrawals == [{"minecraft:raw_iron": 1}]


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


def test_deep_mining_prep_passes_with_one_usable_iron_pickaxe(monkeypatch):
    # The live FOOD_AND_IRON deadlock: an iron+stone kit at 318 durability was
    # rejected by the old 350 gate and forced crafting a SECOND iron pickaxe,
    # which then failed on stick prep and trapped the phase. One usable pick
    # (>=200) plus a bucket must pass with NO crafting.
    client = SimpleNamespace()
    crafted = []
    item_counts = {
        "minecraft:stone_pickaxe": 1,
        "minecraft:iron_pickaxe": 1,
        "minecraft:bucket": 1,
    }
    handler = iron_age.FoodAndIronHandler()
    monkeypatch.setattr(
        iron_age, "count_item", lambda _client, item_id: item_counts.get(item_id, 0)
    )
    monkeypatch.setattr(
        iron_age, "remaining_pickaxe_durability", lambda *_a, **_k: 318
    )
    monkeypatch.setattr(handler, "_ensure_mining_workstation", lambda _client: True)
    monkeypatch.setattr(
        iron_age,
        "_craft_with_table",
        lambda _client, item_id, target: crafted.append((item_id, target)) or True,
    )

    assert handler._craft_essential_iron(client)
    assert crafted == [], "a usable iron pickaxe + bucket must not craft a spare"


def test_deep_mining_prep_replaces_nearly_broken_pickaxes(monkeypatch):
    client = SimpleNamespace()
    crafted = []
    def remaining_durability(*_args, **_kwargs):
        return 290 if crafted else 40

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
        remaining_durability,
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
    # 40 durability is below the 200 bar, so it crafts one replacement; 290
    # then clears the bar and it stops (the old 350 bar needed a second craft).
    assert crafted == [("minecraft:iron_pickaxe", 2)]


def test_craft_essential_iron_reuses_stone_pickaxe_durability_for_reconnect(monkeypatch):
    client = SimpleNamespace()
    crafted = []

    def remaining_durability(*_args, **_kwargs):
        return 370 if crafted else 180

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
        remaining_durability,
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
    assert crafted == [("minecraft:iron_pickaxe", 2)]


def test_ready_deep_mining_kit_skips_unreachable_workstation(monkeypatch):
    """Bot07 already had a fresh iron pick and bucket eight blocks below its table."""
    handler = iron_age.FoodAndIronHandler()
    counts = {
        "minecraft:iron_pickaxe": 1,
        "minecraft:bucket": 1,
    }
    monkeypatch.setattr(
        iron_age,
        "count_item",
        lambda _client, item_id: counts.get(item_id, 0),
    )
    monkeypatch.setattr(
        iron_age,
        "remaining_pickaxe_durability",
        lambda *_args, **_kwargs: 250,
    )
    monkeypatch.setattr(
        handler,
        "_ensure_mining_workstation",
        lambda _client: (_ for _ in ()).throw(
            AssertionError("completed kit must not touch a workstation")
        ),
    )

    assert handler._craft_essential_iron(SimpleNamespace())


def test_mining_workstation_banks_excess_before_ground_disposal(monkeypatch):
    handler = iron_age.FoodAndIronHandler()
    handler.state = SimpleNamespace(custom_data={})
    client = SimpleNamespace(
        transport=SimpleNamespace(dispatch=lambda *_args, **_kwargs: {})
    )
    free_slots = {"count": 0}
    deposits = []

    monkeypatch.setattr(
        iron_age,
        "free_inventory_slots",
        lambda _client: free_slots["count"],
    )
    monkeypatch.setattr(
        handler,
        "_resolve_initial_iron_supply_chest",
        lambda _client: (-92, 70, 41),
    )

    def deposit(_client, chest_pos, state=None):
        deposits.append((chest_pos, state))
        free_slots["count"] = 3
        return 4

    monkeypatch.setattr(iron_age, "deposit_excess_to_chest", deposit)
    monkeypatch.setattr(
        iron_age,
        "manage_inventory",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("persistent storage should satisfy the request")
        ),
    )
    monkeypatch.setattr(
        iron_age,
        "find_nearby_block",
        lambda *_args, **_kwargs: (-92, 70, 40),
    )
    monkeypatch.setattr(iron_age.harness_ops, "available", lambda: True)
    monkeypatch.setattr(
        iron_age.harness_ops,
        "ensure_crafting_table_open",
        lambda *_args, **_kwargs: True,
    )

    assert handler._ensure_mining_workstation(client)
    assert deposits == [((-92, 70, 41), handler.state)]


def test_craft_with_table_converts_absolute_target_to_missing_count(monkeypatch):
    counts = {"minecraft:iron_pickaxe": 1}
    crafted = []
    client = SimpleNamespace()

    def fake_craft(_client, item_id, count):
        crafted.append((item_id, count))
        counts[item_id] = counts.get(item_id, 0) + count
        return True

    monkeypatch.setattr(
        resources,
        "count_item",
        lambda _client, item_id: counts.get(item_id, 0),
    )
    monkeypatch.setattr(resources, "ensure_tool_sticks", lambda *_args: True)
    monkeypatch.setattr(resources, "ensure_stone_material", lambda *_args: True)
    monkeypatch.setattr(
        resources,
        "craft",
        fake_craft,
    )
    monkeypatch.setattr(
        "baritone_client.common.base.open_crafting_table",
        lambda _client: True,
    )
    monkeypatch.setattr(resources.time, "sleep", lambda _seconds: None)

    assert resources._craft_with_table(client, "minecraft:iron_pickaxe", 2)
    assert crafted == [("minecraft:iron_pickaxe", 1)]


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


def test_return_to_base_prefers_verified_storage_over_stale_house_chest(monkeypatch):
    client = SimpleNamespace()
    state = SimpleNamespace(
        custom_data={
            "structures": {
                "starter_house": {
                    "origin": [-142, 65, 84],
                    "supply_chest": [-141, 66, 86],
                }
            }
        }
    )
    handler = iron_age.FoodAndIronHandler()
    monkeypatch.setattr(
        handler,
        "_resolve_initial_iron_supply_chest",
        lambda _client, _state: (-40, 69, 28),
    )
    destinations = []
    monkeypatch.setattr(
        iron_age,
        "goto",
        lambda _client, x, y, z, **_kwargs: destinations.append((x, y, z))
        or True,
    )

    assert handler._return_to_base(client, state)
    assert destinations == [(-40, 69, 28)]


def test_return_to_base_accepts_verified_operational_chest_in_interaction_range(
    monkeypatch,
):
    class Transport:
        def dispatch(self, route, payload):
            if route == "get_state":
                return {"block_position": {"x": -39, "y": 66, "z": 26}}
            if route == "get_block":
                assert (payload["x"], payload["y"], payload["z"]) == (-40, 69, 28)
                return {"id": "minecraft:chest"}
            return {}

    client = SimpleNamespace(transport=Transport())
    state = SimpleNamespace(
        custom_data={
            "structures": {
                "starter_house": {"supply_chest": [-141, 66, 86]}
            }
        }
    )
    handler = iron_age.FoodAndIronHandler()
    monkeypatch.setattr(
        handler,
        "_resolve_initial_iron_supply_chest",
        lambda _client, _state: (-40, 69, 28),
    )
    monkeypatch.setattr(iron_age, "goto", lambda *_args, **_kwargs: False)

    assert handler._return_to_base(client, state)


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


def test_y_descent_falls_back_to_vertical_when_all_diagonals_blocked(monkeypatch):
    # Cliff edge / cave mouth: every diagonal neighbour has an air floor, so the
    # staircase stalled and stranded the bot mid-descent. A straight-down step is
    # safe here because there is solid ground to land on one block below.
    from baritone_client.common import resources

    class Transport:
        def __init__(self):
            self.calls = []
            self.vcol = 0  # call count for the vertical break column (10,29,5)
            self.states = iter((
                {"block_position": {"x": 10, "y": 30, "z": 5}, "health": 20, "food_level": 20},
                {"block_position": {"x": 10, "y": 29, "z": 5}, "health": 20, "food_level": 20},
                {"block_position": {"x": 10, "y": 29, "z": 5}, "health": 20, "food_level": 20},
            ))

        def dispatch(self, route, payload):
            self.calls.append((route, payload))
            if route == "get_state":
                return next(self.states)
            if route == "get_block":
                x, yy, z = payload["x"], payload["y"], payload["z"]
                if x == 10 and z == 5 and yy == 29:
                    # floor under our feet: solid, then air once broken
                    self.vcol += 1
                    return {"id": "minecraft:air" if self.vcol >= 3 else "minecraft:stone"}
                if x == 10 and z == 5 and yy == 28:
                    return {"id": "minecraft:stone"}  # verified solid landing
                return {"id": "minecraft:air"}  # every diagonal floor is air
            if route == "break_block":
                return {"started": True}
            return {}

    transport = Transport()
    client = SimpleNamespace(transport=transport)
    monkeypatch.setattr(resources, "_ensure_mining_pickaxe", lambda _client: True)
    monkeypatch.setattr(resources.time, "sleep", lambda _seconds: None)

    assert resources.go_to_y_level(client, 26, timeout=10)
    gotos = [payload for route, payload in transport.calls if route == "goto"]
    # It stepped straight DOWN (same x/z), not diagonally.
    assert gotos and gotos[0] == {"x": 10, "y": 29, "z": 5}
    assert "Y navigation stalled" not in "".join(
        str(p) for _r, p in transport.calls if _r == "chat"
    )


def test_y_descent_delegates_verified_deepslate_when_exact_break_fails(monkeypatch):
    """Bot07 must let guarded goto mine deepslate left by exact break_block."""
    from baritone_client.common import resources

    class Transport:
        def __init__(self):
            self.calls = []
            self.moved = False

        def dispatch(self, route, payload):
            self.calls.append((route, payload))
            if route == "get_state":
                y = 2 if self.moved else 3
                return {
                    "block_position": {"x": 0 if not self.moved else 1, "y": y, "z": 0},
                    "health": 20,
                    "food_level": 20,
                }
            if route == "get_block":
                pos = (payload["x"], payload["y"], payload["z"])
                if pos == (1, 1, 0):
                    return {"id": "minecraft:stone"}
                if pos in {(1, 2, 0), (1, 3, 0)}:
                    return {"id": "minecraft:deepslate"}
                return {"id": "minecraft:bedrock"}
            if route == "break_block":
                # This is the live Bot07 failure: the exact builder cannot clear
                # the block, while normal Baritone path excavation still can.
                return {"error": "exact builder made no progress"}
            if route == "goto" and payload == {"x": 1, "y": 2, "z": 0}:
                self.moved = True
                return {"started": True}
            return {}

    transport = Transport()
    client = SimpleNamespace(transport=transport)
    monkeypatch.setattr(resources, "_ensure_mining_pickaxe", lambda _client: True)
    monkeypatch.setattr(resources.time, "sleep", lambda _seconds: None)

    assert resources.go_to_y_level(client, -1, timeout=10)
    assert ("goto", {"x": 1, "y": 2, "z": 0}) in transport.calls
    assert not any(route == "break_block" for route, _payload in transport.calls)


def test_y_descent_prefers_verified_vertical_step_in_dense_deepslate(monkeypatch):
    """Do not wait on four diagonal goals before the working vertical route."""
    from baritone_client.common import resources

    class Transport:
        def __init__(self):
            self.calls = []
            self.moved = False

        def dispatch(self, route, payload):
            self.calls.append((route, payload))
            if route == "get_state":
                return {
                    "block_position": {"x": 0, "y": 2 if self.moved else 3, "z": 0},
                    "health": 20,
                    "food_level": 20,
                }
            if route == "get_block":
                pos = (payload["x"], payload["y"], payload["z"])
                if pos == (0, 3, 0):
                    return {"id": "minecraft:air"}
                if pos == (0, 2, 0):
                    return {"id": "minecraft:deepslate"}
                if pos == (0, 1, 0):
                    return {"id": "minecraft:stone"}
                return {"id": "minecraft:bedrock"}
            if route == "goto" and payload == {"x": 0, "y": 2, "z": 0}:
                self.moved = True
                return {"started": True}
            return {}

    transport = Transport()
    client = SimpleNamespace(transport=transport)
    monkeypatch.setattr(resources, "_ensure_mining_pickaxe", lambda _client: True)
    monkeypatch.setattr(resources.time, "sleep", lambda _seconds: None)

    assert resources.go_to_y_level(client, -1, timeout=10)
    gotos = [payload for route, payload in transport.calls if route == "goto"]
    assert gotos[0] == {"x": 0, "y": 2, "z": 0}


def test_y_descent_relocates_out_of_gravel_collar(monkeypatch):
    """Bot08 must tunnel sideways to stable support, never dig down through gravel."""
    from baritone_client.common import resources

    class Transport:
        def __init__(self):
            self.calls = []
            self.at_anchor = False

        def dispatch(self, route, payload):
            self.calls.append((route, payload))
            if route == "get_state":
                if self.at_anchor:
                    return {
                        "block_position": {"x": 2, "y": 65, "z": 0},
                        "health": 20,
                        "food_level": 20,
                    }
                return {
                    "block_position": {"x": 0, "y": 66, "z": 0},
                    "health": 20,
                    "food_level": 20,
                }
            if route == "get_block":
                pos = (payload["x"], payload["y"], payload["z"])
                stable_anchor = {
                    (2, 64, 0): "minecraft:stone",
                    (2, 65, 0): "minecraft:stone",
                    (2, 66, 0): "minecraft:air",
                    (2, 67, 0): "minecraft:air",
                }
                return {"id": stable_anchor.get(pos, "minecraft:gravel")}
            if route == "goto" and payload == {"x": 2, "y": 66, "z": 0}:
                self.at_anchor = True
            return {}

    transport = Transport()
    client = SimpleNamespace(transport=transport)
    monkeypatch.setattr(resources.time, "sleep", lambda _seconds: None)

    assert resources.go_to_y_level(client, 62, timeout=10)
    gotos = [payload for route, payload in transport.calls if route == "goto"]
    assert {"x": 2, "y": 66, "z": 0} in gotos
    assert not any(route == "break_block" for route, _payload in transport.calls)


def test_armor_phase_fails_closed_until_full_set_is_equipped(monkeypatch):
    client = SimpleNamespace()
    monkeypatch.setattr(iron_age, "equip_best_armor", lambda _client: 3)
    monkeypatch.setattr(iron_age, "has_full_armor", lambda *_args, **_kwargs: False)

    assert not iron_age.FoodAndIronHandler()._equip_iron_armor(client)


def test_affordable_armor_is_equipped_before_deep_descent(monkeypatch):
    handler = iron_age.FoodAndIronHandler()
    client = SimpleNamespace()
    calls = []
    monkeypatch.setattr(iron_age, "has_full_armor", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(handler, "_read_state", lambda *_args: {"health": 20})
    monkeypatch.setattr(
        iron_age,
        "count_item",
        lambda _client, item_id: 24 if item_id == "minecraft:iron_ingot" else 0,
    )
    monkeypatch.setattr(
        handler,
        "_craft_iron_armor",
        lambda _client: calls.append("craft") or True,
    )
    monkeypatch.setattr(
        handler,
        "_equip_iron_armor",
        lambda _client: calls.append("equip") or True,
    )

    assert handler._equip_affordable_pre_descent_armor(client)
    assert calls == ["craft", "equip"]


def test_critical_health_armor_uses_stationary_crafting(monkeypatch):
    handler = iron_age.FoodAndIronHandler()
    handler._cached_state = {"health": 3.5}
    client = SimpleNamespace()
    crafted = []
    monkeypatch.setattr(iron_age, "has_full_armor", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(handler, "_read_state", lambda *_args: {"health": 3.5})
    monkeypatch.setattr(
        iron_age,
        "count_item",
        lambda _client, item_id: 34 if item_id == "minecraft:iron_ingot" else 0,
    )
    monkeypatch.setattr(
        handler,
        "_ensure_mining_workstation",
        lambda _client: True,
    )
    monkeypatch.setattr(iron_age, "_ensure_raw_planks", lambda *_args: True)
    monkeypatch.setattr(
        iron_age,
        "_craft_with_table",
        lambda _client, item_id, count: crafted.append((item_id, count)) or True,
    )
    monkeypatch.setattr(handler, "_equip_iron_armor", lambda _client: True)

    assert handler._equip_affordable_pre_descent_armor(client)
    assert [item_id for item_id, _count in crafted] == [
        "minecraft:iron_helmet",
        "minecraft:iron_chestplate",
        "minecraft:iron_leggings",
        "minecraft:iron_boots",
    ]


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


def test_phase_retry_rechecks_initial_iron_storage(monkeypatch):
    handler = iron_age.FoodAndIronHandler()
    handler._initial_iron_supplies_withdrawn = True
    observed = []

    class CapturingSequence:
        def __init__(self, _name, _tasks):
            pass

        def run(self, _client):
            observed.append(handler._initial_iron_supplies_withdrawn)
            return True

    monkeypatch.setattr(iron_age, "SequentialTask", CapturingSequence)

    assert handler.execute(
        SimpleNamespace(),
        SimpleNamespace(),
        SimpleNamespace(),
    )
    assert observed == [False]


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
    # The first craft attempt is intentionally short (fail fast when wood
    # dependencies are absent instead of idling two minutes every phase
    # retry); a small wood reserve is gathered, then one longer retry runs.
    assert attempts == [
        ({"minecraft:stone_pickaxe": 1}, 30),
        ({"minecraft:stone_pickaxe": 1}, 60),
    ]
    assert wood == [(2, 90)]


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
        iron_age, "remaining_pickaxe_durability", lambda *_args, **_kwargs: 250
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


def test_return_to_base_enters_through_missing_door_instead_of_failing(monkeypatch):
    # A griefed/never-built door is an open doorway, not a hard blocker. It must
    # not dead-end the smelting return (Bot07 hit "Starter-house door is missing"
    # every attempt and permanently gave up on FOOD_AND_IRON).
    origin = (-9, 78, -122)
    # Interior target is (origin.x+3, origin.y+1, origin.z+3); door defaults to
    # (origin.x+3, origin.y+1, origin.z). goto reports the outside first, then
    # the interior once traversal is issued.
    calls = {"goto": 0}
    interior = (origin[0] + 3, origin[1] + 1, origin[2] + 3)

    def fake_goto(_client, x, y, z, **_kwargs):
        calls["goto"] += 1
        return True

    class Transport:
        def dispatch(self, route, _payload):
            if route == "get_state":
                # Before any goto: far away. After the interior goto: inside.
                if calls["goto"] >= 2:
                    return {"block_position": {"x": interior[0], "y": interior[1], "z": interior[2]}}
                return {"block_position": {"x": 999, "y": 5, "z": 999}}
            if route == "get_block":
                return {"id": "minecraft:air", "state": {}}  # NO door present
            return {}

    client = SimpleNamespace(transport=Transport())
    state = SimpleNamespace(
        custom_data={"structures": {"starter_house": {"origin": list(origin)}}}
    )
    monkeypatch.setattr(iron_age, "goto", fake_goto)
    monkeypatch.setattr(iron_age.time, "sleep", lambda _s: None)

    assert iron_age.FoodAndIronHandler()._return_to_base(client, state)
    assert calls["goto"] == 2  # walked to the doorway, then into the interior


def test_y_descent_survives_transient_bridge_timeouts(monkeypatch):
    # Under fleet load the bridge stalls: a single get_state/get_block timeout
    # used to raise out and abandon a partly-dug staircase. It must now skip the
    # bad read and keep descending.
    from baritone_client.common import resources
    from baritone_client.core.exceptions import TransportError

    class Transport:
        def __init__(self):
            self.calls = []
            self.state_calls = 0
            self.vcol = 0

        def dispatch(self, route, payload):
            self.calls.append((route, payload))
            if route == "get_state":
                self.state_calls += 1
                if self.state_calls == 1:
                    raise TransportError("Timeout (route: get_state)")
                if self.state_calls <= 3:
                    return {"block_position": {"x": 10, "y": 30, "z": 5}, "health": 20, "food_level": 20}
                return {"block_position": {"x": 10, "y": 29, "z": 5}, "health": 20, "food_level": 20}
            if route == "get_block":
                x, yy, z = payload["x"], payload["y"], payload["z"]
                if x == 10 and z == 5 and yy == 29:
                    self.vcol += 1
                    if self.vcol == 1:
                        raise TransportError("Timeout (route: get_block)")
                    return {"id": "minecraft:air" if self.vcol >= 4 else "minecraft:stone"}
                if x == 10 and z == 5 and yy == 28:
                    return {"id": "minecraft:stone"}
                return {"id": "minecraft:air"}
            if route == "break_block":
                return {"started": True}
            return {}

    transport = Transport()
    client = SimpleNamespace(transport=transport)
    monkeypatch.setattr(resources, "_ensure_mining_pickaxe", lambda _client: True)
    monkeypatch.setattr(resources.time, "sleep", lambda _seconds: None)

    assert resources.go_to_y_level(client, 26, timeout=10)


def test_protected_smelting_return_skips_once_essentials_exist(monkeypatch):
    # A descent (or any late) failure re-runs FOOD_AND_IRON from the top; the
    # "return to base for protected smelting" step must NOT drag the bot back to
    # the surface once the iron pickaxe + bucket already exist, or the descent
    # restarts from scratch every retry and never reaches Y-58.
    handler = iron_age.FoodAndIronHandler()
    returned = []
    monkeypatch.setattr(handler, "_return_to_base", lambda _c, _s: returned.append(True) or True)

    counts = {"minecraft:iron_pickaxe": 1, "minecraft:bucket": 1}
    monkeypatch.setattr(iron_age, "count_item", lambda _c, item: counts.get(item, 0))

    assert handler._return_to_base_for_initial_smelting(object(), object())
    assert returned == [], "must not return to base when the starter kit is forged"


def test_protected_smelting_return_still_runs_before_kit_exists(monkeypatch):
    # Before the pickaxe/bucket exist, the return-to-base is genuinely needed to
    # reach the furnace and forge them.
    handler = iron_age.FoodAndIronHandler()
    returned = []
    monkeypatch.setattr(handler, "_return_to_base", lambda _c, _s: returned.append(True) or True)
    monkeypatch.setattr(iron_age, "count_item", lambda _c, _item: 0)

    assert handler._return_to_base_for_initial_smelting(object(), object())
    assert returned == [True], "must return to base to forge the starter kit"


def test_protected_smelting_return_skips_after_pick_breaks_on_deep_haul(monkeypatch):
    handler = iron_age.FoodAndIronHandler()
    returned = []
    monkeypatch.setattr(handler, "_return_to_base", lambda _c, _s: returned.append(True) or True)
    counts = {"minecraft:diamond": 5, "minecraft:bucket": 1}
    monkeypatch.setattr(iron_age, "count_item", lambda _c, item: counts.get(item, 0))

    assert handler._return_to_base_for_initial_smelting(object(), object())
    assert returned == []


def test_food_and_iron_banks_excess_before_first_smelting():
    handler = iron_age.FoodAndIronHandler()
    source = inspect.getsource(handler.execute)

    assert source.index("Deposit bulky excess before smelting") < source.index(
        'ActionTask("Smelt iron ingots"'
    )


def _deposit_client(chest_block_seq, player_pos=(-114, 88, -34)):
    """Client whose get_block at the chest coord walks through a sequence of
    ids (so a re-placement can flip 'air' -> 'chest'), and whose get_state
    reports the player standing next to the chest."""
    seq = list(chest_block_seq)
    calls = {"place": []}

    def dispatch(route, payload=None):
        if route == "get_block":
            return {"id": seq[min(len(seq) - 1, calls.get("gb", 0))]}
        if route == "get_state":
            return {"block_position": {"x": player_pos[0], "y": player_pos[1], "z": player_pos[2]}}
        return {}

    return SimpleNamespace(transport=SimpleNamespace(dispatch=dispatch)), calls


def test_deposit_replaces_missing_supply_chest_then_deposits(monkeypatch):
    """A stale/missing supply-chest coordinate must be re-established, not
    treated as a fatal phase failure. Confirmed live: Bot08 failed the
    deposit 368 times standing next to an empty (-114,88,-35)."""
    handler = iron_age.FoodAndIronHandler()
    handler.state = SimpleNamespace(
        custom_data={"structures": {"starter_house": {"supply_chest": [-114, 88, -35]}}}
    )
    # First get_block sees no chest; after re-placement it reads as a chest.
    block_state = {"placed": False}

    def get_block_dispatch(route, payload=None):
        if route == "get_block":
            return {"id": "minecraft:chest" if block_state["placed"] else "minecraft:air"}
        if route == "get_state":
            return {"block_position": {"x": -114, "y": 88, "z": -34}}
        return {}

    client = SimpleNamespace(transport=SimpleNamespace(dispatch=get_block_dispatch))

    deposit_attempts = []
    def deposit(_client, chest_pos, state=None):
        # Fail until the chest has been re-placed.
        deposit_attempts.append(chest_pos)
        return 2 if block_state["placed"] else -1
    monkeypatch.setattr(iron_age, "deposit_excess_to_chest", deposit)
    monkeypatch.setattr(iron_age, "count_item", lambda _c, _i: 1)  # carries a chest
    def place(_c, x, y, z, item, allow_break=True):
        block_state["placed"] = True
        return True
    monkeypatch.setattr(iron_age.harness_ops, "place_block_exact", place)
    monkeypatch.setattr(iron_age.harness_ops, "move_near", lambda *_a, **_k: True)

    assert handler._deposit_excess_at_home(client, handler.state) is True
    assert block_state["placed"] is True
    # Deposited after re-placement (second attempt succeeded).
    assert len(deposit_attempts) == 2


def test_deposit_does_not_brick_phase_when_chest_unrecoverable(monkeypatch):
    """If the chest cannot be re-established at all, depositing (an inventory
    optimization) must not fail the whole phase forever -- the bot should
    carry the excess and continue to the descent."""
    handler = iron_age.FoodAndIronHandler()
    handler.state = SimpleNamespace(
        custom_data={"structures": {"starter_house": {"supply_chest": [-114, 88, -35]}}}
    )

    def dispatch(route, payload=None):
        if route == "get_block":
            return {"id": "minecraft:air"}
        if route == "get_state":
            return {"block_position": {"x": -114, "y": 88, "z": -34}}
        return {}
    client = SimpleNamespace(transport=SimpleNamespace(dispatch=dispatch))

    monkeypatch.setattr(iron_age, "deposit_excess_to_chest", lambda *_a, **_k: -1)
    monkeypatch.setattr(iron_age, "count_item", lambda _c, _i: 0)  # no chest carried
    monkeypatch.setattr(iron_age, "craft", lambda *_a, **_k: False)  # cannot craft one
    monkeypatch.setattr(iron_age.harness_ops, "move_near", lambda *_a, **_k: True)

    # Must return True (continue), not False (brick the phase).
    assert handler._deposit_excess_at_home(client, handler.state) is True


def test_stabilize_hunger_falls_back_to_farm_when_local_search_fails(monkeypatch):
    """When the local bounded emergency-food search fails (as it always will
    in an animal-sparse biome), the established wheat farm must be tried
    before degrading to a low-hunger floor or failing outright. Confirmed
    live: Bot09 stuck in a "no passive food source loaded" loop with an
    established farm sitting unused."""
    class Transport:
        def dispatch(self, route, _payload):
            if route == "get_state":
                return {"health": 20.0, "food_level": 3}
            return {}

    client = SimpleNamespace(transport=Transport())
    handler = iron_age.FoodAndIronHandler()
    handler.state = SimpleNamespace(
        custom_data={"wheat_farm": {"origin": [10, 70, 20]}}
    )

    # eat_until_hunger is called twice: once in the normal flow (must fail,
    # so the fallback chain actually runs) and once after the farm harvest
    # (must succeed) -- track state across the two calls explicitly.
    harvested = []
    fed_by_farm = {"v": False}

    def eat_until_hunger_stub(_client, minimum_food):
        return fed_by_farm["v"]

    def harvest_then_allow_eating(_c, x, y, z):
        harvested.append((x, y, z))
        fed_by_farm["v"] = True
        return True

    monkeypatch.setattr(iron_age, "eat_until_hunger", eat_until_hunger_stub)
    monkeypatch.setattr(iron_age, "acquire_emergency_food", lambda *_a, **_k: False)
    monkeypatch.setattr(iron_age, "harvest_wheat_farm", harvest_then_allow_eating)
    monkeypatch.setattr(
        iron_age, "visit_known_herd_for_loot",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("must not travel to the herd when the farm already worked")
        ),
    )

    assert handler._stabilize_hunger(client) is True
    assert harvested == [(10, 70, 20)]
    assert handler._stabilize_hunger_failures == 0


def test_stabilize_hunger_falls_back_to_known_herd_when_no_farm_established(monkeypatch):
    """A verified persisted herd is reusable when no farm is established."""
    class Transport:
        def dispatch(self, route, _payload):
            if route == "get_state":
                return {"health": 20.0, "food_level": 3}
            return {}

    client = SimpleNamespace(transport=Transport())
    handler = iron_age.FoodAndIronHandler()
    handler.state = SimpleNamespace(
        custom_data={
            "structures": {
                "food_source": {
                    "location": [30, 70, 40],
                    "verified": True,
                }
            }
        }
    )

    visited = []
    fed_by_herd = {"v": False}

    def eat_until_hunger_stub(_client, minimum_food):
        return fed_by_herd["v"]

    def visit_herd(_client, required_loot, animal_type, **kwargs):
        visited.append((dict(required_loot), animal_type, kwargs))
        fed_by_herd["v"] = True
        return True

    monkeypatch.setattr(iron_age, "eat_until_hunger", eat_until_hunger_stub)
    monkeypatch.setattr(iron_age, "acquire_emergency_food", lambda *_a, **_k: False)
    monkeypatch.setattr(
        iron_age, "harvest_wheat_farm",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("must not try to harvest a farm that was never established")
        ),
    )
    monkeypatch.setattr(iron_age, "visit_known_herd_for_loot", visit_herd)

    assert handler._stabilize_hunger(client) is True
    assert visited == [
        (
            {"minecraft:beef": 3},
            "cow",
            {"preserve_breeding_pair": True, "location": [30, 70, 40]},
        )
    ]


def test_y_descent_yields_instead_of_mining_at_critical_health(monkeypatch):
    class Transport:
        def __init__(self):
            self.calls = []

        def dispatch(self, route, payload):
            self.calls.append((route, payload))
            if route == "get_state":
                return {
                    "block_position": {"x": 10, "y": 30, "z": 5},
                    "health": 6,
                    "food_level": 16,
                }
            return {}

    client = SimpleNamespace(transport=Transport())
    monkeypatch.setattr(
        "baritone_client.common.combat.recover_health",
        lambda *_args, **_kwargs: False,
    )

    with pytest.raises(SurvivalRecoveryRequired):
        resources.go_to_y_level(client, -58, timeout=10)

    routes = [route for route, _payload in client.transport.calls]
    assert "cancel" in routes
    assert "break_block" not in routes


def test_ensure_supplies_yields_before_handler_at_critical_health(monkeypatch):
    calls = []

    class Transport:
        def dispatch(self, route, _payload):
            calls.append(route)
            if route == "get_state":
                return {"health": 2.5, "food_level": 16}
            return {}

    client = SimpleNamespace(transport=Transport())
    monkeypatch.setattr(resources, "_missing_requirements", lambda *_a: {"x": 1})
    monkeypatch.setattr(
        "baritone_client.common.combat.recover_health",
        lambda *_args, **_kwargs: False,
    )
    handlers = {
        "x": lambda *_a: (_ for _ in ()).throw(AssertionError("unsafe handler"))
    }

    with pytest.raises(SurvivalRecoveryRequired):
        resources.ensure_supplies(client, {"x": 1}, strategies=handlers)

    assert "cancel" in calls


def test_stabilize_hunger_still_degrades_when_farm_and_herd_both_fail(monkeypatch):
    """If the farm/herd fallback also fails, existing degraded-floor and
    failure-counter behavior must still apply unchanged."""
    class Transport:
        def dispatch(self, route, _payload):
            if route == "get_state":
                return {"health": 20.0, "food_level": 3}
            return {}

    client = SimpleNamespace(transport=Transport())
    handler = iron_age.FoodAndIronHandler()
    handler.state = SimpleNamespace(custom_data={})

    monkeypatch.setattr(iron_age, "eat_until_hunger", lambda *_a, **_k: False)
    monkeypatch.setattr(iron_age, "acquire_emergency_food", lambda *_a, **_k: False)
    monkeypatch.setattr(iron_age, "visit_known_herd_for_loot", lambda *_a, **_k: False)

    # food_level (3) is below safe_hunger_floor (6), so it must fail closed
    # until the failure counter reaches its threshold.
    with pytest.raises(SurvivalRecoveryRequired):
        handler._stabilize_hunger(client)
    assert handler._stabilize_hunger_failures == 1


def test_stabilize_hunger_health_branch_falls_back_to_known_sources(monkeypatch):
    """A critically low-health bot whose local emergency search fails must try
    the persisted farm/herd before yielding, not starve in place. Confirmed
    live: Bot10 died here because the health-critical branch never consulted
    the known food sources the hunger branch below already uses."""
    fed = {"v": False}

    class Transport:
        def dispatch(self, route, _payload):
            if route == "get_state":
                # Health only recovers once the farm harvest + eat has run.
                return {
                    "health": 20.0 if fed["v"] else 6.0,
                    "food_level": 20 if fed["v"] else 5,
                }
            return {}

    client = SimpleNamespace(transport=Transport())
    handler = iron_age.FoodAndIronHandler()
    handler.state = SimpleNamespace(
        custom_data={"wheat_farm": {"origin": [10, 70, 20]}}
    )

    harvested = []

    def harvest_then_allow_regen(_c, x, y, z):
        harvested.append((x, y, z))
        fed["v"] = True
        return True

    # recover_health cannot restore health with no carried food; only the
    # farm harvest + eat below makes natural regen (and thus recovery) possible.
    monkeypatch.setattr(iron_age, "recover_health", lambda *_a, **_k: fed["v"])
    monkeypatch.setattr(iron_age, "acquire_emergency_food", lambda *_a, **_k: False)
    monkeypatch.setattr(iron_age, "harvest_wheat_farm", harvest_then_allow_regen)
    monkeypatch.setattr(iron_age, "eat_until_hunger", lambda *_a, **_k: True)
    monkeypatch.setattr(
        iron_age,
        "visit_known_herd_for_loot",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("farm already restored health; no herd trip needed")
        ),
    )

    assert handler._stabilize_hunger(client) is True
    assert harvested == [(10, 70, 20)]
    assert handler._stabilize_hunger_failures == 0


def test_stabilize_hunger_health_branch_still_yields_when_no_food_source(monkeypatch):
    """With no farm/herd and a failed local search, the critically low-health
    bot must still yield for survival recovery (unchanged safety behavior)."""
    class Transport:
        def dispatch(self, route, _payload):
            if route == "get_state":
                return {"health": 6.0, "food_level": 5}
            return {}

    client = SimpleNamespace(transport=Transport())
    handler = iron_age.FoodAndIronHandler()
    handler.state = SimpleNamespace(custom_data={})

    monkeypatch.setattr(iron_age, "recover_health", lambda *_a, **_k: False)
    monkeypatch.setattr(iron_age, "acquire_emergency_food", lambda *_a, **_k: False)
    monkeypatch.setattr(iron_age, "harvest_wheat_farm", lambda *_a, **_k: False)
    monkeypatch.setattr(iron_age, "visit_known_herd_for_loot", lambda *_a, **_k: False)
    monkeypatch.setattr(iron_age, "eat_until_hunger", lambda *_a, **_k: False)

    with pytest.raises(SurvivalRecoveryRequired):
        handler._stabilize_hunger(client)


def test_stabilize_hunger_does_not_release_worker_at_food_ten(monkeypatch):
    """Gatherers stop at food <= 10, so the phase must not claim readiness."""
    class Transport:
        def dispatch(self, route, _payload):
            if route == "get_state":
                return {"health": 20.0, "food_level": 10}
            return {}

    client = SimpleNamespace(transport=Transport())
    handler = iron_age.FoodAndIronHandler()
    handler.state = SimpleNamespace(custom_data={})
    monkeypatch.setattr(iron_age, "eat_until_hunger", lambda *_a, **_k: False)
    monkeypatch.setattr(iron_age, "visit_known_herd_for_loot", lambda *_a, **_k: False)
    monkeypatch.setattr(iron_age, "acquire_emergency_food", lambda *_a, **_k: False)

    with pytest.raises(SurvivalRecoveryRequired):
        handler._stabilize_hunger(client)
    assert handler._stabilize_hunger_failures == 1


def test_durable_food_persists_only_verified_renewable_source(monkeypatch):
    class FakeState:
        def __init__(self):
            self.custom_data = {}
            self.locations = []

        def add_location(self, category, x, y, z, **kwargs):
            self.locations.append((category, x, y, z, kwargs))

    client = SimpleNamespace()
    state = FakeState()
    handler = iron_age.FoodAndIronHandler()
    handler.state = state
    monkeypatch.setattr(iron_age, "discover_herd", lambda *_a, **_k: (40, 70, 50))
    monkeypatch.setattr(
        iron_age,
        "count_item",
        lambda _client, item: 16 if item == "minecraft:cooked_beef" else 0,
    )
    verified = []

    def verify_source(_client, required_loot, animal_type, **kwargs):
        verified.append((required_loot, animal_type, kwargs))
        return True

    monkeypatch.setattr(iron_age, "visit_known_herd_for_loot", verify_source)

    assert handler._ensure_durable_food(client) is True
    assert verified == [
        (
            {},
            "cow",
            {"preserve_breeding_pair": True, "location": (40, 70, 50)},
        )
    ]
    assert state.custom_data["structures"]["food_source"]["verified"] is True
    assert state.locations[0][0] == "farm"


def test_observed_food_group_persists_generalized_renewable_source():
    class FakeState:
        def __init__(self):
            self.custom_data = {}
            self.locations = []

        def add_location(self, category, x, y, z, **kwargs):
            self.locations.append((category, x, y, z, kwargs))

    handler = iron_age.FoodAndIronHandler()
    handler.state = FakeState()
    handler._remember_renewable_food_source("chicken", (12, 70, -4))

    source = handler.state.custom_data["structures"]["food_source"]
    assert source["animal_type"] == "chicken"
    assert source["raw_item"] == "minecraft:chicken"
    assert source["cooked_item"] == "minecraft:cooked_chicken"
    assert source["verified"] is True
    assert "observed" in handler.state.locations[0][4]["tags"]


def test_initial_smelting_targets_only_starter_kit(monkeypatch):
    inventory = {
        "minecraft:raw_iron": 15,
        "minecraft:iron_ingot": 0,
        "minecraft:furnace": 1,
    }
    requested = []
    monkeypatch.setattr(
        iron_age,
        "count_item",
        lambda _client, item_id: inventory.get(item_id, 0),
    )
    monkeypatch.setattr(iron_age, "find_nearby_block", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        iron_age,
        "ensure_supplies",
        lambda _client, requirements, **_kwargs: (
            requested.append(dict(requirements))
            or SimpleNamespace(success=True)
        ),
    )

    assert iron_age.FoodAndIronHandler()._smelt_iron(SimpleNamespace())


def test_initial_smelt_does_not_defer_raw_iron_for_nearly_broken_pick(monkeypatch):
    counts = {
        "minecraft:iron_ingot": 0,
        "minecraft:raw_iron": 16,
        "minecraft:iron_pickaxe": 1,
        "minecraft:bucket": 1,
        "minecraft:furnace": 1,
    }
    requested = []
    monkeypatch.setattr(
        iron_age, "count_item", lambda _client, item_id: counts.get(item_id, 0)
    )
    monkeypatch.setattr(
        iron_age, "remaining_pickaxe_durability", lambda *_args, **_kwargs: 59
    )
    monkeypatch.setattr(iron_age, "find_nearby_block", lambda *_a, **_k: None)
    monkeypatch.setattr(
        iron_age,
        "ensure_supplies",
        lambda _client, requirements, **_kwargs: (
            requested.append(dict(requirements)) or SimpleNamespace(success=True)
        ),
    )

    assert iron_age.FoodAndIronHandler()._smelt_iron(SimpleNamespace())
    assert requested == [{"minecraft:iron_ingot": 6}]
    assert requested == [{"minecraft:iron_ingot": 6}]


def test_loaded_furnace_starter_batch_unblocks_initial_smelting(monkeypatch):
    inventory = {"minecraft:raw_iron": 0, "minecraft:iron_ingot": 0}
    furnace_pos = (1, 64, 1)
    monkeypatch.setattr(
        iron_age,
        "count_item",
        lambda _client, item_id: inventory.get(item_id, 0),
    )
    monkeypatch.setattr(
        iron_age,
        "find_nearby_block",
        lambda *_args, **_kwargs: furnace_pos,
    )

    def resume(*_args, **kwargs):
        assert kwargs["minimum_output"] == 6
        assert kwargs["timeout"] == 120.0
        inventory["minecraft:iron_ingot"] = 6
        return True

    monkeypatch.setattr(iron_age, "resume_active_furnace", resume)
    monkeypatch.setattr(
        iron_age,
        "ensure_supplies",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("starter batch should already satisfy initial smelting")
        ),
    )

    assert iron_age.FoodAndIronHandler()._smelt_iron(SimpleNamespace())


def _starving_descent_client(food_level):
    class Transport:
        def __init__(self):
            self.calls = []

        def dispatch(self, route, payload):
            self.calls.append((route, payload))
            if route == "get_state":
                return {
                    "block_position": {"x": 10, "y": 60, "z": 5},
                    "health": 20,
                    "food_level": food_level,
                }
            if route == "get_block":
                return {"id": "minecraft:stone"}
            return {}

    transport = Transport()
    return SimpleNamespace(transport=transport), transport


def test_descent_refuses_to_commit_below_regen_threshold_with_no_carried_food(
    monkeypatch, capsys
):
    """Minecraft only regenerates health at food >= 18. Descending under that
    line with nothing edible carried is a one-way trip: the bot cannot heal at
    depth and cannot lift itself back over the line. Confirmed live -- Bot08
    descended at food=9 with an empty larder, arrived around Y=40 already
    wounded, lost every flee attempt (a mineshaft has no terrain-safe escape
    endpoints) and died 9 times in one hour, respawning and walking straight
    back down each time."""
    from baritone_client.common import combat

    client, transport = _starving_descent_client(9)
    monkeypatch.setattr(combat, "eat_until_hunger", lambda *_a, **_k: False)
    monkeypatch.setattr(combat, "_emergency_food_count", lambda _c: 0)
    monkeypatch.setattr(resources.time, "sleep", lambda _s: None)

    assert resources.go_to_y_level(client, -58, timeout=10) is False
    assert "refusing a descent that cannot be healed out of" in capsys.readouterr().out


def test_descent_still_proceeds_below_regen_threshold_when_food_is_carried(
    monkeypatch, capsys
):
    """Carrying food means the bot can lift itself back over the regen line at
    depth, so a low current hunger bar alone must not block the descent."""
    from baritone_client.common import combat

    client, _transport = _starving_descent_client(9)
    monkeypatch.setattr(combat, "eat_until_hunger", lambda *_a, **_k: False)
    monkeypatch.setattr(combat, "_emergency_food_count", lambda _c: 4)
    monkeypatch.setattr(resources.time, "sleep", lambda _s: None)

    resources.go_to_y_level(client, -58, timeout=1)
    output = capsys.readouterr().out
    assert "refusing a descent that cannot be healed out of" not in output
    assert "continuing with fallback food=9" in output
