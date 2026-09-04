"""Obsidian is renewable; A1 was treating it as a fixed stock.

Natural obsidian only exists where water has already met lava, so mining it is
drawing down a supply that never refills. Live 2026-09-02 A1 exhausted what was
within reach, wandered 330 blocks, and logged "No reachable minecraft:obsidian
in the bounded search" for hours -- while carrying a water bucket, an empty
bucket and a diamond pickaxe at y=-51, surrounded by lava.

The mechanic has one trap: water meeting a lava SOURCE gives obsidian, water
meeting FLOWING lava gives cobblestone. A caster that skips that check spends
its bucket and calls a stone block a success -- the same failure shape as
optimising the path to a target that was never valid.
"""

from __future__ import annotations

from types import SimpleNamespace

from baritone_client.common import obsidian_casting as casting


class FakeWorld:
    """A tiny block world that answers get_block and mutates on interaction."""

    def __init__(self, blocks):
        self.blocks = dict(blocks)
        self.interactions = []
        self.selected = None
        self.water_buckets = 1

    def dispatch(self, route, payload=None):
        payload = payload or {}
        if route == "get_inventory":
            return {"inventory": [{"id": casting.WATER_BUCKET, "count": self.water_buckets}]}
        if route == "get_block":
            key = (payload["x"], payload["y"], payload["z"])
            return self.blocks.get(key, {"id": "minecraft:air", "state": {}})
        if route == "get_state":
            return {
                "health": 20.0,
                "food_level": 20,
                "is_dead": False,
                # Standing right next to the origin, within reach.
                "block_position": {"x": 0, "y": 1, "z": 1},
            }
        if route == "use_bucket":
            key = (payload["x"], payload["y"], payload["z"])
            self.interactions.append((self.selected, key))
            if self.selected == casting.WATER_BUCKET:
                self.water_buckets -= 1
                below = (key[0], key[1] - 1, key[2])
                target = self.blocks.get(below, {})
                # Vanilla: source -> obsidian, flowing -> cobblestone.
                if target.get("id") == casting.LAVA:
                    level = str(target.get("state", {}).get("level", "0"))
                    self.blocks[below] = {
                        "id": "minecraft:obsidian" if level == "0"
                        else "minecraft:cobblestone",
                        "state": {},
                    }
                self.blocks[key] = {"id": "minecraft:water", "state": {"level": "0"}}
            elif self.selected == casting.EMPTY_BUCKET:
                self.water_buckets += 1
                self.blocks[key] = {"id": "minecraft:air", "state": {}}
            return {}
        if route == "find_blocks":
            found = [
                {"x": x, "y": y, "z": z, "distance": abs(x) + abs(y) + abs(z)}
                for (x, y, z), block in self.blocks.items()
                if block.get("id") == casting.LAVA
            ]
            return {"found": found}
        return {}


def _client(world):
    return SimpleNamespace(transport=world)


def _patch_selection(monkeypatch, world):
    def select(_client, item_id, allow_swap=False):
        world.selected = item_id
        return True

    monkeypatch.setattr("baritone_client.common.inventory.select_item", select)
    monkeypatch.setattr(casting.time, "sleep", lambda _s: None)


def test_a_lava_source_is_distinguished_from_flowing_lava(monkeypatch):
    world = FakeWorld({
        (0, 0, 0): {"id": "minecraft:lava", "state": {"level": "0"}},
        (5, 0, 0): {"id": "minecraft:lava", "state": {"level": "3"}},
        (9, 0, 0): {"id": "minecraft:stone", "state": {}},
    })
    client = _client(world)

    assert casting.is_lava_source(client, 0, 0, 0)
    assert not casting.is_lava_source(client, 5, 0, 0), "flowing lava is not a source"
    assert not casting.is_lava_source(client, 9, 0, 0)


def test_casting_a_source_yields_obsidian_and_returns_the_bucket(monkeypatch):
    world = FakeWorld({(0, 0, 0): {"id": "minecraft:lava", "state": {"level": "0"}}})
    client = _client(world)
    _patch_selection(monkeypatch, world)

    assert casting.cast_one(client, (0, 0, 0))
    assert world.blocks[(0, 0, 0)]["id"] == "minecraft:obsidian"
    # The water must be picked back up: losing the bucket ends the capability.
    assert world.blocks[(0, 1, 0)]["id"] == "minecraft:air"
    assert world.interactions[-1][0] == casting.EMPTY_BUCKET


