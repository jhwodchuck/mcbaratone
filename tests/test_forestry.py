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
        "cycles": 1,
        "logs_harvested": 24,
        "saplings_planted": 5,
        "logs_banked": 24,
    }
