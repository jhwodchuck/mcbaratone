from pathlib import Path
from types import SimpleNamespace

from baritone_client.common import storage_organizer as organizer
from baritone_client.common.storage_catalog import StorageCatalog
from baritone_client.common.storage_organizer import (
    StorageJob,
    category_for_item,
    plan_storage_job,
)


def test_item_categories_are_broad_and_stable():
    assert category_for_item("minecraft:raw_iron") == "ores"
    assert category_for_item("minecraft:spruce_log") == "wood"
    assert category_for_item("minecraft:bread") == "food"
    assert category_for_item("minecraft:iron_pickaxe") == "tools"
    assert category_for_item("minecraft:leather") == "mob_drops"
    assert category_for_item("minecraft:cobblestone") == "building"


def test_planner_migrates_largest_category_to_managed_destination(tmp_path):
    catalog = StorageCatalog(Path(tmp_path) / "catalog.sqlite3", "world-a")
    source = (1, 64, 1)
    destination = (5, 64, 1)
    catalog.observe_inventory(
        source,
        [
            {"slot": 0, "id": "minecraft:raw_iron", "count": 48},
            {"slot": 1, "id": "minecraft:bread", "count": 12},
        ],
        dimension="minecraft:overworld",
        capacity_slots=27,
        purpose="legacy_storage",
    )
    catalog.observe_inventory(
        destination,
        [],
        dimension="minecraft:overworld",
        capacity_slots=54,
        label="quartermaster:ores",
        purpose="quartermaster:ores",
    )

    job = plan_storage_job(catalog)

    assert job is not None
    assert job.source == source
    assert job.destination == destination
    assert job.category == "ores"


def test_planner_never_touches_player_owned_storage(tmp_path):
    catalog = StorageCatalog(Path(tmp_path) / "catalog.sqlite3", "world-a")
    position = (1, 64, 1)
    catalog.observe_inventory(
        position,
        [{"slot": 0, "id": "minecraft:diamond", "count": 12}],
        dimension="minecraft:overworld",
        capacity_slots=27,
        purpose="legacy_storage",
    )
    catalog.register_container(
        position,
        dimension="minecraft:overworld",
        purpose="legacy_storage",
        metadata={"player_owned": True},
    )

    assert plan_storage_job(catalog) is None


def test_cycle_leases_creates_capacity_and_records_verified_transfer(monkeypatch):
    events = []

    class FakeCatalog:
        def acquire_lease(self, key, owner, **_kwargs):
            events.append(("acquire", key, owner))
            return True

        def release_lease(self, key, owner):
            events.append(("release", key, owner))
            return True

    catalog = FakeCatalog()
    state = SimpleNamespace(custom_data={})
    job = StorageJob(
        "minecraft:overworld",
        "ores",
        "legacy ores",
        source=(1, 64, 1),
    )
    monkeypatch.setattr(organizer, "catalog_for", lambda *_args: catalog)
    monkeypatch.setattr(organizer, "plan_storage_job", lambda _catalog: job)
    monkeypatch.setattr(
        organizer,
        "_create_category_storage",
        lambda *_args: (5, 64, 1),
    )
    monkeypatch.setattr(
        organizer,
        "_transfer_category_batch",
        lambda *_args: (48, 2, True),
    )

    result = organizer.run_quartermaster_cycle(SimpleNamespace(), state)

    assert result.success
    assert result.items_moved == 48
    assert result.double_chests_created == 1
    assert state.custom_data["quartermaster"] == {
        "containers_audited": 1,
        "double_chests_created": 1,
        "free_slots_added": 54,
        "catalog_entries_refreshed": 4,
        "cycles": 1,
        "items_moved": 48,
        "stacks_moved": 2,
        "legacy_chests_emptied": 1,
    }
    assert events[0][0] == "acquire"
    assert events[-1][0] == "release"