def test_flowing_lava_is_never_cast(monkeypatch):
    """The trap: this would make cobblestone and report success."""
    world = FakeWorld({(0, 0, 0): {"id": "minecraft:lava", "state": {"level": "5"}}})
    client = _client(world)
    _patch_selection(monkeypatch, world)

    assert not casting.cast_one(client, (0, 0, 0))
    assert world.interactions == [], "must not spend the bucket on flowing lava"
    assert world.blocks[(0, 0, 0)]["id"] == "minecraft:lava"


def test_an_obstructed_pour_position_is_skipped(monkeypatch):
    """Water needs the air block above the source to flow down into it."""
    world = FakeWorld({
        (0, 0, 0): {"id": "minecraft:lava", "state": {"level": "0"}},
        (0, 1, 0): {"id": "minecraft:deepslate", "state": {}},
    })
    client = _client(world)
    _patch_selection(monkeypatch, world)

    assert not casting.cast_one(client, (0, 0, 0))
    assert world.interactions == []


def test_the_bucket_is_reclaimed_even_when_the_cast_fails(monkeypatch):
    """A lost bucket ends the capability; the water is standing right there."""
    world = FakeWorld({(0, 0, 0): {"id": "minecraft:lava", "state": {"level": "0"}}})

    # The block never converts, simulating a cast that silently did nothing.
    original = world.dispatch

    def dispatch(route, payload=None):
        result = original(route, payload)
        if route == "use_bucket" and world.selected == casting.WATER_BUCKET:
            world.blocks[(0, 0, 0)] = {"id": "minecraft:lava", "state": {"level": "0"}}
        return result

    world.dispatch = dispatch
    client = _client(world)
    _patch_selection(monkeypatch, world)
    # An advancing clock so the wait loop expires instead of spinning forever.
    ticks = iter(range(0, 10_000))
    monkeypatch.setattr(casting.time, "monotonic", lambda: float(next(ticks)))

    assert not casting.cast_one(client, (0, 0, 0), timeout=3.0)
    assert world.interactions[-1][0] == casting.EMPTY_BUCKET


def test_casting_refuses_without_a_water_bucket(monkeypatch):
    world = FakeWorld({(0, 0, 0): {"id": "minecraft:lava", "state": {"level": "0"}}})
    monkeypatch.setattr(
        "baritone_client.common.inventory.count_item", lambda _c, _i: 0
    )
    assert casting.cast_obsidian(_client(world), 4) == 0


def test_casting_refuses_when_hurt_or_starving(monkeypatch):
    """Casting walks the bot to standing lava, which is where it dies."""
    world = FakeWorld({(0, 0, 0): {"id": "minecraft:lava", "state": {"level": "0"}}})
    monkeypatch.setattr(
        "baritone_client.common.inventory.count_item", lambda _c, _i: 1
    )
    world.dispatch_state = None
    original = world.dispatch

    def dispatch(route, payload=None):
        if route == "get_state":
            return {"health": 6.0, "food_level": 20, "is_dead": False}
        return original(route, payload)

    world.dispatch = dispatch
    assert casting.cast_obsidian(_client(world), 4) == 0


def test_lava_is_searched_in_rings_and_far_enough_to_matter(monkeypatch):
    """A 24-block radius from a surface base can never reach the lava layer.

    Live on A1 2026-09-02, standing at its base at y=79, the caster fired
    correctly and reported "cast: no lava within 24 blocks" -- the wiring was
    right and the reach was useless. Lava sits below y=0, so the default has to
    span that gap, while still trying the nearest rings first: the closest
    source is the shortest walk, and every block walked toward standing lava is
    risk.
    """
    import inspect

    default = inspect.signature(casting.cast_obsidian).parameters["radius"].default
    assert default >= 96, "must be able to reach the lava layer from the surface"

    asked = []

    class Ringed:
        def dispatch(self, route, payload=None):
            if route == "find_blocks":
                asked.append(payload["radius"])
                # Only the widest ring has anything.
                if payload["radius"] < 64:
                    return {"found": []}
                return {"found": [{"x": 0, "y": 0, "z": 0, "distance": 3.0}]}
            return {}

    found = casting._lava_candidates(_client(Ringed()), 112)
    assert found == [(0, 0, 0)]
    assert asked == sorted(asked), f"must widen outward, got {asked}"
    assert asked[0] < asked[-1], "nearest ring first"


