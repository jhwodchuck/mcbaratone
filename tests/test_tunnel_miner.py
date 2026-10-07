"""The tunnel miner digs a safe staircase to iron, lights it, and comes home."""

from types import SimpleNamespace

import pytest

from baritone_client.common import tunnel_miner as tm
from baritone_client.common import tunnel_planner as tp

AIR = "minecraft:air"
STONE = "minecraft:stone"
IRON = "minecraft:iron_ore"
ENTRANCE = (0, 70, 0)


class FakeWorld:
    """A stone world under a grass surface at y=69, with a scriptable bot."""

    def __init__(self, extent=20, bottom=30):
        self.blocks = {}
        for x in range(-extent, extent + 1):
            for z in range(-extent, extent + 1):
                for y in range(bottom, 70):
                    self.blocks[(x, y, z)] = "minecraft:grass_block" if y == 69 else STONE
        self.pos = ENTRANCE
        self.health = 20.0
        self.entities = []
        self.raw_iron = 0
        self.torches = 12
        self.dug = []
        self.placed = []
        self.hostile_after_digs = None
        self.used_slots = 1
        self.allow_break = "true"
        self.pathing = False
        self.transport = SimpleNamespace(dispatch=self.dispatch)

    def open(self, cell):
        return self.blocks.get(cell, AIR) in (AIR, "minecraft:torch")

    def dispatch(self, route, payload):
        if route == "get_state":
            x, y, z = self.pos
            return {
                "block_position": {"x": x, "y": y, "z": z}, "health": self.health,
                "food_level": 20, "dimension": "minecraft:overworld", "is_dead": self.health <= 0,
                "is_pathing": self.pathing,
            }
        if route == "settings":
            if "get" in payload:
                return {"value": self.allow_break}
            self.allow_break = str(payload.get("value"))
            return {"status": "requested"}
        if route == "get_block":
            return {"id": self.blocks.get((payload["x"], payload["y"], payload["z"]), AIR)}
        if route == "get_view":
            r, (px, py, pz) = payload["radius"], self.pos
            return {"voxels": [
                {"x": x, "y": y, "z": z, "id": b} for (x, y, z), b in self.blocks.items()
                if abs(x - px) <= r and abs(y - py) <= r and abs(z - pz) <= r
            ]}
        if route == "get_entities":
            return {"entities": list(self.entities)}
        if route == "get_inventory":
            return {"inventory": [
                {"slot": n, "id": "minecraft:stone_pickaxe" if n == 0 else "minecraft:dirt", "count": 1}
                for n in range(self.used_slots)
            ]}
        if route == "find_blocks":
            return {"found": [
                {"x": x, "y": y, "z": z} for (x, y, z), b in self.blocks.items() if b in payload["blocks"]
            ]}
        if route == "dig_block":
            cell = (payload["x"], payload["y"], payload["z"])
            block = self.blocks.pop(cell, AIR)
            self.dug.append((cell, block))
            if block == IRON:
                self.raw_iron += 1
            if self.hostile_after_digs is not None and len(self.dug) >= self.hostile_after_digs:
                self.entities = [{"type": "minecraft:zombie", "distance": 5.0}]
            return {"started": True}
        return {}

    def goto(self, _client, x, y, z, **_kw):
        target = (x, y, z)
        if self.open(target) and self.open((x, y + 1, z)):
            self.pos = target
            return True
        return False

    def place(self, _client, x, y, z, block, allow_break=True):
        self.blocks[(x, y, z)] = block
        self.placed.append((x, y, z))
        self.torches -= 1
        return True


@pytest.fixture
def world(monkeypatch):
    w = FakeWorld()
    monkeypatch.setattr("baritone_client.common.navigation.goto", w.goto)
    monkeypatch.setattr("baritone_client.common.harness_ops.place_block_exact", w.place)
    monkeypatch.setattr("baritone_client.common.resources.equip_best_pickaxe", lambda _c: True)
    monkeypatch.setattr(tm.time, "sleep", lambda _s: None)
    return w


def make_miner(world, seconds=10_000.0, **kwargs):
    deadline = tm.time.monotonic() + seconds
    return tm.TunnelMiner(
        world, surface_y=70, deadline=deadline, entrance=ENTRANCE, min_y=12,
        count_fn=lambda item: world.torches if item == "minecraft:torch" else 0, **kwargs,
    )


def test_digs_a_staircase_to_iron_lights_it_and_retreats(world):
    for cell in [(6, 55, 3), (7, 55, 3), (6, 54, 3)]:
        world.blocks[cell] = IRON
    miner = make_miner(world)

    reason = miner.mine(3, lambda: world.raw_iron)

    assert reason == "quota" and world.raw_iron == 3
    assert miner.stats.moves > 15
    # Every dug block was stone, soil or ore: nothing else is ever broken.
    allowed = tp.STONE | {IRON, AIR}
    assert all(block in allowed for _cell, block in world.dug)
    # Lit every few moves once underground, never at the surface.
    assert miner.stats.torches >= 2 and all(y < 67 for _x, y, _z in world.placed)
    # The bot walks its own trail back to the entrance.
    assert miner.retreat() is True and world.pos[1] >= 68


