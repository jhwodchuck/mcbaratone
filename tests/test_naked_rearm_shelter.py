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
                # Block-centred, as a standing player actually is: an integer
                # precise position means straddling a boundary.
                "position": {"x": 0.5, "y": self.feet_y, "z": 0.5},
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


# ---------------------------------------------------------------------------
# water, which defeated the whole method live
# ---------------------------------------------------------------------------
class _WaterWorld(_World):
    """Feet in water over perfectly good dirt: digging can never drop the body."""

    def dispatch(self, route, payload=None):
        payload = payload or {}
        if route == "get_block":
            y = int(payload.get("y", 0))
            if y == self.feet_y:
                return {"id": "minecraft:water"}
        return super().dispatch(route, payload)


def test_refuses_to_dig_while_standing_in_liquid(monkeypatch):
    """THE dragon-b failure: it dug good dirt and never fell, because water held it.

    Water buoys the body, so removing the block underneath does not drop it and
    the descent check can never pass -- and the shaft would flood regardless.
    The live log said "body did not fall into the shaft" on ground that was
    dirt all the way down.
    """
    world = _WaterWorld()
    monkeypatch.setattr(night_shelter, "robust_place", lambda *_a, **_k: True)
    monkeypatch.setattr(
        "baritone_client.common.surface_recovery.reach_dry_surface",
        lambda *_a, **_k: None,
    )

    assert night_shelter.dig_and_seal_night_hole(_client(world)) is False
    assert world.broken == [], (
        f"dug {world.broken} while standing in water; the body cannot fall"
    )


def test_digs_after_reaching_dry_ground(monkeypatch):
    """Water is a reason to move, not a reason to give up."""

    class _DryableWorld(_World):
        def __init__(self):
            super().__init__()
            self.in_water = True

        def dispatch(self, route, payload=None):
            payload = payload or {}
            if route == "get_block" and self.in_water:
                if int(payload.get("y", 0)) == self.feet_y:
                    return {"id": "minecraft:water"}
            return super().dispatch(route, payload)

    world = _DryableWorld()
    moved = {}

    def _dry(*_a, **_k):
        moved["called"] = True
        world.in_water = False
        return (0, world.feet_y, 0)

    monkeypatch.setattr(night_shelter, "robust_place", lambda *_a, **_k: True)
    monkeypatch.setattr(night_shelter, "_has_existing_enclosure", lambda *_a, **_k: True)
    monkeypatch.setattr(
        "baritone_client.common.surface_recovery.reach_dry_surface", _dry
    )

    assert night_shelter.dig_and_seal_night_hole(_client(world), depth=2) is True
    assert moved.get("called"), "never attempted to leave the water"
    assert world.broken, "reached dry ground but still did not dig"


# ---------------------------------------------------------------------------
# stone, the other ground that stopped a shelter cold
# ---------------------------------------------------------------------------
class _MountainWorld(_World):
    """Stone underfoot, with a patch of dirt a few blocks away.

    dragon-a respawned at [1, 163, -7] on a mountain and logged
    "minecraft:stone at y=152 is not hand-mineable; stopping descent" -- it had
    no pickaxe, because rebuilding one is the whole point of the recovery.
    """

    DIRT_AT = (2, -6)  # (x, z) of the diggable patch

    def __init__(self):
        super().__init__(ground="minecraft:stone", feet_y=163)
        self.x = 1
        self.z = -7

    def dispatch(self, route, payload=None):
        payload = payload or {}
        if route == "get_state":
            return {
                "block_position": {"x": self.x, "y": self.feet_y, "z": self.z},
                "position": {
                    "x": self.x + 0.5, "y": self.feet_y, "z": self.z + 0.5
                },
                "world_time": self.day_time,
                "is_dead": False,
            }
        if route == "get_block":
            bx, by, bz = int(payload["x"]), int(payload["y"]), int(payload["z"])
            if (bx, by, bz) in self.broken:
                return {"id": "minecraft:air"}
            if by >= self.feet_y:
                return {"id": "minecraft:air"}
            return {
                "id": "minecraft:dirt"
                if (bx, bz) == self.DIRT_AT
                else "minecraft:stone"
            }
        if route == "dig_block":
            bx, by, bz = int(payload["x"]), int(payload["y"]), int(payload["z"])
            self.broken.append((bx, by, bz))
            self.feet_y = by
            self.inventory.append({"id": "minecraft:dirt", "count": 1})
            return {}
        if route == "get_inventory":
            return {"inventory": list(self.inventory)}
        return {}


