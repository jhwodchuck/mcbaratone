import pytest

from baritone_client.common.inventory import get_inventory
from baritone_client.inventory_evidence import inventory_counts, valid_inventory


class _Transport:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = 0

    def dispatch(self, *_args, **_kwargs):
        self.calls += 1
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response


class _Client:
    def __init__(self, responses):
        self.transport = _Transport(responses)


def _empty():
    return {"inventory": [], "armor": [], "offhand": []}


def test_genuine_empty_inventory_is_valid_and_current():
    client = _Client([_empty()])
    assert valid_inventory(_empty())
    assert get_inventory(client) == {}
    assert client.transport.calls == 1


def test_empty_object_is_malformed_and_retried_then_raises():
    client = _Client([{}, {}, {}])
    with pytest.raises(RuntimeError, match="Current inventory unavailable"):
        get_inventory(client)
    assert client.transport.calls == 3


def test_timeout_does_not_turn_last_snapshot_into_current_inventory():
    client = _Client([{"inventory": [{"id": "minecraft:stone", "count": 4}]}, TimeoutError(), TimeoutError(), TimeoutError()])
    assert get_inventory(client) == {"minecraft:stone": 4}
    with pytest.raises(RuntimeError, match="Current inventory unavailable"):
        get_inventory(client)


def test_malformed_section_is_rejected_even_when_inventory_section_is_empty():
    assert not valid_inventory({"inventory": [], "armor": "unknown", "offhand": []})


def test_explicitly_invalid_snapshot_is_not_current_evidence():
    assert not valid_inventory(
        {"snapshot_valid": False, "inventory": [], "armor": [], "offhand": []}
    )
    assert not valid_inventory(
        {
            "data": {
                "snapshot_valid": False,
                "inventory": [],
                "armor": [],
                "offhand": [],
            }
        }
    )


def test_generic_count_map_schema_is_aggregated():
    client = _Client([
        {
            "items": {
                "minecraft:stone": {"count": 4},
                "minecraft:dirt": 2,
            }
        }
    ])
    assert get_inventory(client) == {
        "minecraft:stone": 4,
        "minecraft:dirt": 2,
    }


def test_canonical_inventory_does_not_double_count_generic_aliases():
    payload = {
        "inventory": [{"id": "minecraft:stone", "count": 2}],
        "items": [{"id": "minecraft:stone", "count": 4}],
        "slots": [{"id": "minecraft:stone", "count": 8}],
        "armor": [],
        "offhand": [],
    }
    assert inventory_counts(payload) == {"minecraft:stone": 2}


def test_canonical_armor_and_offhand_sections_are_counted():
    payload = {
        "inventory": [],
        "armor": [{"id": "minecraft:iron_helmet", "count": 1}],
        "offhand": [{"id": "minecraft:shield", "count": 1}],
    }
    assert inventory_counts(payload) == {
        "minecraft:iron_helmet": 1,
        "minecraft:shield": 1,
    }
