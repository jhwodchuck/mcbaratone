from types import SimpleNamespace

from baritone_client.automator.state_manager import StateManager
from baritone_client.common.landmark_scanner import (
    import_shared_landmarks,
    scan_visible_landmarks,
)
from baritone_client.common.storage_catalog import catalog_for
from baritone_client.world_identity import WorldIdentity


class LandmarkTransport:
    host = "localhost"

    def dispatch(self, route, payload):
        if route == "get_state":
            return {
                "dimension": "minecraft:overworld",
                "block_position": {"x": 10, "y": 64, "z": 10},
            }
        if route == "find_blocks":
            assert payload["radius"] == 32
            assert "minecraft:nether_portal" in payload["blocks"]
            assert "minecraft:bell" in payload["blocks"]
            assert "minecraft:chest" in payload["blocks"]
            return {
                "found": [
                    {"x": 12, "y": 65, "z": 10, "block": "minecraft:nether_portal"},
                    {"x": 12, "y": 66, "z": 10, "block": "minecraft:nether_portal"},
                    {"x": 18, "y": 64, "z": 9, "block": "minecraft:bell"},
                    {"x": 20, "y": 64, "z": 8, "block": "minecraft:chest"},
                ]
            }
        return {}


def _state(run_dir):
    state = StateManager(checkpoint_dir=run_dir)
    state.bind_world_identity(WorldIdentity(server_address="minecraft.test:25565"))
    return state


def test_scan_records_and_shares_deduplicated_landmarks(tmp_path):
    fleet = tmp_path / "runs" / "headlessmc"
    first_dir = fleet / "Bot07" / "controller"
    second_dir = fleet / "Bot16" / "controller"
    first_dir.mkdir(parents=True)
    second_dir.mkdir(parents=True)
    client = SimpleNamespace(transport=LandmarkTransport())
    first_state = _state(first_dir)

    # Two active portal blocks describe one usable portal landmark.
    assert scan_visible_landmarks(client, first_state) == 3
    catalog = catalog_for(client, first_state)
    assert catalog.path == fleet / "shared" / "storage_catalog.sqlite3"
    assert len(catalog.list_landmarks("nether_portal")) == 1
    assert len(catalog.list_landmarks("village")) == 1
    assert len(catalog.list_landmarks("chest")) == 1
    assert len(catalog.list_containers()) == 1

    second_state = _state(second_dir)
    assert import_shared_landmarks(client, second_state) == 3
    portal = second_state.get_locations("nether_portal")["nether_portal"][0]
    assert (portal["x"], portal["y"], portal["z"]) == (12, 65, 10)
    assert "shared" in portal["tags"]


def test_catalog_keeps_landmarks_isolated_by_world(tmp_path):
    path = tmp_path / "Bot01" / "controller"
    path.mkdir(parents=True)
    client = SimpleNamespace(transport=LandmarkTransport())
    first = _state(path)
    scan_visible_landmarks(client, first)

    other = StateManager(checkpoint_dir=path)
    other.bind_world_identity(WorldIdentity(server_address="other.test:25565"))
    assert catalog_for(client, other).list_landmarks() == []