def test_iron_beside_lava_is_never_dug_and_a_clean_vein_is(world):
    world.blocks[(6, 55, 3)] = IRON
    for dx, dy, dz in tp.NEIGHBOURS:  # lava touching the ore on every side
        world.blocks.setdefault((6 + dx, 55 + dy, 3 + dz), "minecraft:lava")
    world.blocks[(6, 55, 3)] = IRON
    world.blocks[(-5, 52, -4)] = IRON  # a clean ore on the other side
    miner = make_miner(world)

    miner.mine(1, lambda: world.raw_iron)

    assert world.raw_iron == 1
    dug_cells = {cell for cell, _b in world.dug}
    lava = {c for c, b in world.blocks.items() if b == "minecraft:lava"}
    for cell in dug_cells:
        assert not any((cell[0] + dx, cell[1] + dy, cell[2] + dz) in lava for dx, dy, dz in tp.NEIGHBOURS)


def test_a_natural_cave_is_never_entered(world):
    for x in range(-8, 9):
        for z in range(-8, 9):
            for y in (52, 53, 54):
                world.blocks.pop((x, y, z), None)  # a wide cavern below the entrance
    world.blocks[(0, 50, 0)] = IRON
    miner = make_miner(world)

    reason = miner.mine(1, lambda: world.raw_iron)

    assert world.raw_iron == 0 and reason in ("no safe route", "no reachable iron")
    assert all(cell[1] > 54 or cell[1] < 52 for cell, _b in world.dug)  # never opened a cavern cell


def test_a_hostile_mob_ends_the_trip_after_the_defence_has_had_its_chance(world):
    world.blocks[(6, 55, 3)] = IRON
    world.hostile_after_digs = 6
    miner = make_miner(world)

    with pytest.raises(tm.MineAbort) as stop:
        miner.mine(1, lambda: world.raw_iron)

    assert stop.value.reason == "hostile mob"
    assert miner.retreat() is False  # a persistent nearby threat still blocks travel
    assert world.pos in miner.trail


def test_low_health_and_the_clock_stop_the_trip(world):
    world.blocks[(6, 55, 3)] = IRON
    world.health = 9.0
    with pytest.raises(tm.MineAbort) as stop:
        make_miner(world).mine(1, lambda: world.raw_iron)
    assert stop.value.reason == "health low"

    world.health = 20.0
    with pytest.raises(tm.MineAbort) as stop:
        make_miner(world, seconds=-1.0).mine(1, lambda: world.raw_iron)
    assert stop.value.reason == "time"


def test_a_structure_block_in_the_way_aborts_instead_of_breaking_it(world):
    world.blocks[(1, 70, 0)] = "minecraft:chest"
    miner = make_miner(world)
    move = tp.Move(ENTRANCE, (1, 70, 0), ((1, 70, 0), (1, 71, 0)), ((1, 70, 0),))
    with pytest.raises(tm.MineAbort) as stop:
        miner.advance(move)
    assert stop.value.reason.startswith("unexpected block") and world.dug == []


def test_a_recorded_spine_is_walked_and_its_cells_count_as_our_own(world):
    # A tunnel from an earlier trip: its open cells must not read as a cave.
    spine = [(0, 70, 0), (1, 69, 0), (2, 68, 0), (3, 67, 0)]
    for frm, to in zip(spine, spine[1:]):
        for cell in tp.required_cells(frm, to):
            world.blocks.pop(cell, None)
    miner = make_miner(world, spine=spine)

    assert tm.own_cells_of(spine) >= {(2, 68, 0), (2, 69, 0), (3, 67, 0), (3, 68, 0)}
    miner.descend(spine)
    assert world.pos == (3, 67, 0)
    assert world.allow_break == "true"


def test_spine_traversal_retries_a_transient_waypoint_failure_and_never_skips_cells(world, monkeypatch):
    spine = [(0, 70, 0), (1, 69, 0), (2, 68, 0)]
    for frm, to in zip(spine, spine[1:]):
        for cell in tp.required_cells(frm, to):
            world.blocks.pop(cell, None)
    real_goto = world.goto
    calls = []

    def transient(client, x, y, z, **kw):
        assert world.allow_break == "false"
        calls.append((x, y, z))
        if len(calls) == 1:
            return False
        return real_goto(client, x, y, z, **kw)

    monkeypatch.setattr("baritone_client.common.navigation.goto", transient)
    miner = make_miner(world, spine=spine)
    miner.descend(spine)

    assert calls == [(1, 69, 0), (1, 69, 0), (2, 68, 0)]
    assert world.pos == spine[-1] and world.allow_break == "true"


