from types import SimpleNamespace

from baritone_client.common import forestry


def test_find_sapling_spots_uses_natural_ground_and_avoids_base_and_trees():
    voxels = []
    for x in range(-12, 13):
        for z in range(-12, 13):
            voxels.append({"x": x, "y": 63, "z": z, "id": "minecraft:grass_block"})
    voxels.append({"x": 8, "y": 64, "z": 0, "id": "minecraft:oak_log"})

    spots = forestry.find_sapling_spots(
        voxels,
        (0, 64, 0),
        maximum=4,
        base_origin=(0, 64, 0),
        base_clearance=7,
    )

    assert len(spots) == 4
    assert all((x * x + z * z) ** 0.5 >= 7 for x, _y, z in spots)
    assert all(((x - 8) ** 2 + z * z) ** 0.5 >= 3 for x, _y, z in spots)


def test_run_wood_cycle_harvests_replants_and_banks(monkeypatch):
    inventories = iter(
        [
            {"minecraft:stone_axe": 1},
            {"minecraft:oak_log": 24, "minecraft:oak_sapling": 5},
            {"minecraft:oak_sapling": 5},
        ]
    )
    client = SimpleNamespace(
        transport=SimpleNamespace(
            dispatch=lambda route, payload=None: {
                "health": 20,
                "food_level": 20,
                "world_time": 1000,
                "dimension": "minecraft:overworld",
            }
            if route == "get_state"
            else {}
        )
    )
    state = SimpleNamespace(custom_data={})
    monkeypatch.setattr(forestry, "get_inventory", lambda _client: next(inventories))
    monkeypatch.setattr(forestry, "gather_wood", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(forestry, "plant_carried_saplings", lambda *_args, **_kwargs: 5)
    monkeypatch.setattr(
        forestry,
        "resolve_storage_location",
        lambda *_args, **_kwargs: (4, 64, 4),
    )
    monkeypatch.setattr(
        forestry,
        "deposit_excess_to_chest",
        lambda *_args, **_kwargs: 1,
    )

    result = forestry.run_wood_cycle(client, state)

    assert result.success
    assert result.logs_harvested == 24
    assert result.saplings_planted == 5
    assert result.logs_banked == 24
    assert state.custom_data["wood_worker"] == {
        "attempts": 1,
        "cycles": 1,
        "logs_harvested": 24,
        "saplings_planted": 5,
        "logs_banked": 24,
        "no_progress": 0,
    }


def test_full_log_backlog_is_banked_before_next_harvest(monkeypatch):
    counts = {"minecraft:oak_log": 100}
    free = {"slots": 0}
    client = SimpleNamespace(
        transport=SimpleNamespace(
            dispatch=lambda route, payload=None: {
                "health": 20,
                "food_level": 20,
                "world_time": 1000,
                "dimension": "minecraft:overworld",
            }
            if route == "get_state"
            else {}
        )
    )
    state = SimpleNamespace(custom_data={})
    monkeypatch.setattr(forestry, "get_inventory", lambda _client: dict(counts))
    monkeypatch.setattr(
        forestry, "free_inventory_slots", lambda _client: free["slots"]
    )

    def bank_backlog(*_args, **_kwargs):
        counts["minecraft:oak_log"] = 0
        free["slots"] = 3
        return True

    def gather(*_args, **_kwargs):
        counts["minecraft:oak_log"] = 24
        return True

    def deposit(*_args, **_kwargs):
        counts["minecraft:oak_log"] = 0
        return 1

    monkeypatch.setattr(forestry, "store_surplus_in_chest", bank_backlog)
    monkeypatch.setattr(forestry, "gather_wood", gather)
    monkeypatch.setattr(
        forestry, "plant_carried_saplings", lambda *_args, **_kwargs: 0
    )
    monkeypatch.setattr(
        forestry,
        "resolve_storage_location",
        lambda *_args, **_kwargs: (4, 64, 4),
    )
    monkeypatch.setattr(forestry, "deposit_excess_to_chest", deposit)

    result = forestry.run_wood_cycle(client, state)

    assert result.success
    assert result.logs_harvested == 24
    assert result.logs_banked == 24


def test_wood_no_progress_rotates_recovery_and_expands_plantation(monkeypatch):
    waypoints = []
    client = SimpleNamespace(
        transport=SimpleNamespace(
            dispatch=lambda route, payload=None: {
                "health": 20, "food_level": 20, "world_time": 1000,
                "dimension": "minecraft:overworld", "position": {"x": 0, "y": 64, "z": 0},
            }
        )
    )
    state = SimpleNamespace(custom_data={"wood_worker": {"escalation_cursor": 1}})
    monkeypatch.setattr(forestry, "get_inventory", lambda _client: {})
    monkeypatch.setattr(forestry, "gather_wood", lambda *_a, **_k: False)
    monkeypatch.setattr(forestry, "ensure_supplies", lambda *_a, **_k: False)
    monkeypatch.setattr(forestry, "plant_carried_saplings", lambda *_a, **_k: 2)
    monkeypatch.setattr(
        forestry,
        "goto",
        lambda _client, x, y, z, **kwargs: waypoints.append((x, y, z, kwargs)) or False,
    )

    expanded = forestry.run_wood_cycle(client, state)
    frontier = forestry.run_wood_cycle(client, state)

    assert expanded.success and expanded.saplings_planted == 2
    assert "expanded verified plantation" in expanded.detail
    assert "attempted bounded tree-search frontier" in frontier.detail
    assert waypoints and (waypoints[0][0] ** 2 + waypoints[0][2] ** 2) ** 0.5 <= 64
    assert waypoints[0][3]["timeout"] == 45
    assert state.custom_data["wood_worker"]["no_progress"] == 2