def test_the_bot_never_stands_in_the_lava_it_came_to_cast():
    """A source in a pool has lava for neighbours."""
    world = FakeWorld({
        (0, 0, 0): {"id": "minecraft:lava", "state": {"level": "0"}},
        # East and west neighbours are more lava; north is a solid ledge.
        (1, 0, 0): {"id": "minecraft:lava", "state": {"level": "0"}},
        (-1, 0, 0): {"id": "minecraft:lava", "state": {"level": "0"}},
        (0, 0, 1): {"id": "minecraft:deepslate", "state": {}},
    })
    client = _client(world)

    spot = casting._standing_spot(client, (0, 0, 0))
    assert spot == (0, 1, 1), f"must pick the solid ledge, got {spot}"


def test_a_source_with_no_dry_foothold_is_skipped():
    world = FakeWorld({
        (0, 0, 0): {"id": "minecraft:lava", "state": {"level": "0"}},
        (1, 0, 0): {"id": "minecraft:lava", "state": {"level": "0"}},
        (-1, 0, 0): {"id": "minecraft:lava", "state": {"level": "0"}},
        (0, 0, 1): {"id": "minecraft:lava", "state": {"level": "0"}},
        (0, 0, -1): {"id": "minecraft:lava", "state": {"level": "0"}},
    })
    assert casting._standing_spot(_client(world), (0, 0, 0)) is None


def test_a_full_inventory_is_discovered_before_walking_to_lava(monkeypatch):
    """Carrying a bucket is not the same as being able to hold one.

    A full inventory has no free hotbar slot to swap into, so select_item
    fails -- at the pour, after the walk. Live on A1 2026-09-02 with 36/36
    slots used, the caster found lava, approached it, and only then reported
    "cast: no water bucket in hand", having spent the whole trip to learn
    something it could have checked standing still.
    """
    world = FakeWorld({(0, 0, 0): {"id": "minecraft:lava", "state": {"level": "0"}}})
    walked = []

    monkeypatch.setattr(
        "baritone_client.common.inventory.count_item", lambda _c, _i: 1
    )
    # Selection fails and freeing a slot does not help.
    monkeypatch.setattr(
        "baritone_client.common.inventory.select_item",
        lambda *_a, **_k: False,
    )
    monkeypatch.setattr(
        "baritone_client.common.resources.manage_inventory",
        lambda *_a, **_k: False,
    )

    assert casting.cast_obsidian(
        _client(world), 2, goto=lambda *a: walked.append(a) or True
    ) == 0
    assert walked == [], "must not walk to lava it cannot pour on"


def test_a_full_inventory_is_cleared_and_casting_proceeds(monkeypatch):
    """One freed slot is all it takes; do not abandon the trip over it."""
    world = FakeWorld({(0, 0, 0): {"id": "minecraft:lava", "state": {"level": "0"}}})
    freed = {"done": False}

    def select(_client, item_id, allow_swap=False):
        if not freed["done"]:
            return False
        world.selected = item_id
        return True

    def free_slot(*_a, **_k):
        freed["done"] = True
        return True

    monkeypatch.setattr(
        "baritone_client.common.inventory.count_item", lambda _c, _i: 1
    )
    monkeypatch.setattr("baritone_client.common.inventory.select_item", select)
    monkeypatch.setattr(
        "baritone_client.common.resources.manage_inventory", free_slot
    )
    monkeypatch.setattr(casting.time, "sleep", lambda _s: None)

    assert casting._bucket_in_hand(_client(world))
    assert freed["done"], "must try to free a slot before giving up"


def test_a_pour_that_did_not_land_costs_nothing(monkeypatch):
    """Dispatching the pour is not evidence it happened."""
    world = FakeWorld({(0, 0, 0): {"id": "minecraft:lava", "state": {"level": "0"}}})
    # Swallow the interaction so no water is ever placed.
    original = world.dispatch
    world.dispatch = lambda route, payload=None: (
        {} if route == "use_bucket" else original(route, payload)
    )
    client = _client(world)
    _patch_selection(monkeypatch, world)
    monkeypatch.setattr(
        "baritone_client.common.inventory.count_item", lambda _c, _i: 1
    )

    assert not casting.cast_one(client, (0, 0, 0))
    assert world.blocks[(0, 0, 0)]["id"] == "minecraft:lava"


