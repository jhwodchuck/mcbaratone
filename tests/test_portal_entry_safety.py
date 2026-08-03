from types import SimpleNamespace

from baritone_client.common import nether


class _Transport:
    def __init__(self, blocks=None):
        self.blocks = blocks or {}
        self.calls = []

    def dispatch(self, route, payload):
        self.calls.append((route, dict(payload)))
        if route == "get_state":
            return {"dimension": "minecraft:the_nether"}
        if route == "get_block":
            return {"id": self.blocks.get(
                (payload["x"], payload["y"], payload["z"]),
                "minecraft:air",
            )}
        if route == "goto":
            return {"success": True}
        return {}


def test_enter_portal_refuses_stale_coordinate_without_goto(monkeypatch):
    transport = _Transport()
    client = SimpleNamespace(transport=transport)
    monkeypatch.setattr(
        nether,
        "_wait_for_dimension",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("inactive portal must not start a dimension wait")
        ),
    )

    assert not nether.enter_portal(
        client,
        (-132, 78, -7),
        target_dimension="minecraft:overworld",
        timeout=1,
    )
    assert not any(route == "goto" for route, _payload in transport.calls)


def test_enter_portal_approaches_live_portal_block(monkeypatch):
    portal = (-132, 78, -7)
    transport = _Transport({portal: "minecraft:nether_portal"})
    client = SimpleNamespace(transport=transport)
    monkeypatch.setattr(nether, "_wait_for_dimension", lambda *_args: True)

    assert nether.enter_portal(
        client,
        portal,
        target_dimension="minecraft:overworld",
        timeout=1,
    )
    goto_calls = [payload for route, payload in transport.calls if route == "goto"]
    assert goto_calls == [{"x": -132, "y": 78, "z": -7, "radius": 0}]