def test_retreat_requires_exact_xyz_and_refuses_an_off_trail_position(world):
    spine = [(0, 70, 0), (1, 69, 0), (2, 68, 0)]
    for frm, to in zip(spine, spine[1:]):
        for cell in tp.required_cells(frm, to):
            world.blocks.pop(cell, None)
    miner = make_miner(world, spine=spine)
    miner.descend(spine)

    world.pos = (2, 68, 1)  # matching height is not proof of reaching the entrance
    assert miner.retreat() is False
    assert world.pos == (2, 68, 1)


def test_recorded_spine_refuses_a_floor_removed_since_last_trip(world):
    spine = [(0, 70, 0), (1, 69, 0)]
    for cell in tp.required_cells(*spine):
        world.blocks.pop(cell, None)
    world.blocks.pop((1, 68, 0), None)
    miner = make_miner(world, spine=spine)
    with pytest.raises(tm.MineAbort, match="no longer supported"):
        miner.descend(spine)
    assert world.pos == spine[0]
    assert world.dug == []
    assert world.allow_break == "true"


def test_retreat_walks_each_recorded_cell_and_proves_the_entrance(world):
    spine = [(0, 70, 0), (1, 69, 0), (2, 68, 0)]
    for frm, to in zip(spine, spine[1:]):
        for cell in tp.required_cells(frm, to):
            world.blocks.pop(cell, None)
    miner = make_miner(world, spine=spine)
    miner.descend(spine)

    assert miner.retreat() is True
    assert world.pos == spine[0] and world.allow_break == "true"


def test_adjacent_ore_under_a_recorded_route_cell_is_not_mined(world):
    ore = (1, 70, 0)
    world.blocks[ore] = IRON
    miner = make_miner(world)
    miner.trail.append((1, 71, 0))  # ore is the support floor for this old cell
    view = miner.view()

    assert not miner.mine_adjacent(view, ENTRANCE, lambda: world.raw_iron)
    assert world.blocks[ore] == IRON
    assert world.raw_iron == 0 and not world.dug


def test_advance_refuses_a_plan_that_would_remove_any_recorded_floor(world):
    miner = make_miner(world)
    world.pos = (1, 70, 0)
    miner.trail.append(world.pos)
    move = tp.Move(
        world.pos, (1, 70, 1), ((1, 70, 1), (1, 71, 1)),
        ((1, 69, 0),),
    )

    with pytest.raises(tm.MineAbort, match="recorded tunnel floor"):
        miner.advance(move)
    assert world.blocks[(1, 69, 0)] == "minecraft:grass_block"
    assert not world.dug


def test_torch_placement_gives_up_after_three_misses_but_mining_goes_on(world, monkeypatch):
    world.blocks[(6, 55, 3)] = IRON
    attempts = []
    monkeypatch.setattr(
        "baritone_client.common.harness_ops.place_block_exact",
        lambda *_a, **_k: attempts.append(1) or False,
    )
    miner = make_miner(world)

    miner.mine(1, lambda: world.raw_iron)

    assert world.raw_iron == 1 and len(attempts) == 3 and miner.stats.torches == 0


def test_rubble_is_thrown_away_when_the_pack_runs_low_and_the_trip_goes_on(world, monkeypatch):
    world.blocks[(6, 55, 3)] = IRON
    world.used_slots = 35  # only one slot free
    thrown = []

    def drop(_client, items, max_stacks=None, retain_counts=None):
        thrown.append(set(items))
        world.used_slots = 20
        return 8

    monkeypatch.setattr("baritone_client.common.inventory.drop_items", drop)

    miner = make_miner(world)
    miner.mine(1, lambda: world.raw_iron)

    assert world.raw_iron == 1 and thrown
    assert "minecraft:cobblestone" in thrown[0] and "minecraft:iron_ore" not in thrown[0]
    assert not any(item.endswith(("pickaxe", "sword", "torch", "ingot")) for item in thrown[0])


def test_a_pack_that_cannot_be_cleared_ends_the_trip_cleanly(world, monkeypatch):
    world.blocks[(6, 55, 3)] = IRON
    world.used_slots = 35
    monkeypatch.setattr("baritone_client.common.inventory.drop_items", lambda *_a, **_k: 0)
    assert make_miner(world).mine(1, lambda: world.raw_iron) == "inventory full"
    assert world.dug == []  # never starts digging with nowhere to put the drops


def test_invalid_inventory_snapshot_is_zero_free_slots_and_fails_closed():
    broken = SimpleNamespace(
        transport=SimpleNamespace(dispatch=lambda route, _payload: {} if route == "get_inventory" else {})
    )
    assert tm.free_slots(broken) == 0

    missing_slot = SimpleNamespace(transport=SimpleNamespace(dispatch=lambda route, _payload: {
        "inventory": [{"id": "minecraft:stone", "count": 64}]
    }))
    assert tm.free_slots(missing_slot) == 0
