import json

from baritone_client.common.storage_catalog import (
    StorageCatalog,
    catalog_for,
    observe_open_container,
    seed_from_state,
)


class _Transport:
    host = "localhost"
    port = 5602

    def dispatch(self, route, payload):
        assert route == "get_state"
        return {"dimension": "minecraft:overworld"}


class _Client:
    transport = _Transport()


class _State:
    def __init__(self, checkpoint_dir):
        self.checkpoint_dir = checkpoint_dir
        self.bound_world_identity = None
        self.custom_data = {}

    def get_locations(self, category):
        return {category: self.custom_data.get("locations", {}).get(category, [])}


def _slot(slot, item_id="minecraft:air", count=0, max_count=64, damage=0):
    return {
        "slot": slot,
        "id": item_id,
        "count": count,
        "max_count": max_count,
        "damage": damage,
    }


def test_catalog_replaces_stale_slot_snapshot(tmp_path):
    catalog = StorageCatalog(tmp_path / "storage_catalog.sqlite3", "world-a")
    position = (10, 64, -4)
    catalog.observe_inventory(
        position,
        [_slot(0, "minecraft:cobblestone", 64), _slot(1, "minecraft:iron_ingot", 12)],
        dimension="minecraft:overworld",
        capacity_slots=27,
    )

    assert catalog.find_item("minecraft:cobblestone")[0]["count"] == 64
    assert catalog.find_item("minecraft:iron_ingot")[0]["count"] == 12

    catalog.observe_inventory(
        position,
        [_slot(0, "minecraft:cobblestone", 20)],
        dimension="minecraft:overworld",
        capacity_slots=27,
    )

    assert catalog.find_item("minecraft:cobblestone")[0]["count"] == 20
    assert catalog.find_item("minecraft:iron_ingot") == []
    assert catalog.inventory_totals() == {"minecraft:cobblestone": 20}
    container = catalog.list_containers()[0]
    assert container["capacity_slots"] == 27
    assert container["occupied_slots"] == 1
    assert container["total_items"] == 20


def test_open_screen_records_only_container_owned_slots(tmp_path):
    checkpoint = {
        "world_identity": {"stable_hash": "mcbaratone-world-v1:test"}
    }
    (tmp_path / "spawn_to_dragon_checkpoint.json").write_text(
        json.dumps(checkpoint), encoding="utf-8"
    )
    state = _State(tmp_path)
    slots = [_slot(index) for index in range(63)]
    slots[2] = _slot(2, "minecraft:diamond", 5)
    # Slot 27 starts the player's inventory for a 63-slot single chest screen.
    slots[27] = _slot(27, "minecraft:diamond", 40)

    totals = observe_open_container(
        _Client(),
        (1, 65, 1),
        {"data": {"total_slots": 63, "slots": slots}},
        state=state,
        label="ores",
        purpose="valuable_materials",
    )

    assert totals == {"minecraft:diamond": 5}
    catalog = catalog_for(_Client(), state)
    assert catalog.world_id == "mcbaratone-world-v1:test"
    assert catalog.find_item("minecraft:diamond")[0]["count"] == 5
    container = catalog.list_containers()[0]
    assert container["label"] == "ores"
    assert container["purpose"] == "valuable_materials"


def test_worlds_do_not_share_inventory_rows(tmp_path):
    path = tmp_path / "storage_catalog.sqlite3"
    world_a = StorageCatalog(path, "world-a")
    world_b = StorageCatalog(path, "world-b")
    world_a.observe_inventory(
        (0, 64, 0),
        [_slot(0, "minecraft:emerald", 9)],
        dimension="minecraft:overworld",
        capacity_slots=27,
    )

    assert world_a.find_item("minecraft:emerald")[0]["count"] == 9
    assert world_b.find_item("minecraft:emerald") == []


def test_fleet_bots_share_catalog_without_crossing_worlds(tmp_path):
    fleet = tmp_path / "runs" / "headlessmc"
    bot07 = fleet / "Bot07" / "controller"
    bot16 = fleet / "Bot16" / "controller"
    bot07.mkdir(parents=True)
    bot16.mkdir(parents=True)

    world_id = "mcbaratone-world-v1:shared"
    first = StorageCatalog(
        fleet / "shared" / "storage_catalog.sqlite3",
        world_id,
    )
    first.register_container(
        (-184, 106, -392),
        dimension="minecraft:overworld",
        purpose="fleet_depot",
    )

    from baritone_client.common.storage_catalog import catalog_from_run_dir

    second = catalog_from_run_dir(bot16, world_id)
    isolated_world = catalog_from_run_dir(bot07, "mcbaratone-world-v1:other")

    assert second.path == fleet / "shared" / "storage_catalog.sqlite3"
    assert [(row["x"], row["y"], row["z"]) for row in second.list_containers()] == [
        (-184, 106, -392)
    ]
    assert isolated_world.list_containers() == []


def test_checkpoint_landmarks_seed_without_claiming_inventory(tmp_path):
    state = _State(tmp_path)
    state.custom_data = {
        "locations": {
            "chest": [
                {"x": 3, "y": 65, "z": 8, "dimension": "overworld"}
            ]
        },
        "structures": {
            "starter_house": {"supply_chest": [20, 70, -5]}
        },
    }

    assert seed_from_state(_Client(), state) == 2
    containers = catalog_for(_Client(), state).list_containers()
    assert {(row["x"], row["y"], row["z"]) for row in containers} == {
        (3, 65, 8),
        (20, 70, -5),
    }
    assert all(row["last_inventory_scan"] is None for row in containers)