def test_stone_underfoot_moves_to_diggable_ground(monkeypatch):
    """THE dragon-a failure: it gave up on a mountain instead of stepping aside."""
    world = _MountainWorld()
    went = {}

    def _goto(_c, x, y, z, **_k):
        went["to"] = (x, z)
        world.x, world.z = x, z
        return True

    monkeypatch.setattr(night_shelter, "robust_place", lambda *_a, **_k: True)
    monkeypatch.setattr(night_shelter, "_has_existing_enclosure", lambda *_a, **_k: True)
    monkeypatch.setattr("baritone_client.common.navigation.goto", _goto)

    assert night_shelter.dig_and_seal_night_hole(_client(world), depth=2) is True
    assert went.get("to") == _MountainWorld.DIRT_AT, (
        f"moved to {went.get('to')}, expected the dirt patch "
        f"{_MountainWorld.DIRT_AT}"
    )
    assert world.broken, "reached diggable ground but never dug"


def test_solid_stone_everywhere_still_refuses(monkeypatch):
    """No diggable ground in range is a real refusal, not an excuse to flail."""

    class _AllStone(_MountainWorld):
        DIRT_AT = (9999, 9999)

    world = _AllStone()
    monkeypatch.setattr(night_shelter, "robust_place", lambda *_a, **_k: True)
    monkeypatch.setattr(
        "baritone_client.common.navigation.goto",
        lambda *_a, **_k: pytest.fail("walked off toward ground that does not exist"),
    )

    assert night_shelter.dig_and_seal_night_hole(_client(world)) is False
    assert world.broken == [], "tried to hand-mine stone"


def test_the_search_never_wanders_far_in_the_dark(monkeypatch):
    """A long night walk with no armour is worse than no shelter."""
    world = _MountainWorld()
    probed = []
    real = night_shelter._house_block_id
    monkeypatch.setattr(
        night_shelter, "_house_block_id",
        lambda c, x, y, z: (probed.append((x, z)), real(c, x, y, z))[1],
    )

    night_shelter._nearest_diggable_column(_client(world), 1, 163, -7)

    worst = max(
        max(abs(px - 1), abs(pz - (-7))) for px, pz in probed
    )
    assert worst <= night_shelter.DIGGABLE_SEARCH_RADIUS, (
        f"probed {worst} blocks out, past the {night_shelter.DIGGABLE_SEARCH_RADIUS} "
        "block search radius"
    )


def test_an_empty_search_says_so(monkeypatch, capsys):
    """A silent fall-through is indistinguishable from the fix not running.

    That ambiguity cost a live diagnosis: dragon-a's log showed only "not
    hand-mineable", which reads the same whether the search found nothing or
    the code was never loaded.
    """

    class _AllStone(_MountainWorld):
        DIRT_AT = (9999, 9999)

    monkeypatch.setattr(night_shelter, "robust_place", lambda *_a, **_k: True)
    night_shelter.dig_and_seal_night_hole(_client(_AllStone()))

    printed = capsys.readouterr().out
    assert "no hand-mineable ground within" in printed, (
        f"search failure was silent; log said only: {printed!r}"
    )


# ---------------------------------------------------------------------------
# straddling, which held the body up over a dug hole
# ---------------------------------------------------------------------------
def test_the_footprint_spans_every_column_the_body_rests_on():
    """A 0.6-wide hitbox off-centre sits on up to four columns."""
    # dragon-a's live position when the descent stalled.
    assert night_shelter._footprint_columns(-1.894, 19.882) == [
        (-3, 19), (-3, 20), (-2, 19), (-2, 20)
    ]
    # Block-centred is the ordinary case and touches exactly one.
    assert night_shelter._footprint_columns(-1.5, 19.5) == [(-2, 19)]


