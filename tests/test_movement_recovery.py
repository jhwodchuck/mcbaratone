from types import SimpleNamespace

import pytest

from baritone_client.common import shelf_escape, surface_egress
from baritone_client.common.movement_recovery import (
    ExplorationWaypoints,
    MovementWatchdog,
    nearest_lower_surface_candidates,
)


def test_watchdog_rejects_pathing_without_real_displacement():
    watchdog = MovementWatchdog()
    state = {
        "is_pathing": True,
        "block_position": {"x": 12, "y": 64, "z": -3},
    }

    watchdog.reset(state)
    for _ in range(4):
        assert not watchdog.observe(state)

    assert watchdog.stalled(4)


def test_exploration_waypoints_never_target_the_current_origin():
    waypoints = ExplorationWaypoints(10, -20, maximum_radius=32)

    targets = [waypoints.next() for _ in range(8)]

    assert all(target != (10, -20) for target in targets)
    assert len(set(targets)) == 8


def test_lower_surface_candidates_prefer_short_reachable_descents():
    response = {
        "found": [
            {"x": -21, "y": 97, "z": -31},
            {"x": -40, "y": 70, "z": 20},  # too deep/far
            {"x": -9, "y": 112, "z": -15},  # no meaningful descent
            {"x": -15, "y": 106, "z": -20},
        ]
    }

    candidates = list(
        nearest_lower_surface_candidates(response, (-8, 113, -15))
    )

    assert candidates[0] == (-15, 107, -20)
    assert (-21, 98, -31) in candidates


def test_surface_egress_tries_local_lower_ground_before_remote_storage(monkeypatch):
    class Transport:
        def __init__(self):
            self.calls = []

        def dispatch(self, route, payload):
            self.calls.append((route, payload))
            if route == "find_blocks":
                return {
                    "found": [
                        {"x": -15, "y": 106, "z": -20, "distance": 10.0}
                    ]
                }
            if route == "get_state":
                return {"block_position": {"x": -15, "y": 107, "z": -20}}
            return {}

    client = SimpleNamespace(transport=Transport())
    destinations = []
    monkeypatch.setattr(
        surface_egress,
        "goto",
        lambda _client, x, y, z, **_kwargs: destinations.append((x, y, z))
        or True,
    )

    result = surface_egress.try_lower_surface_egress(
        client,
        {"block_position": {"x": -8, "y": 113, "z": -15}},
    )

    assert result == (-15, 107, -20)
    assert destinations == [(-15, 107, -20)]


def test_proven_marooned_egress_excavates_up_when_lower_routes_fail(monkeypatch):
    class Transport:
        def dispatch(self, route, payload):
            if route == "get_block":
                return {"id": "minecraft:stone"}
            if route == "find_blocks":
                return {"found": []}
            if route == "get_state":
                return {
                    "health": 20,
                    "block_position": {"x": 7, "y": 64, "z": -191},
                }
            return {}

    client = SimpleNamespace(transport=Transport())
    monkeypatch.setattr(
        surface_egress,
        "supported_column_descent",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        surface_egress,
        "harvest_shelf_dirt",
        lambda *_args, **_kwargs: 0,
    )
    monkeypatch.setattr(
        surface_egress,
        "try_survivable_drop",
        lambda *_args, **_kwargs: None,
    )
    excavations = []
    monkeypatch.setattr(
        "baritone_client.common.build_site_recovery.excavate_surface_egress",
        lambda _client, **kwargs: excavations.append(kwargs) or (7, 70, -191),
    )

    reached = surface_egress.try_lower_surface_egress(
        client,
        {"health": 20, "block_position": {"x": 7, "y": 64, "z": -191}},
        minimum_altitude=0,
        allow_upward_excavation=True,
    )

    assert reached == (7, 70, -191)
    assert excavations == [
        {
            "origin": (7, 64, -191),
            "expected_y": 70,
            "timeout_per_attempt": 30.0,
        }
    ]


