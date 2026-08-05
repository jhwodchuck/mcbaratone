"""Tests for combat telemetry lifecycle and runtime hooks."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

import baritone_client.common.combat_telemetry as telemetry


class Client:
    def __init__(self) -> None:
        self.transport = SimpleNamespace()


class SlottedClient:
    __slots__ = ("transport",)

    def __init__(self) -> None:
        self.transport = object()


def emitted(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    monkeypatch.setattr(
        telemetry,
        "emit_event",
        lambda event, **fields: events.append({"event": event, **fields}),
    )
    return events


def test_lifecycle_uses_stable_encounter_id(monkeypatch: pytest.MonkeyPatch) -> None:
    events = emitted(monkeypatch)
    recorder = telemetry.get_combat_telemetry(
        Client(), snapshot_interval_seconds=0
    )

    assert recorder.begin_encounter(
        source="test", player={"health": 20}, threats=[{"type": "zombie"}]
    )
    encounter_id = recorder.encounter_id
    assert not recorder.begin_encounter(source="nested")
    recorder.record_decision("evade", reason="low health")
    recorder.record_action("flee", outcome="verified")
    recorder.record_snapshot(
        player={"health": 18}, threats=[{"id": 4, "type": "zombie"}]
    )
    recorder.end_encounter(outcome="escaped", player={"health": 18})

    assert [event["event"] for event in events] == [
        "combat_start",
        "combat_decision",
        "combat_action",
        "combat_damage",
        "combat_snapshot",
        "combat_snapshot",
        "combat_end",
    ]
    assert all(event["encounter_id"] == encounter_id for event in events)
    assert events[-1]["encounter_active"] is False
    assert recorder.encounter_id is None


def test_damage_bypasses_snapshot_throttle(monkeypatch: pytest.MonkeyPatch) -> None:
    events = emitted(monkeypatch)
    recorder = telemetry.get_combat_telemetry(
        Client(), snapshot_interval_seconds=60
    )
    recorder.begin_encounter(source="test", player={"health": 20})

    recorder.record_snapshot(player={"health": 20})
    recorder.record_snapshot(player={"health": 17.5})

    assert [event["event"] for event in events].count("combat_snapshot") == 0
    damage = next(event for event in events if event["event"] == "combat_damage")
    assert damage["damage"] == 2.5


def test_target_health_loss_is_observed_as_damage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events = emitted(monkeypatch)
    recorder = telemetry.get_combat_telemetry(Client())
    recorder.begin_encounter(
        source="test",
        target_id=9,
        threats=[{"id": 9, "health": 20}, {"id": 10, "health": 20}],
    )

    recorder.record_snapshot(
        threats=[{"id": 9, "health": 14.5}, {"id": 10, "health": 12}]
    )

    target_events = [
        event for event in events if event["event"] == "combat_target_damage"
    ]
    assert len(target_events) == 1
    damage = target_events[0]
    assert damage["target_id"] == "9"
    assert damage["damage"] == 5.5


def test_payloads_are_semantic_and_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    events = emitted(monkeypatch)
    recorder = telemetry.get_combat_telemetry(Client())
    recorder.begin_encounter(
        source="test",
        player={"health": 20, "ignored": "x" * 500},
        threats=[
            {"id": index, "type": "z" * 500, "ignored": object()}
            for index in range(20)
        ],
    )

    event = events[0]
    assert event["player"] == {"health": 20}
    assert len(event["threats"]) == telemetry.MAX_THREATS
    assert len(event["threats"][0]["type"]) == telemetry.MAX_TEXT_LENGTH
    assert "ignored" not in event["threats"][0]


def test_recorder_is_attached_per_client(monkeypatch: pytest.MonkeyPatch) -> None:
    emitted(monkeypatch)
    first = Client()
    second = Client()

    assert telemetry.get_combat_telemetry(first) is telemetry.get_combat_telemetry(first)
    assert telemetry.get_combat_telemetry(first) is not telemetry.get_combat_telemetry(second)


def test_slotted_test_double_does_not_make_telemetry_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    emitted(monkeypatch)

    recorder = telemetry.get_combat_telemetry(SlottedClient())
    recorder.begin_encounter(source="test")
    recorder.end_encounter(outcome="complete")

    assert recorder.encounter_id is None


def test_telemetry_failures_never_escape(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*_args: Any, **_kwargs: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(telemetry, "emit_event", fail)
    recorder = telemetry.get_combat_telemetry(Client())

    recorder.begin_encounter(source="test", player={"health": 20})
    recorder.record_decision("engage")
    recorder.record_action("attack")
    recorder.record_snapshot(player={"health": 10})
    recorder.end_encounter(outcome="complete")

    assert recorder.encounter_id is None


def test_safe_combat_decorator_preserves_result_and_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events = emitted(monkeypatch)
    client = Client()

    @telemetry.trace_safe_combat
    def succeeds(_client: Any, _target_id: int) -> bool:
        return True

    @telemetry.trace_safe_combat
    def fails(_client: Any, _target_id: int) -> bool:
        raise RuntimeError("attack failed")

    assert succeeds(client, 7) is True
    with pytest.raises(RuntimeError, match="attack failed"):
        fails(client, 8)

    endings = [event for event in events if event["event"] == "combat_end"]
    assert [event["outcome"] for event in endings] == [
        "target_killed",
        "error",
    ]


def test_explicit_purpose_change_separates_encounters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events = emitted(monkeypatch)
    recorder = telemetry.get_combat_telemetry(Client())

    assert recorder.begin_encounter(
        source="defend_or_flee", purpose="hostile_defense", target_id=7
    )
    assert recorder.begin_encounter(
        source="hunt_mobs",
        purpose="passive_hunt",
        target_id=42,
        target_metadata={"id": 42, "type": "minecraft:cow"},
    )

    endings = [event for event in events if event["event"] == "combat_end"]
    starts = [event for event in events if event["event"] == "combat_start"]
    assert endings[-1]["outcome"] == "interrupted"
    assert endings[-1]["reason"] == "purpose_changed"
    assert [event["purpose"] for event in starts] == [
        "hostile_defense",
        "passive_hunt",
    ]
    assert starts[-1]["target"]["type"] == "minecraft:cow"
