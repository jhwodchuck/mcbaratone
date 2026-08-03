from types import SimpleNamespace

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
            if route == "break_block":
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
    assert ("break_block", {"x": 2, "y": 112, "z": 3}) in client.transport.calls