def test_proven_marooned_egress_descends_by_hand_after_tunnels_fail(
    monkeypatch,
):
    position = {"x": 7, "y": 64, "z": -191}

    class Transport:
        def dispatch(self, route, payload):
            if route == "get_block":
                return {"id": "minecraft:stone"}
            if route == "find_blocks":
                return {"found": []}
            if route == "get_state":
                return {"health": 20, "block_position": dict(position)}
            return {}

    client = SimpleNamespace(transport=Transport())
    monkeypatch.setattr(
        surface_egress,
        "supported_column_descent",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        surface_egress,
        "harvest_shelf_dirt",
        lambda *_args, **_kwargs: 0,
    )
    monkeypatch.setattr(
        surface_egress,
        "try_survivable_drop",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "baritone_client.common.build_site_recovery.excavate_surface_egress",
        lambda *_args, **_kwargs: None,
    )
    descents = []

    def descend(_client, **kwargs):
        descents.append(kwargs)
        position["y"] = 59
        return True

    monkeypatch.setattr(
        "baritone_client.common.stone_descent.manual_column_descend",
        descend,
    )

    reached = surface_egress.try_lower_surface_egress(
        client,
        {"health": 20, "block_position": {"x": 7, "y": 64, "z": -191}},
        minimum_altitude=0,
        allow_upward_excavation=True,
    )

    assert reached == (7, 59, -191)
    assert descents == [
        {
            "target_y": 58,
            "max_steps": 6,
            "require_pickaxe": False,
        }
    ]


def test_low_altitude_egress_keeps_descent_override_after_support_exists(
    monkeypatch,
):
    """A newly placed support must not switch the next pass back to y>=96."""

    class Transport:
        def dispatch(self, route, _payload):
            if route == "get_block":
                return {"id": "minecraft:cobblestone"}
            if route == "get_state":
                return {"block_position": {"x": -132, "y": 77, "z": -7}}
            if route == "find_blocks":
                return {"found": []}
            return {}

    calls = []
    monkeypatch.setattr(
        surface_egress,
        "supported_column_descent",
        lambda *_args, **kwargs: calls.append(kwargs) or (-132, 76, -7),
    )

    reached = surface_egress.try_lower_surface_egress(
        SimpleNamespace(transport=Transport()),
        {"block_position": {"x": -132, "y": 77, "z": -7}},
        minimum_altitude=0,
    )

    assert reached == (-132, 76, -7)
    assert calls == [{"minimum_altitude": 0, "target_y": 53}]


def test_shelf_escape_harvests_vacated_blocks_not_current_floor(monkeypatch):
    class Transport:
        def __init__(self):
            self.dirt = 0
            self.broken = set()
            self.calls = []
            self.position = {"x": 0, "y": 113, "z": 0}

        def dispatch(self, route, payload):
            self.calls.append((route, payload))
            if route == "goto":
                self.position = {
                    "x": payload["x"],
                    "y": payload["y"],
                    "z": payload["z"],
                }
                return {"started": True}
            if route == "get_state":
                return {"block_position": self.position, "health": 20}
            if route == "get_inventory":
                return {
                    "inventory": [
                        {"id": "minecraft:dirt", "count": self.dirt, "slot": 0}
                    ]
                }
            if route == "find_blocks":
                return {
                    "found": [
                        {"x": x, "y": 112, "z": 0}
                        for x in range(4)
                    ]
                }
            if route == "get_block":
                key = (payload["x"], payload["y"], payload["z"])
                if payload["y"] == 111 or key in self.broken:
                    return {"id": "minecraft:air"}
                return {"id": "minecraft:grass_block"}
            if route == "break_block":
                key = (payload["x"], payload["y"], payload["z"])
                self.broken.add(key)
                self.dirt += 1
                return {"started": True}
            return {}

    client = SimpleNamespace(transport=Transport())
    monkeypatch.setattr(shelf_escape.time, "sleep", lambda _s: None)

    count = shelf_escape.harvest_shelf_dirt(
        client,
        {"block_position": {"x": 0, "y": 113, "z": 0}},
        target_count=2,
    )

    assert count == 2
    assert (0, 112, 0) in client.transport.broken
    assert (1, 112, 0) in client.transport.broken
    assert (2, 112, 0) not in client.transport.broken


