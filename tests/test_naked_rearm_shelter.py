"""A naked rebuild must dig in, not go chop wood in the dark.

After a grave is written off, `_bootstrap_starter_pickaxe` re-crafts a wooden
pickaxe by calling `ensure_supplies(..., timeout=300)`. That walks the bot
outdoors for up to five minutes. It ran two seconds after a respawn, wearing
nothing and carrying nothing.

dragon-a and dragon-b died this way six times across 2026-08-14/15, each run
ending on the supervisor's terminal safety circuit ("manual repair required"),
which is not automatically recoverable. The trace was identical every time:

    RECOVERY: grave lost; bootstrapping a wooden pickaxe from scratch...
    GATHER: night boundary reached (time=16359); seeking shelter
    Daylight safety: unarmed at night; waiting about 381s for dawn...
    DEFENSE: evade minecraft:zombie at 1.5m (only 0/4 armor pieces; score=94.1)
    FLEE: candidate routes did not increase separation
    Daylight safety: player died; yielding to death recovery

Note lines 2-3: the night gate already fired. `wait_for_safe_daylight` is not
the cure, it is where the bot died -- its only protection,
`build_compact_night_shelter`, returns False on its first line without 10
cobblestone or dirt (base.py), so it just brawls at 0/4 armour until it loses.
The fix has to produce its own blocks first.
"""

from types import SimpleNamespace

import pytest

from baritone_client.actions import death_recovery_action as recovery
from baritone_client.actions import rearm_safety
from baritone_client.common import base, night_shelter


class _World:
    """A minimal world that models digging: blocks vanish and the body falls."""

    def __init__(self, ground="minecraft:dirt", feet_y=64):
        self.ground = ground
        self.feet_y = feet_y
        self.broken = []
        self.placed = []
        self.inventory = []
        self.day_time = 16359  # the night the bots kept dying on

    def dispatch(self, route, payload=None):
        payload = payload or {}
        if route == "get_state":
            return {
                "block_position": {"x": 0, "y": self.feet_y, "z": 0},
                "position": {"x": 0, "y": self.feet_y, "z": 0},
                "world_time": self.day_time,
                "is_dead": False,
                "dimension": "minecraft:overworld",
            }
        if route == "get_block":
            y = int(payload.get("y", 0))
            if (0, y, 0) in self.broken:
                return {"id": "minecraft:air"}
            if any(p[1] == y for p in self.placed):
                return {"id": "minecraft:cobblestone"}
            return {"id": self.ground if y < self.feet_y else "minecraft:air"}
        if route == "dig_block":
            y = int(payload.get("y", 0))
            self.broken.append((0, y, 0))
            self.feet_y = y
            self.inventory.append({"id": "minecraft:dirt", "count": 1})
            return {}
        if route == "get_inventory":
            return {"inventory": list(self.inventory)}
        return {}


def _client(world):
    return SimpleNamespace(transport=SimpleNamespace(dispatch=world.dispatch))


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(night_shelter.time, "sleep", lambda *_a, **_k: None)


def test_the_hole_is_paid_for_by_digging_it(monkeypatch):
    """The blocks that seal the shaft are the ones the shaft produced.

    This is what escapes the circularity: every other shelter primitive needs
    blocks up front, which a respawned bot cannot have.
    """
    world = _World()
    placed = []
    monkeypatch.setattr(
        night_shelter, "robust_place",
        lambda _c, x, y, z, item: (placed.append((x, y, z, item)), True)[1],
    )
    monkeypatch.setattr(night_shelter, "_has_existing_enclosure", lambda *_a, **_k: True)

    assert night_shelter.dig_and_seal_night_hole(_client(world), depth=3) is True
    assert len(world.broken) == 3, f"expected a 3-deep shaft, dug {world.broken}"
    assert placed, "the shaft was dug but never capped"
    assert placed[0][3] == "minecraft:dirt", (
        f"capped with {placed[0][3]}; it must use what it just mined, not a "
        "hardcoded cobblestone it does not own"
    )


def test_the_cap_lands_directly_above_the_bots_head(monkeypatch):
    """A cap at feet+2 is what makes _has_existing_enclosure report roofed."""
    world = _World(feet_y=64)
    placed = []
    monkeypatch.setattr(
        night_shelter, "robust_place",
        lambda _c, x, y, z, item: (placed.append((x, y, z, item)), True)[1],
    )
    monkeypatch.setattr(night_shelter, "_has_existing_enclosure", lambda *_a, **_k: True)

    night_shelter.dig_and_seal_night_hole(_client(world), depth=3)

    assert placed[0][1] == world.feet_y + 2, (
        f"capped at y={placed[0][1]} with feet at {world.feet_y}; the roof scan "
        "starts at feet+2"
    )


def test_stone_ground_is_refused_rather_than_flailed_at(monkeypatch):
    """Without a pickaxe stone cannot be hand-broken; do not dig a partial hole."""
    world = _World(ground="minecraft:stone")
    monkeypatch.setattr(night_shelter, "robust_place", lambda *_a, **_k: True)

    assert night_shelter.dig_and_seal_night_hole(_client(world)) is False
    assert world.broken == [], "dug into stone bare-handed"