def test_digs_out_every_supporting_column(monkeypatch):
    """THE dragon-a stall: one column dug, three snow blocks still holding it.

    Live at x=-1.894, z=19.882 the floor under block_position was already air
    while (-3,153,19), (-3,153,20) and (-2,153,20) were still solid. The body
    had nothing to fall into and the descent aborted on ground that was
    entirely diggable.
    """

    class _StraddleWorld(_World):
        def __init__(self):
            # dirt, not the snow dragon-a actually stood on: snow is now
            # correctly refused for a different reason (it pays for no lid),
            # and this test is about the geometry, not the material.
            super().__init__(ground="minecraft:dirt", feet_y=154)

        def dispatch(self, route, payload=None):
            payload = payload or {}
            if route == "get_state":
                return {
                    "block_position": {"x": -2, "y": self.feet_y, "z": 19},
                    "position": {"x": -1.894, "y": self.feet_y, "z": 19.882},
                    "world_time": self.day_time,
                    "is_dead": False,
                }
            if route == "get_block":
                bx, by, bz = (
                    int(payload["x"]), int(payload["y"]), int(payload["z"])
                )
                if (bx, by, bz) in self.broken:
                    return {"id": "minecraft:air"}
                return {
                    "id": "minecraft:dirt" if by < self.feet_y
                    else "minecraft:air"
                }
            if route == "dig_block":
                self.broken.append(
                    (int(payload["x"]), int(payload["y"]), int(payload["z"]))
                )
                self.inventory.append({"id": "minecraft:dirt", "count": 1})
                # The body only drops once nothing is left holding it.
                floor_y = self.feet_y - 1
                supports = [
                    c for c in night_shelter._footprint_columns(-1.894, 19.882)
                    if (c[0], floor_y, c[1]) not in self.broken
                ]
                if not supports:
                    self.feet_y = floor_y
                return {}
            if route == "get_inventory":
                return {"inventory": list(self.inventory)}
            return {}

    world = _StraddleWorld()
    monkeypatch.setattr(night_shelter, "robust_place", lambda *_a, **_k: True)
    monkeypatch.setattr(night_shelter, "_has_existing_enclosure", lambda *_a, **_k: True)

    assert night_shelter.dig_and_seal_night_hole(_client(world), depth=1) is True
    dug_columns = {(bx, bz) for bx, _by, bz in world.broken}
    assert dug_columns == {(-3, 19), (-3, 20), (-2, 19), (-2, 20)}, (
        f"only cleared {sorted(dug_columns)}; the rest still support the body"
    )


# ---------------------------------------------------------------------------
# the spoil has to be able to become the lid
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "ground, why",
    [
        ("minecraft:snow_block", "needs a shovel, and drops snowballs"),
        ("minecraft:clay", "drops clay balls, which are items not blocks"),
        ("minecraft:sand", "falls, so a cap lands back on the bot's head"),
        ("minecraft:red_sand", "falls"),
        ("minecraft:gravel", "drops flint sometimes, and falls"),
    ],
)
def test_ground_that_cannot_pay_for_the_lid_is_not_dug(ground, why):
    """THE dragon-a snow shaft: dug four columns, fell in, then had no cap.

    A shaft is only worth digging if the spoil can be placed back overhead.
    """
    assert ground not in night_shelter._HAND_MINEABLE_GROUND, (
        f"{ground} is listed as diggable but {why}"
    )


@pytest.mark.parametrize(
    "ground", ["minecraft:dirt", "minecraft:grass_block", "minecraft:podzol"]
)
def test_dirt_family_is_still_diggable(ground):
    """The fix must not narrow the list into uselessness."""
    assert ground in night_shelter._HAND_MINEABLE_GROUND


def test_every_diggable_ground_yields_a_usable_cap():
    """Each listed ground must drop something in _CAP_ITEMS.

    grass/podzol/mycelium drop plain dirt; the rest drop themselves.
    """
    drops = {
        "minecraft:grass_block": "minecraft:dirt",
        "minecraft:podzol": "minecraft:dirt",
        "minecraft:mycelium": "minecraft:dirt",
    }
    for ground in night_shelter._HAND_MINEABLE_GROUND:
        produced = drops.get(ground, ground)
        assert produced in night_shelter._CAP_ITEMS, (
            f"digging {ground} yields {produced}, which cannot cap the shaft"
        )


def test_no_cap_item_falls_under_gravity():
    """A falling cap drops back through the hole onto the bot."""
    for item in night_shelter._CAP_ITEMS:
        assert "sand" not in item and "gravel" not in item, (
            f"{item} obeys gravity and cannot hold a roof"
        )
