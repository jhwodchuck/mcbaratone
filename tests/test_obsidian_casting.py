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

    def dispatch(self, route, payload=None):
        payload = payload or {}
        if route == "get_block":
            key = (payload["x"], payload["y"], payload["z"])
            return self.blocks.get(key, {"id": "minecraft:air", "state": {}})
        if route == "get_state":
            return {"health": 20.0, "food_level": 20, "is_dead": False}
        if route == "interact_block":
            key = (payload["x"], payload["y"], payload["z"])
            self.interactions.append((self.selected, key))
            if self.selected == casting.WATER_BUCKET:
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
        if route == "interact_block" and world.selected == casting.WATER_BUCKET:
            world.blocks[(0, 0, 0)] = {"id": "minecraft:lava", "state": {"level": "0"}}
        return result

    world.dispatch = dispatch
    client = _client(world)
    _patch_selection(monkeypatch, world)
    # An advancing clock so the wait loop expires instead of spinning forever.
    ticks = iter(range(0, 10_000))
    monkeypatch.setattr(casting.time, "monotonic", lambda: float(next(ticks)))

    assert not casting.cast_one(client, (0, 0, 0), timeout=1.0)
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
