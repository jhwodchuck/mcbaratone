from types import SimpleNamespace

import pytest

from baritone_client.common import emergency_food, enclosed_workstation, inventory, resources, survival_farm
from baritone_client.common.tasks import PlayerDeathDetected


@pytest.fixture
def room(monkeypatch):
    blocks = {}
    for x in range(98, 105):
        for z in range(98, 105):
            for y in (64, 67):
                blocks[x, y, z] = {"id": "minecraft:cobblestone"}
            if x in (98, 104) or z in (98, 104):
                for y in (65, 66):
                    blocks[x, y, z] = {"id": "minecraft:cobblestone"}
    live = {"position": {"x": 99.5, "y": 64.9375, "z": 99.5},
            "block_position": {"x": 99, "y": 64, "z": 99},
            "health": 10, "food_level": 17}
    calls = []
    client = SimpleNamespace(_protected_home_anchor=(101, 65, 101),
                             transport=SimpleNamespace(dispatch=lambda r, p:
                                calls.append((r, p)) or dict(live)))
    monkeypatch.setattr("baritone_client.common.farming._block_data", lambda _c, x, y, z:
                        blocks.get((x, y, z), {"id": "minecraft:air"}))
    return client, live, blocks, calls


def test_complete_enclosure_is_observed_from_plot_edge(room):
    client, live, blocks, _calls = room
    assert survival_farm._wait_enclosure(client, live)
    blocks[104, 66, 101] = {"id": "minecraft:air"}
    assert not survival_farm._wait_enclosure(client, live)


def test_inside_table_wins_over_closer_outside_table(room):
    client, _live, blocks, _calls = room
    blocks[103, 65, 103] = {"id": "minecraft:crafting_table"}
    blocks[97, 65, 99] = {"id": "minecraft:crafting_table"}
    assert enclosed_workstation.sheltered_bread_table(client) == (True, (103, 65, 103))


def test_no_inside_table_does_not_authorize_outside_search(room):
    client, _live, blocks, _calls = room
    blocks[97, 65, 99] = {"id": "minecraft:crafting_table"}
    assert enclosed_workstation.sheltered_bread_table(client) == (True, None)


def test_unknown_room_block_fails_closed(room):
    client, _live, blocks, _calls = room
    blocks[98, 65, 99] = {}
    assert enclosed_workstation.sheltered_bread_table(client) == (True, None)


def test_unenclosed_bootstrap_keeps_existing_behavior(room):
    client, _live, blocks, _calls = room
    blocks.clear()
    assert enclosed_workstation.sheltered_bread_table(client) == (False, None)


def test_unknown_player_position_fails_closed(room):
    client, live, _blocks, _calls = room
    live.clear()
    assert enclosed_workstation.sheltered_bread_table(client) == (True, None)


def test_table_query_death_propagates(room, monkeypatch):
    client, _live, _blocks, _calls = room
    monkeypatch.setattr(survival_farm, "_enclosure_bounds", lambda *_a, **_k:
                        (_ for _ in ()).throw(PlayerDeathDetected("dead")))
    with pytest.raises(PlayerDeathDetected):
        enclosed_workstation.sheltered_bread_table(client)


def test_emergency_bread_passes_scoped_table(room, monkeypatch):
    client, _live, blocks, _calls = room
    blocks[103, 65, 103] = {"id": "minecraft:crafting_table"}
    crafted = []
    monkeypatch.setattr(emergency_food, "emergency_food_count", lambda _c: int(bool(crafted)))
    monkeypatch.setattr("baritone_client.common.inventory.count_item", lambda _c, item:
                        6 if item == "minecraft:wheat" else 0)
    monkeypatch.setattr(resources, "_craft_with_table", lambda *a, **k:
                        crafted.append((a, k)) or True)
    assert emergency_food.craft_emergency_bread_from_carried_wheat(client)
    assert crafted[0][1] == {"table_pos": (103, 65, 103)}


def test_missing_sheltered_table_never_calls_generic_crafting(room, monkeypatch):
    client, _live, _blocks, _calls = room
    monkeypatch.setattr(emergency_food, "emergency_food_count", lambda _c: 0)
    monkeypatch.setattr("baritone_client.common.inventory.count_item", lambda *_a: 6)
    monkeypatch.setattr(resources, "_craft_with_table", lambda *_a, **_k:
                        pytest.fail("must not search outside shelter"))
    assert not emergency_food.craft_emergency_bread_from_carried_wheat(client)


def test_scoped_table_open_failure_has_no_placement_fallback(monkeypatch):
    client = object()
    opened = []
    monkeypatch.setattr(resources, "count_item", lambda *_a: 0)
    monkeypatch.setattr(resources, "ensure_tool_sticks", lambda *_a: True)
    monkeypatch.setattr(resources, "ensure_stone_material", lambda *_a: True)
    monkeypatch.setattr("baritone_client.common.base.open_crafting_table", lambda *a:
                        opened.append(a[1:]) or False)
    monkeypatch.setattr("baritone_client.common.base.place_crafting_table", lambda *_a:
                        pytest.fail("cannot replace a scoped failed workstation"))
    assert not resources._craft_with_table(client, "minecraft:bread", 2, table_pos=(103, 65, 103))
    assert opened == [(103, 65, 103)]


def test_food_worker_bread_stops_before_dispatch_if_sheltered_table_refused(room, monkeypatch):
    client, _live, blocks, calls = room
    blocks[103, 65, 103] = {"id": "minecraft:crafting_table"}
    opened = []
    monkeypatch.setattr("baritone_client.common.harness_ops.ensure_crafting_table_open",
                        lambda _c, table_pos=None: opened.append(table_pos) or False)
    assert not inventory.craft(client, "minecraft:bread", 2)
    assert opened == [(103, 65, 103)]
    assert not any(route in {"craft", "auto_craft", "goto", "attack_block"} for route, _ in calls)


def test_food_worker_can_open_the_scoped_table(room):
    client, _live, blocks, _calls = room
    blocks[103, 65, 103] = {"id": "minecraft:crafting_table"}
    opened = []
    assert enclosed_workstation.prepare_sheltered_bread_craft(
        client, lambda _c, table_pos=None: opened.append(table_pos) or True
    )
    assert opened == [(103, 65, 103)]
