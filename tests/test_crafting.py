from baritone_client import Client


class DummyTransport:
    def __init__(self, responses=None):
        self.calls = []
        self.responses = responses or {}

    def dispatch(self, route, payload):
        self.calls.append((route, payload))
        # Return configured response or a default shape
        return self.responses.get(route, {"ok": True})


def test_craft_delegates_to_transport():
    t = DummyTransport(responses={"craft": {"crafted": True, "output_slot": 5}})
    client = Client(t)
    res = client.command.craft("minecraft:stone_pickaxe", count=1)
    assert res.get("crafted") is True
    assert t.calls and t.calls[-1][0] == "craft"


def test_smelt_delegates_and_returns_count():
    t = DummyTransport(responses={"smelt": {"smelted": True, "output_count": 4}})
    client = Client(t)
    res = client.command.smelt("minecraft:iron_ore", count=4)
    assert res.get("smelted") is True
    assert res.get("output_count") == 4
    assert t.calls and t.calls[-1][0] == "smelt"
