"""Focused unit tests for family expedition tracking and escort behavior."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence
from types import SimpleNamespace
from unittest.mock import MagicMock

from baritone_client.operations.family_expedition import (
    EscortDecision,
    EscortFleet,
    EscortWorker,
    ExpeditionConfig,
    FleetTrackerAdapter,
    formation_offsets,
)
from baritone_client.operations.family_expedition import coordinator as coordinator_mod
from baritone_client.operations.family_expedition.escort import (
    desired_position,
    is_safe_hostile_target,
    rotate_offset,
)
from baritone_client.operations.family_expedition.leader_tracker import RconLeaderTracker
from baritone_client.operations.family_expedition.models import PlayerSnapshot, Vec3, validate_player_name


class FakeRconCommandClient:
    def __init__(self, responses: dict[str, str], *, default: str = "0") -> None:
        self.responses = dict(responses)
        self.default = default
        self.commands: list[str] = []

    def command(self, command: str) -> SimpleNamespace:
        self.commands.append(command)
        return SimpleNamespace(body=self.responses.get(command, self.default))


def test_validate_player_name_preserves_case_and_rejects_unsafe():
    safe = validate_player_name(".CommanderRykerH")
    assert safe == ".CommanderRykerH"

    bedrock_case = "." + "A" * 16
    assert validate_player_name(bedrock_case) == bedrock_case

    invalid = [
        "bad name",
        "bad;name",
        "bad\"name",
        "bad\\name",
        "@everyone",
    ]
    for name in invalid:
        try:
            validate_player_name(name)
        except ValueError:
            pass
        else:
            raise AssertionError(f"Expected ValueError for player name: {name}")


def test_rcon_leader_tracker_parses_standard_list_and_entity_telemetry():
    leader = ".CommanderRykerH"
    fake = FakeRconCommandClient(
        {
            "list": "There are 1 of 2 players online: .CommanderRykerH, Bot07",
            f"data get entity {leader} Pos": 'has the following entity data: [12.5d, 64.0d, -31.25d]',
            f"data get entity {leader} Rotation": 'has the following entity data: [45.0f, 180.0f]',
            f"data get entity {leader} Dimension": 'has the following entity data: "minecraft:the_end"',
            f"data get entity {leader} Health": '20.0d',
        }
    )
    tracker = RconLeaderTracker(
        fake,
        leader,
        stale_threshold_seconds=2.0,
        monotonic_clock=lambda: 123.0,
        wall_clock=lambda: 456.0,
    )

    snapshot = tracker.snapshot()
    assert snapshot.online
    assert snapshot.position == Vec3(12.5, 64.0, -31.25)
    assert snapshot.rotation == (45.0, 180.0)
    assert snapshot.dimension == "minecraft:the_end"
    assert snapshot.health == 20.0
    assert tracker.is_fresh(snapshot, now=123.5)
    assert fake.commands == [
        "list",
        f"data get entity {leader} Pos",
        f"data get entity {leader} Rotation",
        f"data get entity {leader} Dimension",
        f"data get entity {leader} Health",
    ]


def test_rcon_leader_tracker_offline_without_entity_reads_on_case_mismatch():
    leader = ".CommanderRykerH"
    fake = FakeRconCommandClient(
        {"list": "There are 1 of 1 players online: .commanderrykerh, Other"}
    )
    tracker = RconLeaderTracker(fake, leader, monotonic_clock=lambda: 100.0, wall_clock=lambda: 200.0)
    snapshot = tracker.snapshot()

    assert not snapshot.online
    assert snapshot.position is None
    assert fake.commands == ["list"]


def test_rcon_leader_tracker_treats_missing_or_stale_critical_telemetry_as_not_fresh():
    leader = ".CommanderRykerH"
    tracker = RconLeaderTracker(
        FakeRconCommandClient({}, default="0"),
        leader,
        stale_threshold_seconds=5.0,
        monotonic_clock=lambda: 10.0,
    )

    missing_position = PlayerSnapshot(
        name=leader,
        online=True,
        position=None,
        dimension="minecraft:the_end",
        observed_at_monotonic=9.0,
    )
    missing_dimension = PlayerSnapshot(
        name=leader,
        online=True,
        position=Vec3(1.0, 2.0, 3.0),
        dimension=None,
        observed_at_monotonic=9.0,
    )
    stale = PlayerSnapshot(
        name=leader,
        online=True,
        position=Vec3(1.0, 2.0, 3.0),
        dimension="minecraft:the_end",
        observed_at_monotonic=0.0,
    )

    assert tracker.is_fresh(missing_position) is False
    assert tracker.is_fresh(missing_dimension) is False
    assert tracker.is_fresh(stale, now=10.0) is False


def test_formation_offsets_are_unique_deterministic_and_eye_carrier_rotation_and_position():
    names = ("Bot19", "Bot07", "Bot16", "Bot15", "Bot18", "Bot17", "Bot18", "Bot15")
    first = formation_offsets(names)
    second = formation_offsets(tuple(reversed(names)))

    assert len(first) == 6
    assert len(first) == len(second) == len(set(first.keys()))
    assert first == second
    assert first["Bot07"].role == "eye_carrier"
    assert len({slot.offset for slot in first.values()}) == 6

    leader = PlayerSnapshot(
        name="Leader",
        online=True,
        position=Vec3(10.0, 64.0, -5.0),
        rotation=(90.0, 0.0),
    )
    slot = first["Bot14"] if "Bot14" in first else first["Bot15"]
    first_target = desired_position(leader, slot)
    second_target = desired_position(leader, slot)
    rotated = rotate_offset(slot, 90.0)
    assert first_target == second_target
    assert first_target == Vec3(
        leader.position.x + rotated.x,
        leader.position.y + rotated.y,
        leader.position.z + rotated.z,
    )


def test_is_safe_hostile_target_accepts_hostile_and_rejects_players_and_protected():
    assert is_safe_hostile_target({"type": "minecraft:zombie", "name": "hostile"}, ("friend",)) is True
    assert is_safe_hostile_target({"type": "player", "name": "hero"}, ("hero",)) is False
    assert is_safe_hostile_target({"type": "minecraft:zombie", "name": "ProtectedFriend"}, ("protectedfriend",)) is False
    assert is_safe_hostile_target({"type": "minecraft:cow"}, ("any",)) is False


@dataclass
class FakeFleetTracker:
    snapshots: dict[str, PlayerSnapshot] = field(default_factory=dict)
    entities: dict[str, Sequence[Mapping[str, Any]]] = field(default_factory=dict)
    cancelled: list[str] = field(default_factory=list)

    def get_snapshot(self, name: str) -> PlayerSnapshot | None:
        return self.snapshots.get(name)

    def get_entities(self, name: str) -> Sequence[Mapping[str, Any]]:
        return self.entities.get(name, ())

    def cancel(self, name: str) -> None:
        self.cancelled.append(name)


def _base_snapshot(
    *,
    name: str = "Bot07",
    position: Vec3 = Vec3(0.0, 64.0, 0.0),
    rotation: tuple[float, float] = (0.0, 0.0),
    dimension: str = "minecraft:overworld",
    health: float = 20.0,
    food: int = 20,
    game_mode: str = "survival",
    online: bool = True,
    observed_at: float = 10.0,
    entities: int = 0,
) -> PlayerSnapshot:
    return PlayerSnapshot(
        name=name,
        online=online,
        position=position,
        rotation=rotation,
        dimension=dimension,
        health=health,
        food=food,
        game_mode=game_mode,
        observed_at_monotonic=observed_at,
    )


def test_escort_worker_missing_or_stale_leader_holds_and_cancels_no_follow_dispatch():
    slot = formation_offsets(("Bot07", "Bot16"))["Bot07"]
    tracker = FakeFleetTracker(
        snapshots={
            "Bot07": _base_snapshot(name="Bot07"),
            ".CommanderRykerH": _base_snapshot(name=".CommanderRykerH", observed_at=0.0),
        }
    )
    navigator = MagicMock(return_value=True)
    defense = MagicMock(return_value=False)
    worker = EscortWorker(
        "Bot07",
        ".CommanderRykerH",
        slot,
        tracker,
        navigator,
        defense,
        clock=lambda: 10.0,
        stale_seconds=5.0,
    )

    tick = worker.step()
    assert tick.decision == EscortDecision.HOLD
    assert "stale" in tick.reason
    assert tracker.cancelled == ["Bot07"]
    navigator.assert_not_called()
    defense.assert_not_called()


def test_escort_worker_unsafe_bot_returns_abort_or_hold_without_navigation():
    slot = formation_offsets(("Bot07", "Bot16"))["Bot07"]
    bot_dead = _base_snapshot(name="Bot07", health=0.0)
    bot_not_survival = _base_snapshot(name="Bot07", health=20.0, food=5, game_mode="creative")
    leader = _base_snapshot(name=".CommanderRykerH")
    navigator = MagicMock(return_value=True)
    defense = MagicMock(return_value=False)

    for bad_snapshot in (bot_dead, bot_not_survival):
        tracker = FakeFleetTracker(
            snapshots={"Bot07": bad_snapshot, ".CommanderRykerH": leader}
        )
        worker = EscortWorker(
            "Bot07",
            ".CommanderRykerH",
            slot,
            tracker,
            navigator,
            defense,
            clock=lambda: 10.0,
            stale_seconds=5.0,
            min_health=12.0,
            min_food=10,
        )
        tick = worker.step()
        assert tick.decision in {EscortDecision.ABORT, EscortDecision.HOLD}
        assert tracker.cancelled == ["Bot07"]
        navigator.assert_not_called()
        defense.assert_not_called()


def test_escort_worker_dimension_mismatch_is_hold():
    slot = formation_offsets(("Bot07", "Bot16"))["Bot07"]
    tracker = FakeFleetTracker(
        snapshots={
            "Bot07": _base_snapshot(name="Bot07", dimension="minecraft:the_nether"),
            ".CommanderRykerH": _base_snapshot(name=".CommanderRykerH", dimension="minecraft:the_end"),
        }
    )
    navigator = MagicMock(return_value=True)
    defense = MagicMock(return_value=False)
    worker = EscortWorker(
        "Bot07",
        ".CommanderRykerH",
        slot,
        tracker,
        navigator,
        defense,
        clock=lambda: 10.0,
        stale_seconds=5.0,
    )

    tick = worker.step()
    assert tick.decision == EscortDecision.HOLD
    assert "dimension mismatch" in tick.reason
    assert tracker.cancelled == ["Bot07"]
    navigator.assert_not_called()


def test_fleet_hold_cancels_all_workers():
    slots = formation_offsets(("Bot07", "Bot16"))
    tracker = FakeFleetTracker(
        snapshots={
            "Bot07": _base_snapshot(name="Bot07", observed_at=0.0),
            "Bot16": _base_snapshot(name="Bot16"),
            ".CommanderRykerH": _base_snapshot(name=".CommanderRykerH", observed_at=0.0),
        }
    )
    navigator = MagicMock(return_value=True)
    defense = MagicMock(return_value=False)

    worker_a = EscortWorker(
        "Bot07",
        ".CommanderRykerH",
        slots["Bot07"],
        tracker,
        navigator,
        defense,
        clock=lambda: 10.0,
        stale_seconds=5.0,
    )
    worker_b = EscortWorker(
        "Bot16",
        ".CommanderRykerH",
        slots["Bot16"],
        tracker,
        navigator,
        defense,
        clock=lambda: 10.0,
        stale_seconds=5.0,
    )
    fleet = EscortFleet((worker_a, worker_b))
    tick = fleet.tick()

    assert tick.decision == EscortDecision.HOLD
    assert set(tracker.cancelled) == {"Bot07", "Bot16"}
    navigator.assert_not_called()


def test_short_navigation_timeout_accepts_verified_progress(monkeypatch):
    config = ExpeditionConfig()
    client = SimpleNamespace(_last_navigation_survival_abort=False)
    snapshots = iter(
        (
            _base_snapshot(name="Bot07", position=Vec3(0.0, 64.0, 0.0)),
            _base_snapshot(name="Bot07", position=Vec3(2.0, 64.0, 0.0)),
        )
    )

    class ProgressTracker:
        leader_tracker = SimpleNamespace(leader_name=".CommanderRykerH")
        bot_clients = {name: client for name in config.bot_names}
        clock = staticmethod(lambda: 10.0)

        @staticmethod
        def get_snapshot(name):
            if name == "Bot07":
                return next(snapshots)
            return _base_snapshot(name=name)

        @staticmethod
        def get_entities(_name):
            return ()

        @staticmethod
        def cancel(_name):
            return None

    monkeypatch.setattr(coordinator_mod, "goto", lambda *_args, **_kwargs: False)
    fleet = coordinator_mod.build_escort_fleet(
        ProgressTracker(),
        config,
        protected_names=(".CommanderRykerH",),
    )
    worker = next(item for item in fleet.workers if item.bot_name == "Bot07")

    assert worker.navigator("Bot07", Vec3(10.0, 64.0, 0.0), 12.0) is True


def test_fleet_tracker_rcon_exception_returns_offline_snapshot():
    class BrokenLeaderTracker:
        leader_name = ".CommanderRykerH"

        @staticmethod
        def snapshot():
            raise RuntimeError("RCON unavailable")

    tracker = FleetTrackerAdapter(BrokenLeaderTracker(), {}, clock=lambda: 42.0)
    snapshot = tracker.refresh_leader()

    assert snapshot.name == ".CommanderRykerH"
    assert snapshot.online is False
    assert snapshot.observed_at_monotonic == 42.0