def test_missing_container_is_hidden_and_not_revived_by_checkpoint_seed(tmp_path):
    state = _State(tmp_path)
    state.custom_data = {
        "locations": {
            "chest": [
                {"x": 3, "y": 65, "z": 8, "dimension": "overworld"}
            ]
        }
    }
    seed_from_state(_Client(), state)
    catalog = catalog_for(_Client(), state)
    catalog.observe_inventory(
        (3, 65, 8),
        [_slot(0, "minecraft:bread", 8)],
        dimension="minecraft:overworld",
        capacity_slots=27,
    )
    catalog.mark_missing((3, 65, 8), dimension="minecraft:overworld")

    assert catalog.list_containers() == []
    assert catalog.find_item("minecraft:bread") == []
    assert catalog.item_count("minecraft:bread") == 0
    assert catalog.inventory_totals() == {}

    # Restart seeding imports the checkpoint landmark as merely ``known``;
    # that must not override stronger live evidence that the chest is gone.
    seed_from_state(_Client(), state)
    assert catalog.list_containers() == []


def test_barrels_and_shulkers_count_as_storage_containers():
    """The catalog stores barrels, so testing for "chest" alone rejects them.

    Live 2026-08-01: (-182,106,-392) was a *verified barrel*, and every bot
    walked ~19m to it and logged "expected chest is missing", then moved on to
    the next entry -- forever.
    """
    from baritone_client.common.inventory import _is_storage_container

    assert _is_storage_container("minecraft:chest")
    assert _is_storage_container("minecraft:trapped_chest")
    assert _is_storage_container("minecraft:barrel")
    assert _is_storage_container("minecraft:shulker_box")
    assert _is_storage_container("minecraft:red_shulker_box")


def test_non_containers_are_rejected():
    from baritone_client.common.inventory import _is_storage_container

    assert not _is_storage_container("minecraft:air")
    assert not _is_storage_container("minecraft:stone")
    assert not _is_storage_container("")
    assert not _is_storage_container(None)


def test_missing_container_is_forgotten_so_it_is_not_re_walked(monkeypatch):
    """A confirmed-absent coordinate must leave the catalog.

    Otherwise it is offered again next pass, and with a shared fleet catalog
    every bot repeats the same fruitless tour.
    """
    from baritone_client.common import inventory

    marked = []

    class FakeCatalog:
        def mark_missing(self, position, *, dimension):
            marked.append((position, dimension))

    class Transport:
        def dispatch(self, route, _payload=None):
            if route == "get_state":
                return {"dimension": "minecraft:overworld"}
            return {}

    monkeypatch.setattr(
        "baritone_client.common.storage_catalog.catalog_for",
        lambda _client, _state: FakeCatalog(),
    )

    client = type("C", (), {"transport": Transport()})()
    inventory._forget_missing_container(client, (-165, 106, -415), None)

    assert marked == [((-165, 106, -415), "minecraft:overworld")]


def test_forgetting_never_raises_into_the_caller(monkeypatch):
    """Catalog trouble must not abort the storage operation itself."""
    from baritone_client.common import inventory

    def boom(_client, _state):
        raise RuntimeError("catalog locked")

    monkeypatch.setattr(
        "baritone_client.common.storage_catalog.catalog_for", boom
    )

    class Transport:
        def dispatch(self, _route, _payload=None):
            return {"dimension": "minecraft:overworld"}

    client = type("C", (), {"transport": Transport()})()
    inventory._forget_missing_container(client, (1, 2, 3), None)  # must not raise


def test_old_chest_only_test_rejected_a_real_barrel():
    """Pins the defect itself rather than the new helper's existence.

    The previous check was literally `if "chest" not in block`. Against the
    barrel the catalog had stored as *verified*, that is False, so the bot
    declared a missing chest and walked away from a container that was really
    there. If these two ever agree again, the regression is back.
    """
    from baritone_client.common.inventory import _is_storage_container

    barrel = "minecraft:barrel"
    assert ("chest" in barrel) is False  # the old predicate said "missing"
    assert _is_storage_container(barrel) is True  # the new one finds it


def test_resource_lease_is_exclusive_and_recovers_after_expiry(tmp_path):
    catalog = StorageCatalog(tmp_path / "catalog.sqlite3", "world-a")

    assert catalog.acquire_lease("quartermaster:cycle", "Bot07", now=100.0)
    assert not catalog.acquire_lease("quartermaster:cycle", "Bot15", now=110.0)
    assert catalog.acquire_lease("quartermaster:cycle", "Bot15", now=230.0)
    assert not catalog.release_lease("quartermaster:cycle", "Bot07")
    assert catalog.release_lease("quartermaster:cycle", "Bot15")


def test_container_inventory_is_scoped_to_exact_container(tmp_path):
    catalog = StorageCatalog(tmp_path / "catalog.sqlite3", "world-a")
    catalog.observe_inventory(
        (1, 64, 1),
        [{"slot": 0, "id": "minecraft:iron_ingot", "count": 12}],
        dimension="minecraft:overworld",
        capacity_slots=27,
    )
    catalog.observe_inventory(
        (2, 64, 1),
        [{"slot": 0, "id": "minecraft:iron_ingot", "count": 7}],
        dimension="minecraft:overworld",
        capacity_slots=27,
    )

    assert catalog.container_inventory(
        (1, 64, 1), dimension="minecraft:overworld"
    ) == {"minecraft:iron_ingot": 12}