def test_an_unrecovered_bucket_stops_the_run_instead_of_grinding(monkeypatch):
    """One lost bucket makes every later candidate fail the same way.

    Live on A1 2026-09-02, "cast: no water bucket in hand" repeated across a
    run whose opening check had passed -- the bucket was spent mid-loop and
    never came back, and each further candidate was walked to and refused. That
    reads like a selection bug rather than a spent bucket.
    """
    world = FakeWorld({
        (0, 0, 0): {"id": "minecraft:lava", "state": {"level": "0"}},
        (4, 0, 0): {"id": "minecraft:lava", "state": {"level": "0"}},
        (0, 0, 1): {"id": "minecraft:deepslate", "state": {}},
        (4, 0, 1): {"id": "minecraft:deepslate", "state": {}},
    })
    walked = []
    buckets = {"water": 1}

    monkeypatch.setattr(
        "baritone_client.common.inventory.count_item",
        lambda _c, item: buckets["water"] if "water" in item else 1,
    )
    monkeypatch.setattr(casting.time, "sleep", lambda _s: None)

    def select(_client, item_id, allow_swap=False):
        world.selected = item_id
        return True

    monkeypatch.setattr("baritone_client.common.inventory.select_item", select)

    real_cast_one = casting.cast_one

    def cast_one(client, lava, **kw):
        buckets["water"] = 0          # the pour spends it and it never returns
        return real_cast_one(client, lava, **kw)

    monkeypatch.setattr(casting, "cast_one", cast_one)

    casting.cast_obsidian(
        _client(world), 2, goto=lambda *a: walked.append(a) or True
    )
    assert len(walked) <= 1, f"must stop after the bucket is gone, walked {walked}"


def test_a_pour_is_not_attempted_from_out_of_reach():
    """The server silently rejects a use-on-block beyond ~4.5 blocks.

    Live on A1 2026-09-02, thirteen consecutive sources reported "water did
    not land" while the bucket was never actually spent: the bot was pouring
    from wherever it happened to be standing.
    """
    world = FakeWorld({(0, 0, 0): {"id": "minecraft:lava", "state": {"level": "0"}}})
    original = world.dispatch
    world.dispatch = lambda route, payload=None: (
        {"health": 20.0, "food_level": 20, "is_dead": False,
         "block_position": {"x": 40, "y": 1, "z": 40}}      # far away
        if route == "get_state" else original(route, payload)
    )

    assert not casting.cast_one(_client(world), (0, 0, 0))
    assert world.interactions == [], "must not pour from across the room"


def test_a_failed_walk_skips_the_source_instead_of_pouring_anyway():
    """goto blocks and reports arrival; ignoring that answer wasted every cast."""
    world = FakeWorld({
        (0, 0, 0): {"id": "minecraft:lava", "state": {"level": "0"}},
        (0, 0, 1): {"id": "minecraft:deepslate", "state": {}},
    })

    import baritone_client.common.obsidian_casting as mod

    called = []
    original_cast_one = mod.cast_one
    try:
        mod.cast_one = lambda *a, **k: called.append(a) or True
        made = mod.cast_obsidian(
            _client(world), 1, goto=lambda *_a: False      # never arrives
        )
    finally:
        mod.cast_one = original_cast_one

    assert made == 0
    assert called == [], "a failed walk must not be followed by a pour"


def test_a_run_that_casts_nothing_says_why(capsys, monkeypatch):
    """Silent skips made a failing run indistinguishable from a hung one.

    Live A1 2026-09-02: 3.5 minutes of dead air between "No reachable
    obsidian" and "Could not gather portal materials", because every skip in
    the candidate loop was a bare `continue`.
    """
    world = FakeWorld({
        # Flowing lava -- skipped as not-a-source.
        (0, 0, 0): {"id": "minecraft:lava", "state": {"level": "4"}},
        (9, 0, 0): {"id": "minecraft:lava", "state": {"level": "2"}},
    })
    monkeypatch.setattr(
        "baritone_client.common.inventory.count_item", lambda _c, _i: 1
    )
    monkeypatch.setattr(
        "baritone_client.common.inventory.select_item", lambda *_a, **_k: True
    )
    assert casting.cast_obsidian(_client(world), 2, goto=lambda *_a: True) == 0

    out = capsys.readouterr().out
    assert "nothing made from" in out, out
    assert "flowing=2" in out, out


