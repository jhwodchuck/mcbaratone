from types import SimpleNamespace

import pytest

from baritone_client.common import combat
from baritone_client.common.tunnel_travel_defense import (
    CorridorStepDefense,
    corridor_snapshot,
)


ORIGIN = (0, 70, 0)
TARGET = (1, 69, 0)


def player(**overrides):
    value = {
        "position": {"x": 1.2, "y": 69.8, "z": 0.5},
        "block_position": {"x": 1, "y": 69, "z": 0},
        "velocity": {"x": 0.2, "y": 0.42, "z": 0},
        "health": 20.0,
        "is_dead": False,
        "dimension": "minecraft:overworld",
        "fall_distance": 0.2,
        "is_on_ground": False,
        "entity_id": 99,
    }
    value.update(overrides)
    return value


def creeper(**overrides):
    value = {
        "id": 7,
        "type": "minecraft:creeper",
        "distance": 7.5,
        "position": {"x": 8.7, "y": 69.8, "z": 0.5},
        "velocity": {"x": 0.0, "y": 0.0, "z": 0.0},
        "can_see_player": False,
        "is_aggressive": False,
    }
    value.update(overrides)
    return value


def snapshot(*entities, **player_overrides):
    return {
        "snapshot_version": 1,
        "tick": 123,
        "count": len(entities),
        "skipped_count": 0,
        "player": player(**player_overrides),
        "entities": list(entities),
    }


def block_reader(missing=()):
    missing = set(missing)
    supports = {(0, 69, 0), (1, 68, 0)}

    def get_block(cell):
        if cell in missing:
            return "minecraft:air"
        if cell in supports:
            return "minecraft:stone"
        return "minecraft:air"

    return get_block


def test_airborne_one_step_corridor_suppresses_only_stationary_occluded_creeper():
    observed = snapshot(creeper())

    filtered = corridor_snapshot(observed, ORIGIN, TARGET)

    assert filtered is not None
    assert filtered["entities"] == []
    assert filtered["count"] == 1  # raw frame metadata is not rewritten
    assert filtered["player"]["is_on_ground"] is False
    assert filtered["player"]["velocity"] == {"x": 0.2, "y": 0.42, "z": 0}
    # The general threat evaluator still sees this mid-air observation as a threat.
    assert combat.assess_threats(observed["entities"], observed["player"])


@pytest.mark.parametrize(
    "changes",
    [
        {"can_see_player": True},
        {"is_aggressive": True},
        {"target_id": 99},
        {"type": "other:creeper"},
        {"is_attacking": None},
        {"angry_at_player": "false"},
        {"velocity": {"x": 0.1, "y": 0, "z": 0}},
        {"distance": 6.0, "position": {"x": 7.2, "y": 69.8, "z": 0.5}},
    ],
    ids=["visible", "aggressive", "targeted", "non-vanilla-creeper", "unknown-attacking",
         "malformed-anger", "moving", "near-boundary"],
)
def test_actionable_or_near_creeper_is_never_suppressed(changes):
    mob = creeper(**changes)

    filtered = corridor_snapshot(snapshot(mob), ORIGIN, TARGET)

    assert filtered is not None
    assert filtered["entities"] == [mob]


def test_only_the_qualified_creeper_is_removed_and_other_mobs_remain():
    safe = creeper()
    zombie = {
        "id": 8,
        "type": "minecraft:zombie",
        "distance": 8.0,
        "position": {"x": 9.2, "y": 69.8, "z": 0.5},
        "velocity": {"x": 0, "y": 0, "z": 0},
        "can_see_player": False,
        "is_aggressive": False,
    }

    filtered = corridor_snapshot(snapshot(safe, zombie), ORIGIN, TARGET)

    assert filtered is not None and filtered["entities"] == [zombie]


