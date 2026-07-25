from types import SimpleNamespace

from baritone_client.automator.phases import megabase
from baritone_client.automator.state_manager import Phase, StateManager


def test_megabase_handoff_persists_plan_and_verifies_beacon(monkeypatch, tmp_path):
    world = {}
    inventory = {
        "minecraft:shulker_box": 5,
        "minecraft:iron_block": 9,
        "minecraft:beacon": 1,
    }

    class Transport:
        def dispatch(self, route, payload):
            if route == "get_state":
                return {
                    "dimension": "minecraft:overworld",
                    "block_position": {"x": 19, "y": 65, "z": 35},
                }
            if route == "get_block":
                return {
                    "id": world.get(
                        (payload["x"], payload["y"], payload["z"]),
                        "minecraft:air",
                    )
                }
            return {}

    client = SimpleNamespace(transport=Transport())
    state = StateManager(tmp_path)
    handler = megabase.MegabaseInitHandler()
    monkeypatch.setattr(
        megabase,
        "count_item",
        lambda _client, item_id: inventory.get(item_id, 0),
    )
    monkeypatch.setattr(megabase, "goto", lambda *_a, **_k: True)

    def place(_client, x, y, z, item_id, **_kwargs):
        world[(x, y, z)] = item_id
        inventory[item_id] -= 1
        return True

    monkeypatch.setattr(megabase.harness_ops, "place_block_exact", place)

    assert handler._pack_shulkers(client)
    assert handler._select_location(client, state)
    assert state.custom_data["megabase_location"] == [24, 65, 40]
    assert handler._begin_excavation(client, state)
    plan = state.custom_data["terraform_plan"]
    assert plan["center"] == [24, 40]
    assert plan["next_ring"] == 0
    assert state.get_phase_payload(Phase.MEGABASE_INIT)["terraform_plan"] == plan
    assert handler._place_beacon(client, state)
    assert state.custom_data["structures"]["beacon"]["verified"] is True
    assert world[(24, 66, 40)] == "minecraft:beacon"


def test_megabase_beacon_fails_without_real_item(monkeypatch, tmp_path):
    client = SimpleNamespace(
        transport=SimpleNamespace(
            dispatch=lambda route, payload: {"id": "minecraft:air"}
        )
    )
    state = StateManager(tmp_path)
    state.custom_data["megabase_location"] = [8, 64, 8]
    monkeypatch.setattr(megabase, "count_item", lambda *_a: 0)
    assert not megabase.MegabaseInitHandler()._place_beacon(client, state)

