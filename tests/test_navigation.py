from types import SimpleNamespace

from baritone_client.common import navigation


def test_goto_fails_fast_when_goal_is_rejected_without_movement(monkeypatch):
    class Transport:
        def __init__(self):
            self.calls = []

        def dispatch(self, route, payload):
            self.calls.append((route, dict(payload)))
            if route == "get_state":
                return {
                    "health": 20,
                    "food_level": 20,
                    "is_pathing": False,
                    "block_position": {"x": -145, "y": 66, "z": -286},
                }
            return {}

    transport = Transport()
    client = SimpleNamespace(transport=transport)
    monkeypatch.setattr(navigation.time, "sleep", lambda _seconds: None)

    assert not navigation.goto(client, -160, 104, -388, timeout=300)

    state_reads = [call for call in transport.calls if call[0] == "get_state"]
    assert len(state_reads) == 3
    assert transport.calls[-1][0] == "cancel"


def test_staged_goto_targets_loaded_column_surface(monkeypatch):
    class Transport:
        def dispatch(self, route, payload):
            if route == "get_block":
                return {
                    "id": (
                        "minecraft:water"
                        if payload["y"] <= 62
                        else "minecraft:air"
                    )
                }
            return {}

    destinations = []

    def navigate(_client, x, y, z, **_kwargs):
        destinations.append((x, y, z))
        return True

    assert navigation.staged_goto(
        SimpleNamespace(transport=Transport()),
        (-160, 104, -388),
        (-145, 66, -286),
        navigate=navigate,
    )

    assert destinations[0] == (-150, 63, -318)
    assert destinations[-1] == (-160, 104, -388)