@pytest.mark.parametrize(
    "changes",
    [
        {"position": {"x": 40, "y": 69.8, "z": 0.5}},
        {"fall_distance": 1.01},
        {"fall_distance": float("nan")},
        {"velocity": {"x": 0, "y": float("inf"), "z": 0}},
        {"velocity": {"x": True, "y": 0, "z": 0}},
        {"position": {"x": "1.2", "y": 69.8, "z": 0.5}},
        {"health": float("nan")},
        {"health": 0},
        {"is_dead": True},
        {"dimension": "minecraft:the_nether"},
        {"dimension": "not_overworld"},
        {"is_on_ground": None},
        {"block_position": {"x": 2, "y": 69, "z": 0}},
    ],
    ids=["off-corridor", "excess-fall", "unknown-fall", "bad-velocity", "boolean-velocity",
         "string-position", "bad-health", "dead-health", "dead", "wrong-dimension",
         "dimension-alias", "unknown-ground", "pose-mismatch"],
)
def test_incomplete_or_abnormal_player_proof_fails_closed(changes):
    assert corridor_snapshot(snapshot(creeper(), **changes), ORIGIN, TARGET) is None


@pytest.mark.parametrize(
    "changes",
    [
        {"snapshot_version": 2},
        {"tick": -1},
        {"count": 0},
        {"skipped_count": 1},
        {"entities": None},
    ],
    ids=["version", "tick", "count", "skipped", "missing-entities"],
)
def test_incomplete_atomic_snapshot_fails_closed(changes):
    observed = snapshot(creeper())
    observed.update(changes)

    assert corridor_snapshot(observed, ORIGIN, TARGET) is None


@pytest.mark.parametrize(
    "changes",
    [
        {"distance": float("nan")},
        {"position": {}},
        {"velocity": {"x": 0, "y": 0, "z": float("nan")}},
        {"id": True},
    ],
    ids=["bad-distance", "missing-position", "bad-velocity", "bad-id"],
)
def test_malformed_entity_rows_fail_closed(changes):
    assert corridor_snapshot(snapshot(creeper(**changes)), ORIGIN, TARGET) is None


def test_callback_rechecks_fresh_visibility_and_suppression_is_not_sticky(monkeypatch):
    client = SimpleNamespace(transport=SimpleNamespace(dispatch=lambda *_a: {"value": "false"}))
    responses = iter([snapshot(creeper()), snapshot(creeper(can_see_player=True))])
    defended = []
    monkeypatch.setattr(combat, "_get_combat_snapshot", lambda *_a, **_k: next(responses))
    monkeypatch.setattr(
        combat, "defend_or_flee",
        lambda _client, *, observed_snapshot: defended.append(observed_snapshot) or False,
    )
    callback = CorridorStepDefense(client, ORIGIN, TARGET, block_reader())

    assert callback.preflight()
    assert callback() is False
    assert callback() is False
    assert defended[0]["entities"] == []
    assert defended[1]["entities"][0]["can_see_player"] is True


@pytest.mark.parametrize("failure", ["allow_break", "support"])
def test_callback_aborts_if_travel_contract_changes_mid_hop(monkeypatch, failure):
    setting = {"value": "false"}
    client = SimpleNamespace(transport=SimpleNamespace(dispatch=lambda *_a: setting))
    defended = []
    monkeypatch.setattr(combat, "_get_combat_snapshot", lambda *_a, **_k: snapshot())
    monkeypatch.setattr(combat, "defend_or_flee", lambda *_a, **_k: defended.append(True))
    get_block = block_reader()
    callback = CorridorStepDefense(client, ORIGIN, TARGET, get_block)
    assert callback.preflight()
    if failure == "support":
        callback.get_block = block_reader(missing={(1, 68, 0)})
    else:
        setting["value"] = "true"

    assert callback() is True
    assert callback.abort_reason
    assert not defended


def test_callback_aborts_when_snapshot_is_unknown(monkeypatch):
    client = SimpleNamespace(transport=SimpleNamespace(dispatch=lambda *_a: {"value": "false"}))
    monkeypatch.setattr(combat, "_get_combat_snapshot", lambda *_a, **_k: None)
    callback = CorridorStepDefense(client, ORIGIN, TARGET, block_reader())

    assert callback.preflight()
    assert callback() is True
    assert "observation" in callback.abort_reason


def test_callback_aborts_when_defense_processing_fails(monkeypatch):
    client = SimpleNamespace(transport=SimpleNamespace(dispatch=lambda *_a: {"value": "false"}))
    monkeypatch.setattr(combat, "_get_combat_snapshot", lambda *_a, **_k: snapshot())
    monkeypatch.setattr(combat, "defend_or_flee", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError()))
    callback = CorridorStepDefense(client, ORIGIN, TARGET, block_reader())

    assert callback.preflight()
    assert callback() is True
    assert "failed" in callback.abort_reason
