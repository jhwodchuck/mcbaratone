from types import SimpleNamespace

from baritone_client.common.navigation import find_nearby_block


class SearchTransport:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def dispatch(self, route, payload):
        assert route == "find_blocks"
        self.calls.append(payload)
        return {"found": self.responses.get(payload["radius"], [])}


def test_find_nearby_block_searches_outward_and_sorts_by_distance():
    transport = SearchTransport(
        {
            8: [],
            16: [
                {"x": -16, "y": 64, "z": 0, "distance": 16.0},
                {"x": 9, "y": 64, "z": 0, "distance": 9.0},
            ],
        }
    )
    client = SimpleNamespace(transport=transport)

    assert find_nearby_block(client, ["minecraft:oak_log"], radius=128) == (9, 64, 0)
    assert [call["radius"] for call in transport.calls] == [8, 16]
    assert all(call["limit"] == 4096 for call in transport.calls)


def test_find_nearby_block_honors_a_small_requested_radius():
    transport = SearchTransport(
        {5: [{"x": 1, "y": 63, "z": 2, "distance": 2.25}]}
    )
    client = SimpleNamespace(transport=transport)

    assert find_nearby_block(client, ["minecraft:stone"], radius=5) == (1, 63, 2)
    assert [call["radius"] for call in transport.calls] == [5]
