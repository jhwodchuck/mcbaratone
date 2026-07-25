from types import SimpleNamespace

from baritone_client.automator.phases import nether_prep
from baritone_client.automator.resource_manager import ResourceManager
from baritone_client.automator.state_manager import Phase, StateManager
from baritone_client.common import nether
from baritone_client.common.tasks import TaskResult


class MissionStub:
    def __init__(self):
        self.checkpoints = []

    def macro(self, name, params):
        assert name == "enter_nether"
        return {"result": {"ready": params["obsidian"] >= 14}}

    def checkpoint(self, phase, note=None):
        self.checkpoints.append((phase, note))


class PortalTransport:
    def __init__(self):
        self.dimension = "minecraft:overworld"
        self.blocks = {}
        self.calls = []

    def dispatch(self, route, payload, **_kwargs):
        self.calls.append((route, dict(payload)))
        if route == "get_state":
            return {
                "dimension": self.dimension,
                "block_position": {"x": 0, "y": 64, "z": 0},
            }
        if route == "get_block":
            key = (payload["x"], payload["y"], payload["z"])
            return {"id": self.blocks.get(key, "minecraft:air")}
        if route == "place_fire":
            x, y, z = payload["x"], payload["y"], payload["z"]
            for dx in (0, 1):
                for dy in (0, 1, 2):
                    self.blocks[(x + dx, y + dy, z)] = "minecraft:nether_portal"
            return {"ignited": True}
        if route == "goto":
            self.dimension = (
                "minecraft:the_nether"
                if "nether" not in self.dimension
                else "minecraft:overworld"
            )
            return {"started": True}
        if route == "find_blocks":
            return {"found": []}
        return {}


def test_build_ignite_verify_and_enter_portal(monkeypatch):
    transport = PortalTransport()
    client = SimpleNamespace(transport=transport, mission=MissionStub())
    portal = (3, 64, 0)

    def place(_client, x, y, z, item_id):
        assert item_id == "minecraft:obsidian"
        transport.blocks[(x, y, z)] = item_id
        return True

    monkeypatch.setattr(nether, "place_block", place)
    monkeypatch.setattr(nether.time, "sleep", lambda _seconds: None)

    assert nether.build_nether_portal(client, *portal)
    assert nether.verify_portal(client, portal, require_active=False)
    assert not nether.verify_portal(client, portal, require_active=True)
    assert nether.ignite_portal(client, portal)
    assert nether.verify_portal(client, portal, require_active=True)
    assert nether.enter_portal(
        client, portal, target_dimension="minecraft:the_nether", timeout=1
    )
    goto = [payload for route, payload in transport.calls if route == "goto"][-1]
    assert goto == {"x": 4, "y": 65, "z": 0, "radius": 0}


def test_find_nearest_portal_uses_supported_unwrapped_find_blocks_route():
    class FindTransport(PortalTransport):
        def dispatch(self, route, payload, **kwargs):
            if route == "find_blocks":
                assert payload["blocks"] == ["minecraft:nether_portal"]
                return {
                    "found": [
                        {"x": 20, "y": 70, "z": 20, "distance": 30.0},
                        {"x": 3, "y": 65, "z": 4, "distance": 5.0},
                    ]
                }
            return super().dispatch(route, payload, **kwargs)

    transport = FindTransport()
    client = SimpleNamespace(transport=transport)
    assert nether.find_nearest_portal(client) == (3, 65, 4)


def test_fortress_detection_uses_find_blocks_and_unwrapped_response(monkeypatch):
    class FortressTransport(PortalTransport):
        def dispatch(self, route, payload, **kwargs):
            if route == "find_blocks":
                assert "minecraft:nether_bricks" in payload["blocks"]
                return {
                    "found": [
                        {
                            "x": 100 + index,
                            "y": 64,
                            "z": 200,
                            "block": "minecraft:nether_bricks",
                        }
                        for index in range(40)
                    ]
                }
            return super().dispatch(route, payload, **kwargs)

    transport = FortressTransport()
    transport.dimension = "minecraft:the_nether"
    client = SimpleNamespace(transport=transport)
    monkeypatch.setattr(nether.time, "sleep", lambda _seconds: None)
    assert nether.find_nether_fortress(client, timeout=1) == (119, 64, 200)
    assert not any(route == "scan_blocks" for route, _ in transport.calls)


def test_nether_handler_persists_portal_pair_and_fortress(monkeypatch, tmp_path):
    transport = PortalTransport()
    client = SimpleNamespace(transport=transport, mission=MissionStub())
    resources = ResourceManager(client)
    state = StateManager(checkpoint_dir=tmp_path)
    handler = nether_prep.NetherAndBlazeHandler()
    rods = {"count": 0}

    monkeypatch.setattr(nether_prep, "_ensure_raw_planks", lambda *_args: True)
    monkeypatch.setattr(
        nether_prep,
        "ensure_supplies",
        lambda *_args, **_kwargs: TaskResult.ok(),
    )
    monkeypatch.setattr(
        nether_prep,
        "_read_state_with_retry",
        lambda *_args, **_kwargs: (
            {"block_position": {"x": 0, "y": 64, "z": 0}},
            None,
        ),
    )
    monkeypatch.setattr(nether_prep, "build_nether_portal", lambda *_args: True)
    monkeypatch.setattr(nether_prep, "ignite_portal", lambda *_args: True)
    monkeypatch.setattr(
        nether_prep,
        "verify_portal",
        lambda *_args, **_kwargs: True,
    )

    def enter(_client, _portal, *, target_dimension, timeout):
        transport.dimension = target_dimension
        return True

    monkeypatch.setattr(nether_prep, "enter_portal", enter)
    monkeypatch.setattr(
        nether_prep,
        "find_nearest_portal",
        lambda *_args: (100, 65, 100),
    )
    monkeypatch.setattr(
        nether_prep,
        "find_nether_fortress",
        lambda *_args: (240, 70, -80),
    )

    def hunt(_client, target_count):
        assert target_count == 6
        rods["count"] = 6
        return 6

    monkeypatch.setattr(nether_prep, "hunt_blazes", hunt)
    monkeypatch.setattr(
        nether_prep,
        "count_item",
        lambda *_args: rods["count"],
    )

    result = handler.execute(client, resources, state)
    assert result.success
    assert transport.dimension == "minecraft:overworld"
    portals = state.get_locations("nether_portal")["nether_portal"]
    assert {entry["dimension"] for entry in portals} == {
        "overworld",
        "the_nether",
    }
    fortress = state.get_locations("nether_fortress")["nether_fortress"]
    assert (fortress[0]["x"], fortress[0]["y"], fortress[0]["z"]) == (
        240,
        70,
        -80,
    )
    assert state.get_phase_payload(Phase.NETHER_AND_BLAZE)["blaze_rods"] == 6