def test_supported_column_descent_places_support_before_break(monkeypatch):
    from baritone_client.common import automation_utils

    class Transport:
        def __init__(self):
            self.position = {"x": 2, "y": 113, "z": 3}
            self.calls = []

        def dispatch(self, route, payload):
            self.calls.append((route, payload))
            if route == "get_block":
                return {"id": "minecraft:air"}
            if route == "get_inventory":
                return {
                    "inventory": [
                        {"id": "minecraft:spruce_planks", "count": 1}
                    ]
                }
            if route == "dig_block":
                self.position["y"] -= 1
                return {"started": True}
            if route == "get_state":
                return {"block_position": self.position, "health": 20}
            return {}

    client = SimpleNamespace(transport=Transport())
    placed = []
    monkeypatch.setattr(
        automation_utils,
        "place_block",
        lambda _client, x, y, z, item: placed.append((x, y, z, item)) or True,
    )
    monkeypatch.setattr(shelf_escape.time, "sleep", lambda _seconds: None)

    result = shelf_escape.supported_column_descent(
        client,
        {"block_position": {"x": 2, "y": 113, "z": 3}},
        target_y=112,
    )

    assert result == (2, 112, 3)
    assert placed == [(2, 111, 3, "minecraft:spruce_planks")]
    assert ("dig_block", {"x": 2, "y": 112, "z": 3}) in client.transport.calls


# -- stranded on its own bridge with nothing to place (A1 2026-11-08) ----------
COBBLE = "minecraft:cobblestone"


def _a1_bridge():
    """The live structure: a bridge at y=106 ending in a pillar on a tree."""
    blocks = {(-367, 106, z): COBBLE for z in range(53, 86)}
    blocks[(-366, 106, 85)] = COBBLE
    blocks.update({(-367, y, 85): COBBLE for y in range(94, 106)})
    blocks[(-367, 93, 85)] = "minecraft:oak_leaves"
    # Live: the nearest ground, in view but out of reach without blocks.
    blocks[(-359, 97, 66)] = "minecraft:stone"
    return blocks


class _Scaffold:
    def __init__(self, position, blocks):
        self.position = list(position)
        self.blocks = dict(blocks)
        self.digs, self.routes = [], []

    def state(self):
        x, y, z = self.position
        return {
            "health": 20.0,
            "food_level": 6,
            "is_pathing": False,
            "block_position": {"x": x, "y": y, "z": z},
            "position": {"x": x + 0.5, "y": y, "z": z + 0.5},
        }

    def dispatch(self, route, payload=None, **_kwargs):
        if route == "get_state":
            return self.state()
        if route == "get_block":
            cell = (payload["x"], payload["y"], payload["z"])
            return {"id": self.blocks.get(cell, "minecraft:air")}
        if route == "find_blocks":
            return {"found": [
                {"x": x, "y": y, "z": z}
                for (x, y, z), block in self.blocks.items()
                if block in payload["blocks"]
            ]}
        if route == "dig_block":
            cell = (payload["x"], payload["y"], payload["z"])
            self.digs.append(cell)
            del self.blocks[cell]
            x, _y, z = self.position
            while (x, self.position[1] - 1, z) not in self.blocks:
                self.position[1] -= 1
        return {}

    def goto(self, _client, x, y, z, **_kwargs):
        self.routes.append((x, y, z))
        if self.blocks.get((x, y - 1, z)) != COBBLE:
            return False  # only the bridge itself is walkable
        self.position = [x, y, z]
        return True


def _stranded(monkeypatch, position, blocks=None):
    from baritone_client.common import resources, stone_descent

    world = _Scaffold(position, _a1_bridge() if blocks is None else blocks)
    monkeypatch.setattr(surface_egress, "goto", world.goto)
    monkeypatch.setattr(stone_descent.time, "sleep", lambda *_a: None)
    monkeypatch.setattr(resources, "_ensure_mining_pickaxe", lambda *_a: True)
    monkeypatch.setattr(resources, "select_item", lambda *_a, **_k: True)
    return world, SimpleNamespace(transport=world)


