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
