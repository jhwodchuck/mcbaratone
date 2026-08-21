"""Regression coverage for low-priority autonomous background systems."""

from types import SimpleNamespace

from baritone_client.automator import systems


class _Transport:
    def __init__(self):
        self.position = {"x": 0, "y": 64, "z": 0}

    def dispatch(self, route, _payload, timeout=None):
        assert route == "get_state"
        return {
            "block_position": dict(self.position),
            "dimension": "minecraft:overworld",
        }


def test_mapping_scans_once_per_stationary_chunk(monkeypatch, tmp_path):
    transport = _Transport()
    client = SimpleNamespace(transport=transport)
    clock = {"now": 100.0}
    scans = []
    monkeypatch.setattr(systems.time, "time", lambda: clock["now"])
    monkeypatch.setattr(
        systems,
        "runtime_artifact_path",
        lambda *_args, **_kwargs: tmp_path / "world_map.md",
    )
    monkeypatch.setattr(
        systems,
        "scan_visible_landmarks",
        lambda *_args, **_kwargs: scans.append(
            tuple(transport.position.values())
        )
        or 0,
    )

    mapper = systems.MappingSystem(
        client,
        SimpleNamespace(),
        landmark_scan_interval=60.0,
    )
    mapper.tick()
    assert scans == [], "startup work must get the bridge before passive mapping"

    clock["now"] = 161.0
    mapper.tick()
    clock["now"] = 500.0
    mapper.tick()
    assert len(scans) == 1, "a stationary chunk must not be rescanned"

    transport.position["x"] = 17
    mapper.tick()
    assert len(scans) == 2, "entering a new chunk should remain discoverable"


def test_mapping_does_not_hot_loop_failed_chunk_scan(monkeypatch, tmp_path):
    transport = _Transport()
    client = SimpleNamespace(transport=transport)
    clock = {"now": 100.0}
    attempts = []
    monkeypatch.setattr(systems.time, "time", lambda: clock["now"])
    monkeypatch.setattr(
        systems,
        "runtime_artifact_path",
        lambda *_args, **_kwargs: tmp_path / "world_map.md",
    )

    def fail_scan(*_args, **_kwargs):
        attempts.append(clock["now"])
        raise TimeoutError("bounded passive scan timed out")

    monkeypatch.setattr(systems, "scan_visible_landmarks", fail_scan)
    mapper = systems.MappingSystem(
        client,
        SimpleNamespace(),
        landmark_scan_interval=60.0,
    )

    clock["now"] = 161.0
    mapper.tick()
    clock["now"] = 500.0
    mapper.tick()

    assert attempts == [161.0]
