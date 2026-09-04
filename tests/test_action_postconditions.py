from types import SimpleNamespace

import pytest

from baritone_client.common import automation_utils, furnace_recovery


@pytest.mark.parametrize("response", [None, {}, {"moved": False}, {"moved": True},
                                      {"moved": True, "postcondition_verified": False}])
def test_refuel_requires_observed_effect(response):
    client = SimpleNamespace(transport=SimpleNamespace(dispatch=lambda *_a: response))
    screen = {"slots": [{"slot": 8, "id": "minecraft:oak_log", "count": 2}]}
    assert not furnace_recovery._load_carried_fuel(client, screen)


@pytest.mark.parametrize("screen", [{}, {"sync_id": 1, "slots": []},
                                   {"sync_id": 1, "type": "InventoryMenu", "slots": [{"slot": n} for n in range(3)]}])
def test_missing_or_wrong_furnace_menu_is_not_empty_completed_batch(screen):
    client = SimpleNamespace(transport=SimpleNamespace(dispatch=lambda *_a: screen))
    with pytest.raises(RuntimeError):
        furnace_recovery._verified_furnace_slots(client, [None])


def test_successful_placement_response_without_block_does_not_pass(monkeypatch):
    clock = SimpleNamespace(now=0.0)
    monkeypatch.setattr(automation_utils, "time", SimpleNamespace(
        monotonic=lambda: clock.now,
        sleep=lambda seconds: setattr(clock, "now", clock.now + seconds)))
    monkeypatch.setattr("baritone_client.common.inventory.select_item", lambda *_a, **_k: True)
    calls = []
    def dispatch(route, payload):
        calls.append(route)
        return {"id": "minecraft:air"} if route == "get_block" else {"placed": True, "status": "ok"}
    client = SimpleNamespace(transport=SimpleNamespace(dispatch=dispatch))
    assert not automation_utils.place_block(client, 1, 2, 3, "minecraft:obsidian")
    assert calls.count("place_block") == 1


def test_timed_out_placement_is_reconciled_without_replay(monkeypatch):
    monkeypatch.setattr(automation_utils.time, "sleep", lambda _s: None)
    monkeypatch.setattr("baritone_client.common.inventory.select_item", lambda *_a, **_k: True)
    world = {"block": "minecraft:air", "mutations": 0}
    def dispatch(route, payload):
        if route == "get_block":
            return {"id": world["block"]}
        world["mutations"] += 1
        world["block"] = "minecraft:obsidian"
        raise TimeoutError("response lost after placement")
    client = SimpleNamespace(transport=SimpleNamespace(dispatch=dispatch))
    assert automation_utils.place_block(client, 1, 2, 3, "minecraft:obsidian")
    assert world["mutations"] == 1
