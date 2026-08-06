import sqlite3
from types import SimpleNamespace

import pytest

from baritone_client.automator.adaptive_scheduler import GameSignals
from baritone_client.automator.mutual_aid import (
    expire_stale,
    open_requests,
    publish_requests,
    raise_request,
)
from baritone_client.common.storage_catalog import StorageCatalog


def _state(tmp_path, bot="Bot16"):
    controller = tmp_path / "runs" / "headlessmc" / bot / "controller"
    controller.mkdir(parents=True)
    return SimpleNamespace(
        checkpoint_dir=controller,
        custom_data={},
        bound_world_identity=None,
        _last_position=(-180, 110, -410),
    )


def _signals(**overrides):
    values = {
        "observed": True,
        "entities_observed": True,
        "dimension": "minecraft:overworld",
        "health": 20.0,
        "food": 20,
        "world_time": 1000,
        "position": (-180, 110, -410),
        "nearby_hostiles": 0,
    }
    values.update(overrides)
    return GameSignals(**values)


def test_repeated_food_trigger_updates_one_open_request_at_storage_layer(tmp_path):
    state = _state(tmp_path)
    catalog = StorageCatalog(tmp_path / "catalog.sqlite3", "world-a")

    first = publish_requests(state, _signals(food=0), now=100.0, catalog=catalog)
    second = publish_requests(state, _signals(food=2), now=110.0, catalog=catalog)

    requests = open_requests(catalog=catalog)
    assert len(first) == len(second) == len(requests) == 1
    assert requests[0]["request_id"] == first[0]["request_id"]
    assert requests[0]["detail"] == "food 2<14"
    assert requests[0]["created_at"] == 100.0
    assert requests[0]["expires_at"] == 290.0
    with catalog._connect() as db, pytest.raises(sqlite3.IntegrityError):
        db.execute(
            """INSERT INTO aid_requests(
                   world_id, request_id, requester, kind, detail, dimension,
                   urgency, created_at, expires_at
               ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ("world-a", "second-open-food", "Bot16", "food", "duplicate",
             "minecraft:overworld", 90, 111.0, 291.0),
        )


def test_different_blockers_publish_separate_requests_with_verbatim_detail(tmp_path):
    state = _state(tmp_path)
    state.custom_data["camp_holds"] = {"streak": 6}
    catalog = StorageCatalog(tmp_path / "catalog.sqlite3", "world-a")

    publish_requests(state, _signals(food=0), now=100.0, catalog=catalog)
    publish_requests(
        state, _signals(nearby_hostiles=8), now=101.0, catalog=catalog
    )

    requests = {request["kind"]: request for request in open_requests(catalog=catalog)}
    assert set(requests) == {"food", "clear_hostiles"}
    assert requests["clear_hostiles"]["detail"] == "8 hostile(s) near"


def test_urgent_health_and_work_escalation_publish_distinct_need_facts(tmp_path):
    state = _state(tmp_path)
    state.custom_data = {
        "camp_holds": {"streak": 6},
        "productive_work": {"escalation_level": 3, "last_detail": "no wheat delta"},
    }
    catalog = StorageCatalog(tmp_path / "catalog.sqlite3", "world-a")

    publish_requests(
        state, _signals(food=0, health=7.0, nearby_hostiles=8),
        now=100.0, catalog=catalog,
    )

    requests = {request["kind"]: request for request in open_requests(catalog=catalog)}
    assert set(requests) == {"food", "rescue", "clear_hostiles", "supply"}
    assert requests["rescue"]["detail"] == "health 7.0<16"
    assert requests["supply"]["detail"] == "no wheat delta"


def test_expiry_is_idempotent_and_open_requests_reflect_inserts_updates_and_expiry(tmp_path):
    state = _state(tmp_path)
    catalog = StorageCatalog(tmp_path / "catalog.sqlite3", "world-a")

    raise_request(
        state, "food", "food 0<14", 90, 10, now=100.0, catalog=catalog
    )
    raise_request(
        state, "supply", "no bread delta", 50, 60, now=101.0, catalog=catalog
    )
    updated = raise_request(
        state, "supply", "no wheat delta", 50, 60, now=102.0, catalog=catalog
    )

    assert {request["kind"] for request in open_requests(catalog=catalog)} == {
        "food", "supply"
    }
    assert updated["detail"] == "no wheat delta"
    assert expire_stale(110.0, catalog=catalog) == 1
    assert expire_stale(110.0, catalog=catalog) == 0
    assert [request["kind"] for request in open_requests(catalog=catalog)] == ["supply"]
    assert catalog.list_aid_requests(open_only=False)[0]["resolution"] == "expired"
