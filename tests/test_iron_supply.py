from types import SimpleNamespace

from baritone_client.automator import iron_scheduler
from baritone_client.common import iron_supply


def test_run_iron_cycle_mines_smelts_banks_and_credits_only_new_iron(monkeypatch):
    inventories = iter(
        [
            {"minecraft:stone_pickaxe": 1, "minecraft:iron_ingot": 2},
            {
                "minecraft:stone_pickaxe": 1,
                "minecraft:raw_iron": 24,
                "minecraft:iron_ingot": 2,
            },
            {"minecraft:stone_pickaxe": 1, "minecraft:iron_ingot": 26},
            {"minecraft:stone_pickaxe": 1},
        ]
    )
    client = SimpleNamespace(
        transport=SimpleNamespace(
            dispatch=lambda route, payload=None: {
                "health": 20,
                "food_level": 20,
                "dimension": "minecraft:overworld",
            }
            if route == "get_state"
            else {}
        )
    )
    state = SimpleNamespace(custom_data={})
    monkeypatch.setattr(
        iron_supply,
        "get_inventory",
        lambda _client: next(inventories),
    )
    monkeypatch.setattr(iron_supply, "gather_ores", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        iron_supply,
        "_smelt_with_furnace",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        iron_supply,
        "resolve_storage_location",
        lambda *_args, **_kwargs: (4, 64, 4),
    )
    monkeypatch.setattr(
        iron_supply,
        "deposit_excess_to_chest",
        lambda *_args, **_kwargs: 1,
    )

    result = iron_supply.run_iron_cycle(client, state)

    assert result.success
    assert result.raw_iron_mined == 24
    assert result.iron_ingots_smelted == 24
    assert result.iron_ingots_banked == 26
    assert result.iron_banked == 24
    assert state.custom_data["iron_worker"] == {
        "attempts": 1,
        "cycles": 1,
        "raw_iron_mined": 24,
        "iron_ingots_smelted": 24,
        "iron_banked": 24,
        "no_progress": 0,
    }


def test_iron_no_progress_rotates_prerequisite_recovery_then_frontier(monkeypatch):
    waypoints = []
    client = SimpleNamespace(
        transport=SimpleNamespace(
            dispatch=lambda route, payload=None: {
                "health": 20, "food_level": 20, "dimension": "minecraft:overworld",
                "position": {"x": 0, "y": 64, "z": 0},
            }
        )
    )
    state = SimpleNamespace(custom_data={})
    monkeypatch.setattr(iron_supply, "get_inventory", lambda _client: {})
    monkeypatch.setattr(iron_supply, "gather_ores", lambda *_a, **_k: False)
    monkeypatch.setattr(iron_supply, "ensure_supplies", lambda *_a, **_k: False)
    monkeypatch.setattr(iron_supply, "resolve_storage_location", lambda *_a, **_k: None)
    monkeypatch.setattr(
        iron_supply,
        "goto",
        lambda _client, x, y, z, **kwargs: waypoints.append((x, y, z, kwargs)) or False,
    )

    first = iron_supply.run_iron_cycle(client, state)
    second = iron_supply.run_iron_cycle(client, state)
    third = iron_supply.run_iron_cycle(client, state)

    assert not first.success and "pickaxe" in first.detail
    assert "fuel and storage" in second.detail
    assert "attempted bounded mining frontier" in third.detail
    assert waypoints and (waypoints[0][0] ** 2 + (waypoints[0][1] - 64) ** 2 + waypoints[0][2] ** 2) ** 0.5 <= 64
    assert waypoints[0][3]["tolerance"] == 3.0
    worker = state.custom_data["iron_worker"]
    assert worker["no_progress"] == 3
    assert worker.get("iron_banked", 0) == 0


def test_run_iron_cycle_requires_survival_margin(monkeypatch):
    client = SimpleNamespace(
        transport=SimpleNamespace(
            dispatch=lambda *_args, **_kwargs: {
                "health": 15,
                "food_level": 20,
                "dimension": "minecraft:overworld",
            }
        )
    )
    monkeypatch.setattr(
        iron_supply,
        "gather_ores",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("unsafe mining should not start")
        ),
    )

    result = iron_supply.run_iron_cycle(client, SimpleNamespace(custom_data={}))

    assert not result.success
    assert "survival margin" in result.detail


def test_scheduler_adapter_persists_total_banked_progress(monkeypatch):
    monkeypatch.setattr(
        iron_scheduler,
        "run_iron_cycle",
        lambda *_args, **_kwargs: iron_supply.IronCycleResult(
            True,
            "banked 24 iron",
            iron_banked=24,
            total_iron_banked=24,
        ),
    )
    runtime = {}

    result = iron_scheduler.run_scheduled_iron_cycle(
        SimpleNamespace(),
        SimpleNamespace(),
        runtime,
    )

    assert result == (True, "banked 24 iron", 0, 24)
    assert runtime["iron_units_banked"] == 24
