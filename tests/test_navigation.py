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
