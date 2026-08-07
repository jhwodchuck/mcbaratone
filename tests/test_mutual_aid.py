import sqlite3
from types import SimpleNamespace

import pytest

from baritone_client.automator.adaptive_scheduler import GameSignals
from baritone_client.automator import aid_response
from baritone_client.automator.end_readiness import FleetRole
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


def _armed_responder(monkeypatch):
    monkeypatch.setattr(aid_response, "expedition_is_too_dangerous", lambda _client: False)
    monkeypatch.setattr(aid_response, "equip_best_weapon", lambda _client: True)
    return SimpleNamespace()


def _clear_hostiles_request(tmp_path, catalog, *, position=(0, 64, 0), ttl=200):
    requester = _state(tmp_path, "Bot16")
    return raise_request(
        requester, "clear_hostiles", "4 hostile(s) near", 60, ttl,
        now=100.0, catalog=catalog, position=position,
    )


def test_only_one_competing_responder_wins_the_aid_lease(tmp_path, monkeypatch):
    catalog = StorageCatalog(tmp_path / "catalog.sqlite3", "world-a")
    request = _clear_hostiles_request(tmp_path, catalog)
    client = _armed_responder(monkeypatch)
    signals = _signals(position=(10, 64, 0))
    first = _state(tmp_path, "Bot17")
    second = _state(tmp_path, "Bot18")
    first_opportunity = aid_response.select_clear_hostiles_opportunity(
        client, first, signals, role=FleetRole.BALANCED, now=110.0, catalog=catalog
    )
    second_opportunity = aid_response.select_clear_hostiles_opportunity(
        client, second, signals, role=FleetRole.BALANCED, now=110.0, catalog=catalog
    )

    assert first_opportunity is not None and second_opportunity is not None
    wins = [
        aid_response.try_claim_clear_hostiles(
            client, state, signals, opportunity, now=110.0, catalog=catalog
        )
        for state, opportunity in ((first, first_opportunity), (second, second_opportunity))
    ]

    assert sum(item is not None for item in wins) == 1
    assert wins[0]["request_id"] == request["request_id"]


@pytest.mark.parametrize("signals", [_signals(health=5.0), _signals(food=2)])
def test_unfit_responder_never_claims_clear_hostiles(tmp_path, monkeypatch, signals):
    catalog = StorageCatalog(tmp_path / "catalog.sqlite3", "world-a")
    _clear_hostiles_request(tmp_path, catalog)
    client = _armed_responder(monkeypatch)
    responder = _state(tmp_path, "Bot17")

    assert aid_response.select_clear_hostiles_opportunity(
        client, responder, signals, role=FleetRole.BALANCED, now=110.0, catalog=catalog
    ) is None


def test_unarmed_responder_never_claims_clear_hostiles(tmp_path, monkeypatch):
    catalog = StorageCatalog(tmp_path / "catalog.sqlite3", "world-a")
    _clear_hostiles_request(tmp_path, catalog)
    monkeypatch.setattr(aid_response, "expedition_is_too_dangerous", lambda _client: False)
    monkeypatch.setattr(aid_response, "equip_best_weapon", lambda _client: False)

    assert aid_response.select_clear_hostiles_opportunity(
        SimpleNamespace(), _state(tmp_path, "Bot17"), _signals(), role=FleetRole.BALANCED,
        now=110.0, catalog=catalog,
    ) is None


def test_safe_night_responder_can_claim_hostile_aid(tmp_path, monkeypatch):
    catalog = StorageCatalog(tmp_path / "catalog.sqlite3", "world-a")
    _clear_hostiles_request(tmp_path, catalog)

    opportunity = aid_response.select_clear_hostiles_opportunity(
        _armed_responder(monkeypatch),
        _state(tmp_path, "Bot17"),
        _signals(position=(10, 64, 0), world_time=18000),
        role=FleetRole.BALANCED,
        now=110.0,
        catalog=catalog,
    )

    assert opportunity is not None


def test_lapsed_aid_lease_is_claimable_by_another_responder(tmp_path, monkeypatch):
    catalog = StorageCatalog(tmp_path / "catalog.sqlite3", "world-a")
    request = _clear_hostiles_request(tmp_path, catalog)
    assert catalog.acquire_lease(f"aid:{request['request_id']}", "Bot17", ttl_seconds=10, now=100.0)
    client = _armed_responder(monkeypatch)
    responder = _state(tmp_path, "Bot18")
    opportunity = aid_response.select_clear_hostiles_opportunity(
        client, responder, _signals(position=(10, 64, 0)), role=FleetRole.BALANCED, now=111.0, catalog=catalog
    )

    assert opportunity is not None
    assert aid_response.try_claim_clear_hostiles(
        client, responder, _signals(position=(10, 64, 0)), opportunity, now=111.0, catalog=catalog
    ) is not None


