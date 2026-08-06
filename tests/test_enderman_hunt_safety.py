"""Safety regressions for the Enderman pearl hunt.

Bot07 died three times in one session (server log: "was shot by Skeleton"
every time) while running ``hunt_endermen``. The hunt provoked Endermen by
looking at them, never fought them, never checked health/food, and never
noticed it had died -- it kept logging pearl progress for twelve minutes
against a corpse. These tests lock in the repair.
"""

from types import SimpleNamespace

import pytest

from baritone_client.common import enderman_hunt
from baritone_client.common.tasks import PlayerDeathDetected


class HuntTransport:
    """Minimal bridge stub for the Enderman hunt loop."""

    def __init__(self, *, endermen=None, difficulty="easy"):
        self.calls = []
        self.position = {"x": 0, "y": 40, "z": 0}
        self.difficulty = difficulty
        self.health = 20.0
        self.food = 20
        self.endermen = endermen if endermen is not None else []

    def dispatch(self, route, payload=None, **_kwargs):
        payload = payload or {}
        self.calls.append((route, dict(payload)))
        if route == "get_state":
            return {
                "dimension": "minecraft:overworld",
                "block_position": dict(self.position),
                "difficulty": self.difficulty,
                "health": self.health,
                "food": self.food,
            }
        if route == "find_blocks":
            return {
                "found": [
                    {"x": 5, "y": 40, "z": 5, "block": "minecraft:stone"},
                ]
            }
        if route == "get_block":
            return {"id": "minecraft:cave_air"}
        if route == "get_entities":
            return {"entities": list(self.endermen)}
        if route == "goto":
            self.position = {
                "x": payload.get("x", 0),
                "y": payload.get("y", 40),
                "z": payload.get("z", 0),
            }
            return {"started": True}
        return {}

    def routes(self):
        return [route for route, _payload in self.calls]


@pytest.fixture
def frozen_clock(monkeypatch):
    clock = {"now": 0.0}

    def fake_time():
        clock["now"] += 1.0
        return clock["now"]

    monkeypatch.setattr(enderman_hunt.time, "time", fake_time)
    monkeypatch.setattr(enderman_hunt.time, "sleep", lambda _s: None)
    return clock


FULL_IRON_ARMOR = {
    "helmet": "minecraft:iron_helmet",
    "chestplate": "minecraft:iron_chestplate",
    "leggings": "minecraft:iron_leggings",
    "boots": "minecraft:iron_boots",
}


@pytest.fixture
def quiet_recovery(monkeypatch):
    """Neutralize the recovery helpers so each test drives one behaviour.

    Armor defaults to a full set: the hunt refuses to start without it, so
    every test not specifically about the armor gate would otherwise exit
    early and assert nothing.
    """
    monkeypatch.setattr(enderman_hunt, "defend_or_flee", lambda *_a, **_k: False)
    monkeypatch.setattr(enderman_hunt, "heal_if_needed", lambda *_a, **_k: False)
    monkeypatch.setattr(enderman_hunt, "eat_until_hunger", lambda *_a, **_k: True)
    monkeypatch.setattr(enderman_hunt, "ensure_alive", lambda *_a, **_k: False)
    monkeypatch.setattr(
        enderman_hunt, "get_equipped_armor", lambda _c: dict(FULL_IRON_ARMOR)
    )


def test_hunt_runs_hostile_defense_every_poll(
    monkeypatch, frozen_clock, quiet_recovery
):
    """Skeletons in the cave -- not Endermen -- are what actually killed Bot07."""
    transport = HuntTransport()
    client = SimpleNamespace(transport=transport)
    monkeypatch.setattr(enderman_hunt, "count_item", lambda *_a: 0)

    defended = []
    monkeypatch.setattr(
        enderman_hunt, "defend_or_flee", lambda *_a, **_k: defended.append(True) or False
    )

    enderman_hunt.hunt_endermen(client, target_count=12, timeout=20)

    assert defended, "hunt never ran hostile defense; cave skeletons go unanswered"


def test_a_dead_bot_stops_hunting(monkeypatch, frozen_clock, quiet_recovery):
    """The old loop logged pearl progress for 12 minutes against a corpse."""
    transport = HuntTransport()
    client = SimpleNamespace(transport=transport)
    monkeypatch.setattr(enderman_hunt, "count_item", lambda *_a: 0)

    def die(*_a, **_k):
        raise PlayerDeathDetected("died mid-hunt")

    monkeypatch.setattr(enderman_hunt, "defend_or_flee", die)

    fallback = []
    monkeypatch.setattr(
        enderman_hunt, "hunt_mobs", lambda *_a, **_k: fallback.append(True) or {}
    )

    with pytest.raises(PlayerDeathDetected):
        enderman_hunt.hunt_endermen(client, target_count=12, timeout=20)

    assert not fallback, "death was swallowed and the hunt restarted itself"


def test_provoked_endermen_are_actually_fought(
    monkeypatch, frozen_clock, quiet_recovery
):
    """Looking at an Enderman aggroes it; the old loop then just slept."""
    enderman = {"id": 42, "position": {"x": 3, "y": 40, "z": 3}}
    transport = HuntTransport(endermen=[enderman])
    client = SimpleNamespace(transport=transport)
    monkeypatch.setattr(enderman_hunt, "count_item", lambda *_a: 0)

    fought = []
    monkeypatch.setattr(
        enderman_hunt,
        "safe_combat",
        lambda _c, target_id, **_k: fought.append(target_id) or True,
    )

    enderman_hunt.hunt_endermen(client, target_count=12, timeout=30)

    assert 42 in fought, "hunt provoked an Enderman without ever fighting it"
    assert "look_at" in transport.routes()


