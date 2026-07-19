import json
import threading
import time

from baritone_client.automator.state_manager import Phase, StateManager
from baritone_client.core.client import Client
from baritone_client.core.facades.schematics import SchematicManager
from baritone_client.transport.command_dispatcher import CommandDispatcher
from baritone_client.world_identity import WorldIdentity
from baritone_client.operator_monitor import OperatorMonitor, OperatorMonitorConfig


class DummyTransport:
    def __init__(self, delay=0.0):
        self.calls = []
        self.delay = delay

    def dispatch(self, route, payload, **kwargs):
        self.calls.append((route, payload))
        if self.delay:
            time.sleep(self.delay)
        if route == "command":
            return {"status": "ok", "data": {"seq": len(self.calls)}}
        if route == "get_state":
            return {"world_seed": 42, "position": {"x": 0, "y": 64, "z": 0}}
        return {"status": "ok", "data": {}}

    def shutdown(self):
        return None


def test_world_identity_is_stable_and_dimension_independent():
    overworld = WorldIdentity.from_state({
        "world_seed": 42,
        "dimension": "minecraft:overworld",
        "world_identity": {"seed": 42, "world_name": "Survival"},
    })
    nether = WorldIdentity.from_state({
        "world_seed": 42,
        "dimension": "minecraft:the_nether",
        "world_identity": {"seed": 42, "world_name": "Survival"},
    })
    assert overworld.stable_hash == nether.stable_hash
    assert overworld.matches(nether)


def test_checkpoint_rejects_wrong_world_without_applying_state(tmp_path):
    manager = StateManager(checkpoint_dir=tmp_path)
    manager.bind_world_identity(WorldIdentity(seed=11, world_name="A"))
    manager.set_phase(Phase.FOOD_AND_IRON)
    manager.save_checkpoint({"minecraft:iron_ingot": 9})

    other = StateManager(checkpoint_dir=tmp_path)
    assert not other.load_checkpoint(
        current_world_identity=WorldIdentity(seed=12, world_name="B")
    )
    assert other.current_phase == Phase.BRIDGE_CHECK
    assert other.last_checkpoint_validation == "world_mismatch"


def test_direct_checkpoint_save_retains_bound_world_identity(tmp_path):
    manager = StateManager(checkpoint_dir=tmp_path)
    identity = WorldIdentity(seed=99, world_name="Persistent")
    manager.bind_world_identity(identity)
    manager.save_checkpoint({})
    payload = json.loads((tmp_path / manager.CHECKPOINT_FILE).read_text(encoding="utf-8"))
    assert payload["world_seed"] == 99
    assert payload["world_identity"]["stable_hash"] == identity.stable_hash


def test_dispatcher_coalesces_observations_but_not_mutations():
    transport = DummyTransport(delay=0.05)
    dispatcher = CommandDispatcher(transport, observation_cache_ttl=0.0)
    results = []
    threads = [
        threading.Thread(target=lambda: results.append(dispatcher.dispatch("get_state", {})))
        for _ in range(2)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(transport.calls) == 1
    assert any(result.get_data().get("deduplicated") for result in results)

    dispatcher.dispatch("build", {"name": "house"})
    dispatcher.dispatch("build", {"name": "house"})
    assert len(transport.calls) == 3


def test_schematic_facade_exposes_safe_legacy_plan():
    payload = json.dumps({
        "name": "tiny",
        "blocks": [{"block": "stone", "position": [0, 0, 0]}],
    })
    plan = SchematicManager(DummyTransport()).plan_legacy_json(
        payload, origin=(10, 64, 20)
    )
    assert plan.steps[0].id == "minecraft:stone"
    assert (plan.steps[0].x, plan.steps[0].y, plan.steps[0].z) == (10, 64, 20)


def test_client_follow_controller_uses_native_routes_and_tcp_polling():
    transport = DummyTransport()
    client = Client(transport)
    controller = client.create_follow_controller(
        allowed_actors={"Owner"}, home_provider=lambda: (1, 64, 2)
    )
    result = controller.handle("Owner", "follow Steve")
    assert result.dispatched
    assert transport.calls[-1] == ("follow", {"entity": "Steve"})
    assert controller.get_capabilities()["transport"]["status_mode"] == "polling"


def test_operator_monitor_synthesizes_stopped_state(tmp_path):
    class NoProcesses:
        def is_running(self, pid):
            return False

        def command_for_pid(self, pid):
            return None

        def find_matching_processes(self, command_fragments):
            return []

    monitor = OperatorMonitor(OperatorMonitorConfig(
        workspace=tmp_path,
        process_provider=NoProcesses(),
        tcp_probe=lambda host, port, timeout: {
            "reachable": False,
            "latency_ms": None,
            "error": "closed",
            "host": host,
            "port": port,
        },
    ))
    snapshot = monitor.collect()
    assert snapshot["schema_version"] == "1.0"
    assert snapshot["overall_state"] == "stopped"
    assert "evidence" in snapshot
