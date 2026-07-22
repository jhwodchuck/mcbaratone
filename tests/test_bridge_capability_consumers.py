from types import SimpleNamespace

import pytest

from baritone_client.actions.travel import TravelAction
from baritone_client.common.state import WorldState
from baritone_client.core.facades.processes import FarmProcess


class RecordingTransport:
    def __init__(self, responses=None):
        self.responses = responses or {}
        self.calls = []

    def dispatch(self, route, payload):
        self.calls.append((route, payload))
        return self.responses.get(route, {})


def test_world_state_exposes_bridge_biome(tmp_path):
    transport = RecordingTransport(
        {
            "get_state": {
                "position": {"x": 1, "y": 70, "z": 2, "yaw": 0, "pitch": 0},
                "health": 20,
                "food_level": 18,
                "dimension": "minecraft:overworld",
                "biome": "minecraft:plains",
            }
        }
    )
    state = WorldState(SimpleNamespace(transport=transport), str(tmp_path)).refresh()
    assert state.biome == "minecraft:plains"


def test_navigate_to_biome_uses_nearest_loaded_scan_result():
    transport = RecordingTransport(
        {
            "scan_biomes": {
                "radius": 256,
                "biomes": [
                    {
                        "id": "minecraft:forest",
                        "distance": 90,
                        "position": {"x": 90, "y": 72, "z": 0},
                    },
                    {
                        "id": "minecraft:plains",
                        "distance": 40,
                        "position": {"x": 30, "y": 68, "z": 20},
                    },
                ],
            },
            "goto": {"started": True},
        }
    )
    context = SimpleNamespace(client=SimpleNamespace(transport=transport))

    result = TravelAction().navigate_to_biome(context, "plains", max_distance=256)

    assert result.success is True
    assert transport.calls == [
        ("scan_biomes", {"radius": 256, "step": 16}),
        ("goto", {"x": 30, "y": 68, "z": 20}),
    ]


def test_navigate_to_biome_fails_instead_of_blind_exploration():
    transport = RecordingTransport(
        {"scan_biomes": {"radius": 128, "biomes": []}}
    )
    context = SimpleNamespace(client=SimpleNamespace(transport=transport))

    result = TravelAction().navigate_to_biome(context, "plains", max_distance=128)

    assert result.success is False
    assert all(route != "explore" for route, _ in transport.calls)


def test_farm_facade_matches_bridge_range_center_and_replant_contract():
    transport = RecordingTransport(
        {"process/farm/start": {"started": True}}
    )

    result = FarmProcess(transport).start(
        range=24, center=(10, 64, -3), replant=False
    )

    assert result == {"started": True}
    assert transport.calls == [
        (
            "process/farm/start",
            {"range": 24, "x": 10, "y": 64, "z": -3, "replant": False},
        )
    ]


def test_farm_facade_rejects_nonexistent_crop_filter():
    with pytest.raises(ValueError, match="does not support crop filtering"):
        FarmProcess(RecordingTransport()).start(crop="wheat")