def test_hunt_stops_below_the_regeneration_floor(
    monkeypatch, frozen_clock, quiet_recovery
):
    """Food under 18 means no natural regen, so the fight cannot be survived."""
    transport = HuntTransport()
    transport.food = 6
    transport.health = 10.0
    client = SimpleNamespace(transport=transport)
    monkeypatch.setattr(enderman_hunt, "count_item", lambda *_a: 0)
    monkeypatch.setattr(enderman_hunt, "eat_until_hunger", lambda *_a, **_k: False)

    engaged = []
    monkeypatch.setattr(
        enderman_hunt, "_engage_enderman", lambda *_a, **_k: engaged.append(True) or False
    )

    enderman_hunt.hunt_endermen(client, target_count=12, timeout=30)

    assert not engaged, "hunt kept provoking Endermen while unable to heal"


def test_barren_location_is_abandoned_instead_of_burning_the_timeout(
    monkeypatch, frozen_clock, quiet_recovery
):
    """Bot07 spent 12+ minutes at 0/12 pearls in a cave with no Endermen."""
    transport = HuntTransport(endermen=[])
    client = SimpleNamespace(transport=transport)
    monkeypatch.setattr(enderman_hunt, "count_item", lambda *_a: 0)

    timeout = 100000
    enderman_hunt.hunt_endermen(client, target_count=12, timeout=timeout)

    # The stub clock advances one second per call, so an unabandoned hunt runs
    # until it exhausts the timeout. Returning far short of it is the proof.
    assert frozen_clock["now"] < timeout / 10, (
        "hunt never abandoned a barren area; it burned its whole timeout at 0 pearls"
    )


def test_travel_legs_are_defended_not_raw_goto(
    monkeypatch, frozen_clock, quiet_recovery
):
    """Bot07's fatal 18 damage was taken *while walking* to a spawn pocket.

    The loop-top safety tick could not see it: a raw ``goto`` plus a fixed
    sleep left a 33-second undefended gap, and the next check found 2.0 health.
    Travel must go through the supervised navigator instead.
    """
    transport = HuntTransport()
    client = SimpleNamespace(transport=transport)
    monkeypatch.setattr(enderman_hunt, "count_item", lambda *_a: 0)

    supervised = []
    monkeypatch.setattr(
        enderman_hunt,
        "_supervised_goto",
        lambda _c, x, y, z, **_k: supervised.append((x, y, z)) or True,
    )

    enderman_hunt.hunt_endermen(client, target_count=12, timeout=30)

    assert supervised, "hunt travelled without the supervised navigator"
    raw_gotos = [r for r in transport.routes() if r == "goto"]
    assert not raw_gotos, (
        f"hunt still issues unsupervised raw goto ({len(raw_gotos)}); "
        "the travel leg would be undefended again"
    )


def test_peaceful_difficulty_bails_out_immediately(
    monkeypatch, frozen_clock, quiet_recovery
):
    """Endermen do not spawn on Peaceful; hunting is pure wasted runtime."""
    transport = HuntTransport(difficulty="peaceful")
    client = SimpleNamespace(transport=transport)
    monkeypatch.setattr(enderman_hunt, "count_item", lambda *_a: 3)

    assert enderman_hunt.hunt_endermen(client, target_count=12, timeout=30) == 3
    assert "goto" not in transport.routes()


def test_naked_bot_never_provokes_an_enderman(
    monkeypatch, frozen_clock, quiet_recovery
):
    """The fleet's 20 deaths all carried "only 0/4 armor pieces" telemetry.

    Provoking a 40-health mob with no armor is unsurvivable regardless of how
    good the combat loop is, so the hunt must refuse before it starts.
    """
    enderman = {"id": 7, "position": {"x": 2, "y": 40, "z": 2}}
    transport = HuntTransport(endermen=[enderman])
    client = SimpleNamespace(transport=transport)
    monkeypatch.setattr(enderman_hunt, "count_item", lambda *_a: 0)
    monkeypatch.setattr(enderman_hunt, "get_equipped_armor", lambda _c: {})

    engaged = []
    monkeypatch.setattr(
        enderman_hunt,
        "_engage_enderman",
        lambda *_a, **_k: engaged.append(True) or False,
    )

    enderman_hunt.hunt_endermen(client, target_count=12, timeout=30)

    assert not engaged, "an unarmored bot still provoked an Enderman"


def test_armored_bot_may_hunt(monkeypatch, frozen_clock, quiet_recovery):
    """The gate must not block a properly equipped bot, or nothing progresses."""
    enderman = {"id": 7, "position": {"x": 2, "y": 40, "z": 2}}
    transport = HuntTransport(endermen=[enderman])
    client = SimpleNamespace(transport=transport)
    monkeypatch.setattr(enderman_hunt, "count_item", lambda *_a: 0)
    monkeypatch.setattr(enderman_hunt, "_supervised_goto", lambda *_a, **_k: True)

    engaged = []
    monkeypatch.setattr(
        enderman_hunt,
        "_engage_enderman",
        lambda *_a, **_k: engaged.append(True) or True,
    )

    enderman_hunt.hunt_endermen(client, target_count=12, timeout=30)

    assert engaged, "a fully armored bot was blocked from hunting"