@pytest.mark.parametrize("hazard", ["minecraft:lava", "minecraft:water"])
def test_never_digs_into_a_hazard(monkeypatch, hazard):
    world = _World(ground=hazard)
    monkeypatch.setattr(night_shelter, "robust_place", lambda *_a, **_k: True)

    assert night_shelter.dig_and_seal_night_hole(_client(world)) is False
    assert world.broken == []


def test_an_uncappable_shaft_reports_failure(monkeypatch):
    """Digging in and failing to close the lid is the worst outcome of all.

    The old build_emergency_shelter did exactly that: it dug three blocks then
    tried to cap with cobblestone it did not carry, so the bot ended up
    cornered at the bottom of an open shaft while the caller was told False.
    """
    world = _World()
    monkeypatch.setattr(night_shelter, "robust_place", lambda *_a, **_k: False)
    monkeypatch.setattr(night_shelter, "_has_existing_enclosure", lambda *_a, **_k: False)

    assert night_shelter.dig_and_seal_night_hole(_client(world)) is False


def test_emergency_shelter_no_longer_caps_with_unowned_cobblestone(monkeypatch):
    """The naked branch must route through the self-financing hole."""
    world = _World()
    called = {}
    monkeypatch.setattr(base, "count_item", lambda _c, _i: 0)
    monkeypatch.setattr(base, "establish_dry_footing", lambda _c: True)
    monkeypatch.setattr(base, "get_player_pos", lambda _c: (0, 64, 0))
    monkeypatch.setattr(
        night_shelter, "dig_and_seal_night_hole",
        lambda _c, **_k: called.setdefault("dug", True),
    )

    assert base.build_emergency_shelter(_client(world)) is True
    assert called.get("dug"), "naked branch did not use the dig-and-seal hole"


# ---------------------------------------------------------------------------
# the gate in front of the 300-second gather
# ---------------------------------------------------------------------------
def _recovery_client(day_time, threats=()):
    def dispatch(route, payload=None):
        if route == "get_state":
            return {
                "world_time": day_time,
                "is_dead": False,
                "block_position": {"x": 0, "y": 64, "z": 0},
            }
        return {}

    return SimpleNamespace(transport=SimpleNamespace(dispatch=dispatch))


def test_daylight_and_clear_gathers_immediately(monkeypatch):
    """The common case must stay free -- no shelter work, no behaviour change."""
    monkeypatch.setattr(
        "baritone_client.common.combat.scan_for_threats", lambda *_a, **_k: []
    )
    monkeypatch.setattr(
        night_shelter, "dig_and_seal_night_hole",
        lambda *_a, **_k: pytest.fail("dug a hole in broad daylight"),
    )

    assert rearm_safety.survivable_bootstrap_window(_recovery_client(1000)) is True


def test_night_shelters_before_it_gathers(monkeypatch):
    """THE bug: at night the old code went straight out for wood and died."""
    order = []
    monkeypatch.setattr(
        "baritone_client.common.combat.scan_for_threats", lambda *_a, **_k: []
    )
    monkeypatch.setattr(base, "_has_existing_enclosure", lambda *_a, **_k: False)
    monkeypatch.setattr(
        night_shelter, "dig_and_seal_night_hole",
        lambda *_a, **_k: (order.append("shelter"), True)[1],
    )
    monkeypatch.setattr(
        base, "wait_for_safe_daylight",
        lambda *_a, **_k: (order.append("wait"), True)[1],
    )

    assert rearm_safety.survivable_bootstrap_window(_recovery_client(16359)) is True
    assert order == ["shelter", "wait"], (
        f"got {order}; the bot must be sealed in before it waits out the night"
    )


def test_unshelterable_night_defers_instead_of_gathering(monkeypatch):
    """No hand-mineable ground: refuse the gather rather than repeat the death."""
    monkeypatch.setattr(
        "baritone_client.common.combat.scan_for_threats", lambda *_a, **_k: []
    )
    monkeypatch.setattr(base, "_has_existing_enclosure", lambda *_a, **_k: False)
    monkeypatch.setattr(night_shelter, "dig_and_seal_night_hole", lambda *_a, **_k: False)

    assert rearm_safety.survivable_bootstrap_window(_recovery_client(16359)) is False


def test_bootstrap_does_not_gather_when_the_window_is_unsurvivable(monkeypatch):
    """The 300s outdoor gather must not start at all if we cannot survive it."""
    monkeypatch.setattr(
        "baritone_client.common.inventory.count_item", lambda *_a, **_k: 0
    )
    monkeypatch.setattr(recovery, "survivable_bootstrap_window", lambda _c: False)
    monkeypatch.setattr(
        "baritone_client.common.resources.ensure_supplies",
        lambda *_a, **_k: pytest.fail("started a naked 300s gather anyway"),
    )

    assert recovery._bootstrap_starter_pickaxe(_recovery_client(16359)) is False
