from baritone_client.common import inventory


class DummyTransport:
    def __init__(self, responses=None):
        self.responses = responses or {}
        self.calls = []

    def dispatch(self, route, payload):
        self.calls.append((route, payload))
        return self.responses.get(route, {"status": "ok", "data": {}})


class DummyClient:
    def __init__(self, transport):
        self.transport = transport


def test_check_craft_via_bridge():
    t = DummyTransport(responses={
        "check_craft": {"status": "ok", "data": {"can_craft": True, "missing": [], "recipe_id": "r1"}}
    })
    client = DummyClient(t)
    res = inventory.check_craft(client, "minecraft:stone_pickaxe", count=1)
    assert res["can_craft"] is True
    assert res["recipe_id"] == "r1"


def test_craft_item_delegates():
    t = DummyTransport(responses={
        "craft": {"status": "ok", "data": {"crafted": True}}
    })
    client = DummyClient(t)
    ok = inventory.craft_item(client, "r1", count=1)
    assert ok is True
    assert t.calls and t.calls[-1][0] == "craft"