def test_arrival_without_a_hostile_drop_is_not_fulfilled(tmp_path, monkeypatch):
    catalog = StorageCatalog(tmp_path / "catalog.sqlite3", "world-a")
    request = _clear_hostiles_request(tmp_path, catalog)
    client = _armed_responder(monkeypatch)
    responder = _state(tmp_path, "Bot17")
    opportunity = aid_response.select_clear_hostiles_opportunity(
        client, responder, _signals(position=(10, 64, 0)), role=FleetRole.BALANCED, now=110.0, catalog=catalog
    )
    hostile = {"id": 7, "type": "minecraft:zombie"}
    monkeypatch.setattr(aid_response, "goto", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(aid_response, "defend_or_flee", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(aid_response, "safe_combat", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        aid_response, "scan_for_threats", lambda *_args, **_kwargs: [hostile] * 4
    )

    assert opportunity is not None
    result = aid_response.run_clear_hostiles_response(
        client, responder, _signals(position=(10, 64, 0)), opportunity, now=110.0, catalog=catalog
    )

    assert not result.fulfilled
    stored = catalog.list_aid_requests(open_only=False)
    assert stored[0]["resolution"] is None
    assert catalog.acquire_lease(f"aid:{request['request_id']}", "Bot18", now=111.0)
    with catalog._connect() as db:
        events = [row["event_type"] for row in db.execute("SELECT event_type FROM storage_events")]
    assert "aid_request_abandoned" in events


def test_measured_hostile_drop_fulfils_the_request(tmp_path, monkeypatch):
    catalog = StorageCatalog(tmp_path / "catalog.sqlite3", "world-a")
    _clear_hostiles_request(tmp_path, catalog)
    client = _armed_responder(monkeypatch)
    responder = _state(tmp_path, "Bot17")
    opportunity = aid_response.select_clear_hostiles_opportunity(
        client, responder, _signals(position=(10, 64, 0)), role=FleetRole.BALANCED, now=110.0, catalog=catalog
    )
    hostile = {"id": 7, "type": "minecraft:zombie"}
    observations = iter(([hostile] * 4, [hostile] * 4, [hostile]))
    monkeypatch.setattr(aid_response, "goto", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(aid_response, "defend_or_flee", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(aid_response, "safe_combat", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        aid_response, "scan_for_threats", lambda *_args, **_kwargs: next(observations)
    )

    assert opportunity is not None
    result = aid_response.run_clear_hostiles_response(
        client, responder, _signals(position=(10, 64, 0)), opportunity, now=110.0, catalog=catalog
    )

    assert result.fulfilled
    assert (result.before_hostiles, result.after_hostiles) == (4, 1)
    assert catalog.list_aid_requests(open_only=False)[0]["resolution"] == "fulfilled"


def test_distant_clear_hostiles_request_is_not_claimed(tmp_path, monkeypatch):
    catalog = StorageCatalog(tmp_path / "catalog.sqlite3", "world-a")
    _clear_hostiles_request(tmp_path, catalog, position=(129, 64, 0))

    assert aid_response.select_clear_hostiles_opportunity(
        _armed_responder(monkeypatch), _state(tmp_path, "Bot17"), _signals(),
        role=FleetRole.BALANCED, now=110.0, catalog=catalog,
    ) is None


def test_role_hold_gets_the_responder_score_preference(tmp_path, monkeypatch):
    catalog = StorageCatalog(tmp_path / "catalog.sqlite3", "world-a")
    _clear_hostiles_request(tmp_path, catalog)
    client = _armed_responder(monkeypatch)
    responder = _state(tmp_path, "Bot17")
    signals = _signals(position=(10, 64, 0))
    ordinary = aid_response.select_clear_hostiles_opportunity(
        client, responder, signals, FleetRole.VILLAGE_FOOD,
        role_held=False, now=110.0, catalog=catalog,
    )
    held = aid_response.select_clear_hostiles_opportunity(
        client, responder, signals, FleetRole.VILLAGE_FOOD,
        role_held=True, now=110.0, catalog=catalog,
    )

    assert ordinary is not None and held is not None
    assert held.score > ordinary.score


def test_requester_keeps_publishing_recovery_state_without_waiting_for_a_responder(tmp_path, monkeypatch):
    state = _state(tmp_path, "Bot16")
    state.custom_data["camp_holds"] = {"streak": 6}
    catalog = StorageCatalog(tmp_path / "catalog.sqlite3", "world-a")

    first = publish_requests(state, _signals(nearby_hostiles=4), now=100.0, catalog=catalog)
    second = publish_requests(state, _signals(nearby_hostiles=3), now=101.0, catalog=catalog)

    assert [request["request_id"] for request in first] == [request["request_id"] for request in second]
    assert catalog.list_aid_requests()[0]["detail"] == "3 hostile(s) near"
    assert aid_response.select_clear_hostiles_opportunity(
        _armed_responder(monkeypatch), state, _signals(nearby_hostiles=0),
        FleetRole.BALANCED, now=101.0, catalog=catalog,
    ) is None
    with catalog._connect() as db:
        assert db.execute("SELECT COUNT(*) AS count FROM resource_leases").fetchone()["count"] == 0