def test_a_run_that_cannot_walk_reports_the_walk(capsys, monkeypatch):
    # Far enough that the in-reach shortcut does not apply.
    world = FakeWorld({
        (40, 0, 40): {"id": "minecraft:lava", "state": {"level": "0"}},
        (40, 0, 41): {"id": "minecraft:deepslate", "state": {}},
    })
    monkeypatch.setattr(
        "baritone_client.common.inventory.count_item", lambda _c, _i: 1
    )
    monkeypatch.setattr(
        "baritone_client.common.inventory.select_item", lambda *_a, **_k: True
    )

    assert casting.cast_obsidian(_client(world), 1, goto=lambda *_a: False) == 0
    assert "walk_failed=1" in capsys.readouterr().out


def test_a_successful_cast_is_reported_as_success_not_failure():
    """#53 left this print inside the else branch.

    The live log then read:

        cast: water did not land above (259, -55, 292)
        cast: obsidian at (259, -55, 292) (0/1)
        cast: nothing made from 42 candidates (no_foothold=39, pour_failed=3)

    claiming obsidian was made, on the failure path, with a counter that never
    moved. A log that contradicts itself is worse than a silent one.
    """
    import inspect

    source = inspect.getsource(casting.cast_obsidian)
    success = source[source.index("if cast_one(client, position):"):]
    success = success[: success.index("if count_item(")]

    before_else, _, after_else = success.partition("else:")
    assert "cast: obsidian at" in before_else, success
    assert "cast: obsidian at" not in after_else, success
    assert "pour_failed" in after_else, success


def test_footholds_look_past_the_four_orthogonal_neighbours():
    """39 of 42 live sources had no orthogonal dry neighbour."""
    offsets = casting._FOOTHOLD_OFFSETS

    # The original four are still tried, and tried first.
    orthogonal = {(1, 1, 0), (-1, 1, 0), (0, 1, 1), (0, 1, -1)}
    assert orthogonal <= set(offsets)
    assert orthogonal == set(offsets[:4]), offsets[:4]

    # ...but they are no longer the only option.
    assert len(offsets) > 4
    # Never the lava's own column, which is not a foothold.
    assert not any(dx == 0 and dz == 0 for dx, _dy, dz in offsets)


def test_every_foothold_can_actually_touch_the_pour_target():
    """A spot out of reach is not a foothold, it is a wasted walk."""
    for dx, dy, dz in casting._FOOTHOLD_OFFSETS:
        distance = (dx * dx + (dy - 1) ** 2 + dz * dz) ** 0.5
        assert distance <= casting.REACH, (dx, dy, dz, distance)


def test_the_foothold_search_stays_cheap():
    """Each offset costs two block reads on every candidate that fails."""
    assert len(casting._FOOTHOLD_OFFSETS) <= 20, len(casting._FOOTHOLD_OFFSETS)


def test_a_source_already_in_reach_is_poured_without_walking(monkeypatch):
    """Lava embedded in rock has no standing ledge, but is often already close.

    Live A1 2026-09-02 at (258,-57,289), the nearest source (260,-55,291) sat
    3.5 blocks away inside deepslate: every neighbour was either solid rock
    (cannot stand in it) or air over lava (cannot stand on it), so all 20
    foothold offsets failed. Meanwhile the bot was already close enough to
    pour. Checking reach first costs one state read; the foothold search costs
    forty block reads before it can reach the same conclusion.
    """
    world = FakeWorld({(0, 0, 0): {"id": "minecraft:lava", "state": {"level": "0"}}})
    walked = []
    monkeypatch.setattr(
        "baritone_client.common.inventory.count_item", lambda _c, _i: 1
    )
    _patch_selection(monkeypatch, world)

    # FakeWorld reports the player at (0, 1, 1) -- adjacent to the pour target.
    made = casting.cast_obsidian(
        _client(world), 1, goto=lambda *a: walked.append(a) or True
    )

    assert made == 1, "an in-reach source must still be cast"
    assert walked == [], "no walk should be needed when already in reach"
    assert world.blocks[(0, 0, 0)]["id"] == "minecraft:obsidian"


def test_the_reach_shortcut_does_not_skip_the_walk_when_far(monkeypatch):
    """Distant sources must still be approached."""
    world = FakeWorld({
        (40, 0, 40): {"id": "minecraft:lava", "state": {"level": "0"}},
        (40, 0, 41): {"id": "minecraft:deepslate", "state": {}},
    })
    walked = []
    monkeypatch.setattr(
        "baritone_client.common.inventory.count_item", lambda _c, _i: 1
    )
    _patch_selection(monkeypatch, world)

    casting.cast_obsidian(
        _client(world), 1, goto=lambda *a: walked.append(a) or True
    )
    assert walked, "a source 40 blocks away must still be walked to"