def test_a_bot_stranded_mid_bridge_walks_to_its_pillar_and_digs_down(monkeypatch):
    """A1 gave up for 18 hours with its way down in plain reach."""
    world, client = _stranded(monkeypatch, (-367, 107, 64))
    monkeypatch.setattr(
        surface_egress, "try_survivable_drop",
        lambda *_a, **_k: pytest.fail("the pillar costs no fall damage"),
    )

    reached = surface_egress.try_lower_surface_egress(
        client, world.state(), attempt_limit=6, timeout_per_candidate=18.0)

    assert world.routes[-1] == (-367, 107, 85)
    assert reached == (-367, 95, 85)
    assert world.digs == [(-367, y, 85) for y in range(106, 94, -1)]
    assert (-367, 94, 85) in world.blocks, "the last block stays as the floor"


def test_a_bot_already_on_its_pillar_digs_without_walking(monkeypatch):
    world, client = _stranded(monkeypatch, (-367, 107, 85))
    assert surface_egress.descend_own_pillar(client, world.state()) == (-367, 95, 85)
    assert world.routes == []


def test_the_nearest_pillar_is_used(monkeypatch):
    blocks = _a1_bridge()
    blocks.update({(-367, y, 53): COBBLE for y in range(100, 106)})
    blocks[(-367, 99, 53)] = "minecraft:stone"
    world, client = _stranded(monkeypatch, (-367, 107, 60), blocks)
    assert surface_egress.descend_own_pillar(client, world.state()) == (-367, 101, 53)


def test_ordinary_ground_is_never_dug_out_as_a_pillar(monkeypatch):
    hill = {
        (x, y, z): COBBLE
        for x in range(-3, 4) for z in range(-3, 4) for y in range(60, 64)
    }
    hill.update({(x, 59, z): "minecraft:stone" for x in range(-3, 4) for z in range(-3, 4)})
    world, client = _stranded(monkeypatch, (0, 64, 0), hill)
    assert surface_egress.descend_own_pillar(client, world.state()) is None
    assert world.digs == [] and world.routes == []


def test_a_column_hanging_over_air_is_not_dug(monkeypatch):
    blocks = _a1_bridge()
    del blocks[(-367, 93, 85)]
    world, client = _stranded(monkeypatch, (-367, 107, 64), blocks)
    assert surface_egress.descend_own_pillar(client, world.state()) is None
    assert world.digs == []


def test_a_stub_too_short_to_matter_is_ignored(monkeypatch):
    blocks = {(0, 63, 0): COBBLE, (0, 62, 0): COBBLE, (0, 61, 0): "minecraft:stone"}
    world, client = _stranded(monkeypatch, (0, 64, 0), blocks)
    assert surface_egress.descend_own_pillar(client, world.state()) is None
    assert world.digs == []


def test_an_unread_side_is_not_proof_of_a_pillar(monkeypatch):
    world, client = _stranded(monkeypatch, (-367, 107, 85))
    plain = world.dispatch

    def blind(route, payload=None, **kwargs):
        if route == "get_block" and (payload["x"], payload["y"], payload["z"]) == (-366, 105, 85):
            return {"id": ""}
        return plain(route, payload, **kwargs)

    world.dispatch = blind
    assert surface_egress.descend_own_pillar(client, world.state()) is None
    assert world.digs == []


def test_the_bot_never_digs_where_it_did_not_arrive(monkeypatch):
    blocks = _a1_bridge()
    blocks[(-367, 105, 64)] = COBBLE  # diggable here, so only arrival protects it
    world, client = _stranded(monkeypatch, (-367, 107, 64), blocks)
    monkeypatch.setattr(surface_egress, "goto", lambda *_a, **_k: True)
    assert surface_egress.descend_own_pillar(client, world.state()) is None
    assert world.digs == []
