from types import SimpleNamespace

import pytest

from baritone_client.common import farming


@pytest.fixture
def bucket_world(monkeypatch):
    blocks = {(3, 63, 9): {"id": "minecraft:dirt"},
              (3, 64, 9): {"id": "minecraft:grass_block"}}
    inventory = {"minecraft:bucket": 1, "minecraft:water_bucket": 1}
    calls = []
    behavior = {"capability": True, "reject": False, "effect": True}

    class Transport:
        def dispatch(self, route, payload):
            calls.append((route, payload))
            target = tuple(payload.get(axis) for axis in ("x", "y", "z"))
            if route == "get_version":
                if behavior.get("query_error"):
                    raise RuntimeError("version unavailable")
                if "version_reply" in behavior:
                    return behavior["version_reply"]
                return {"capabilities": {"water_bucket_postconditions": behavior["capability"]}}
            if route == "get_block":
                return blocks.get(target, {"id": "minecraft:air"})
            if route == "dig_block":
                blocks[target] = {"id": "minecraft:air"}
            if route == "use_bucket":
                if behavior["reject"]:
                    raise RuntimeError("no visible support")
                if behavior["effect"]:
                    pickup = payload["operation"] == "pickup"
                    blocks[target] = {"id": "minecraft:air"} if pickup else {
                        "id": "minecraft:water", "state": {"level": "0"}}
                    inventory["minecraft:water_bucket"] += 1 if pickup else -1
                return {"accepted": True}
            if route == "use_item":
                # A legacy crosshair use can land above crop level.
                blocks[(3, 65, 9)] = {"id": "minecraft:water", "state": {"level": "0"}}
            return {}

    client = SimpleNamespace(transport=Transport())
    monkeypatch.setattr(farming, "goto", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(farming, "select_item", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(farming, "count_item", lambda _client, item: inventory.get(item, 0))
    monkeypatch.setattr(farming.time, "sleep", lambda _seconds: None)
    return client, blocks, inventory, calls, behavior


def test_advertised_bucket_command_places_only_at_farmland_height(bucket_world):
    client, blocks, _inventory, calls, _behavior = bucket_world
    assert farming.ensure_farm_water(client, 3, 64, 9)
    assert ("use_bucket", {"x": 3, "y": 64, "z": 9, "operation": "place"}) in calls
    assert blocks[(3, 64, 9)]["id"] == "minecraft:water"
    assert (3, 65, 9) not in blocks
    assert not any(route in ("look_at", "use_item") for route, _payload in calls)


@pytest.mark.parametrize("failure", ["reject", "no_effect", "query_error"])
def test_verified_bucket_failure_never_falls_back_to_crosshair_use(bucket_world, failure):
    client, _blocks, _inventory, calls, behavior = bucket_world
    behavior.update({"reject": failure == "reject", "effect": failure != "no_effect",
                     "query_error": failure == "query_error"})
    assert not farming.ensure_farm_water(client, 3, 64, 9)
    assert not any(route in ("look_at", "use_item") for route, _payload in calls)


def test_elevated_source_is_reclaimed_by_exact_target_before_replacement(bucket_world):
    client, blocks, inventory, calls, _behavior = bucket_world
    blocks[(3, 65, 9)] = {"id": "minecraft:water", "state": {"level": "0"}}
    inventory["minecraft:water_bucket"] = 0
    assert farming.ensure_farm_water(client, 3, 64, 9)
    uses = [payload for route, payload in calls if route == "use_bucket"]
    assert uses == [{"x": 3, "y": 65, "z": 9, "operation": "pickup"},
                    {"x": 3, "y": 64, "z": 9, "operation": "place"}]
    assert blocks[(3, 65, 9)]["id"] == "minecraft:air"
    assert not any(route == "use_item" for route, _payload in calls)


@pytest.mark.parametrize("reply", [{"error": "unavailable"}, {"data": []},
                                  {"capabilities": {"water_bucket_postconditions": "true"}}])
def test_invalid_capability_reply_does_not_authorize_crosshair_fallback(bucket_world, reply):
    client, _blocks, _inventory, calls, behavior = bucket_world
    behavior["version_reply"] = reply
    assert not farming.ensure_farm_water(client, 3, 64, 9)
    assert not any(route in ("look_at", "use_item", "use_bucket") for route, _payload in calls)
