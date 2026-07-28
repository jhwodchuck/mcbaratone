from types import SimpleNamespace

from baritone_client.automator.phase_executor import PhaseExecutor, PhaseHandler
from baritone_client.automator.phase_verifier import PhaseVerifier
from baritone_client.automator.automator import EndGameAutomator
from baritone_client.automator.objective import (
    ObjectivePlanner,
    ObjStatus,
    default_objectives,
)
from baritone_client.automator.resource_manager import ResourceManager
from baritone_client.automator.state_manager import Phase, StateManager
from baritone_client.automator.phases.iron_farm import IronFarmHandler
from baritone_client.automator.phases.trading import ToolPerfectionHandler
from baritone_client.automator.phases.villager import VillagerInfraHandler
from baritone_client.automator.phases.xp_engine import XpEngineHandler
from baritone_client.common.tasks import TaskResult
from baritone_client.common.storage_catalog import catalog_for
from baritone_client.world_identity import WorldIdentity


class FakeTransport:
    def __init__(self, inventory=None, blocks=None):
        self.inventory = inventory or []
        self.blocks = blocks or {}

    def dispatch(self, route, payload, **_kwargs):
        if route == "get_state":
            return {
                "health": 20,
                "food_level": 20,
                "is_dead": False,
                "dimension": "minecraft:overworld",
            }
        if route == "get_inventory":
            return {"inventory": list(self.inventory), "armor": [], "offhand": []}
        if route == "get_block":
            position = (payload["x"], payload["y"], payload["z"])
            return {"id": self.blocks.get(position, "minecraft:air")}
        return {}


class SuccessfulHandler(PhaseHandler):
    def get_name(self):
        return "Unverified success"

    def execute(self, client, resources, state):
        return TaskResult.ok("handler said success")


def make_verifier(tmp_path, inventory=None, blocks=None):
    client = SimpleNamespace(transport=FakeTransport(inventory, blocks))
    resources = ResourceManager(client)
    state = StateManager(checkpoint_dir=tmp_path)
    return client, resources, state, PhaseVerifier(client, resources, state)


def test_verifier_covers_every_survival_gate_exactly_once(tmp_path):
    _client, _resources, _state, verifier = make_verifier(tmp_path)
    gate_ids = [gate for spec in verifier.specs.values() for gate in spec.gate_ids]
    survival_ids = [gate for gate in gate_ids if gate.startswith("T")]
    assert survival_ids == [f"T{number}" for number in range(1200, 1214)]


def test_food_and_iron_requires_live_items_and_persisted_food_source(tmp_path):
    inventory = [
        {"id": "minecraft:bread", "count": 16},
        {"id": "minecraft:iron_pickaxe", "count": 1},
        {"id": "minecraft:shield", "count": 1},
        {"id": "minecraft:iron_helmet", "count": 1},
        {"id": "minecraft:iron_chestplate", "count": 1},
        {"id": "minecraft:iron_leggings", "count": 1},
    ]
    _client, _resources, state, verifier = make_verifier(tmp_path, inventory)

    missing = verifier.verify(Phase.FOOD_AND_IRON, TaskResult.ok())
    assert not missing.success
    assert "renewable food source persisted" in missing.reason

    state.custom_data["farm_location"] = [10, 64, 10]
    verified = verifier.verify(Phase.FOOD_AND_IRON, TaskResult.ok())
    assert verified.success
    assert verified.gate_ids == ("T1204",)


def test_boot_requires_real_survival_capabilities(tmp_path):
    inventory = [{"id": "minecraft:stone_pickaxe", "count": 1}]
    _client, _resources, state, verifier = make_verifier(tmp_path, inventory)
    state.record_phase_payload(
        Phase.BOOT_SEQUENCE,
        {"completed_actions": 10, "sequence_result": "boot ready"},
    )
    state.custom_data["structures"] = {
        "bootstrap_base": {"verified": True},
    }

    missing = verifier.verify(Phase.BOOT_SEQUENCE, TaskResult.ok())
    assert not missing.success
    assert "renewable starter food source persisted" in missing.reason

    state.custom_data["structures"]["food_source"] = {
        "verified": True,
        "type": "starter_crop_farm",
    }
    verified = verifier.verify(Phase.BOOT_SEQUENCE, TaskResult.ok())

    assert verified.success
    assert verified.gate_ids == ("BOOT",)


def test_initial_gathering_counts_items_deposited_in_verified_storage(tmp_path):
    inventory = [{"id": "minecraft:stone_pickaxe", "count": 1}]
    client, _resources, state, verifier = make_verifier(tmp_path, inventory)
    catalog_for(client, state).observe_inventory(
        (4, 64, 9),
        [
            {"slot": 0, "id": "minecraft:oak_log", "count": 16},
            {"slot": 1, "id": "minecraft:cobblestone", "count": 32},
        ],
        dimension="minecraft:overworld",
        capacity_slots=27,
    )

    verified = verifier.verify(Phase.INITIAL_GATHERING, TaskResult.ok())

    assert verified.success
    assert verified.gate_ids == ("T1202",)


def test_executor_rejects_handler_success_when_postconditions_are_missing(tmp_path):
    client, resources, state, verifier = make_verifier(tmp_path)
    executor = PhaseExecutor(
        client,
        resources,
        state,
        max_retries=0,
        screenshot_enabled=False,
        verifier=verifier,
    )
    executor.register_handler(Phase.FOOD_AND_IRON, SuccessfulHandler())

    assert not executor.execute_phase(Phase.FOOD_AND_IRON)
    assert state.get_progress(Phase.FOOD_AND_IRON) == 0.0
    payload = state.get_phase_payload(Phase.FOOD_AND_IRON)
    assert payload["verification_gate_ids"] == ["T1204"]


def test_spawn_bootstrap_requires_a_durable_world_seed(tmp_path):
    _client, _resources, state, verifier = make_verifier(tmp_path)
    state.update_progress(1.0, phase=Phase.BRIDGE_CHECK)

    assert not verifier.verify(Phase.SPAWN_BOOTSTRAP, TaskResult.ok()).success

    state.bind_world_identity(WorldIdentity(seed=12345, world_name="test"))
    assert verifier.verify(Phase.SPAWN_BOOTSTRAP, TaskResult.ok()).success


def test_unimplemented_handlers_fail_explicitly(tmp_path):
    client, resources, state, _verifier = make_verifier(tmp_path)
    handlers = (
        VillagerInfraHandler(),
        XpEngineHandler(),
        IronFarmHandler(),
        ToolPerfectionHandler(),
    )
    for handler in handlers:
        result = handler.execute(client, resources, state)
        assert not result.success
        assert "not implemented" in result.reason


def test_resume_audit_rewinds_pre_verifier_completion(tmp_path):
    client, resources, state, verifier = make_verifier(tmp_path)
    state.has_durable_inventory_observations = True
    state.phase_progress.update(
        {
            Phase.BRIDGE_CHECK: 1.0,
            Phase.SPAWN_BOOTSTRAP: 1.0,
            Phase.INITIAL_GATHERING: 1.0,
            Phase.BASE_CONSTRUCTION: 1.0,
            Phase.BOOT_SEQUENCE: 1.0,
            Phase.FOOD_AND_IRON: 1.0,
        }
    )
    planner = ObjectivePlanner(default_objectives())
    planner.restore(
        {
            Phase.BRIDGE_CHECK,
            Phase.SPAWN_BOOTSTRAP,
            Phase.INITIAL_GATHERING,
            Phase.BASE_CONSTRUCTION,
            Phase.BOOT_SEQUENCE,
            Phase.FOOD_AND_IRON,
        }
    )
    automator = SimpleNamespace(
        planner=planner,
        state=state,
        phase_verifier=verifier,
    )

    EndGameAutomator._revalidate_completed_objectives(automator)

    assert planner.completed_phases() == {
        Phase.BRIDGE_CHECK,
        Phase.SPAWN_BOOTSTRAP,
    }
    assert state.get_current_phase() is Phase.INITIAL_GATHERING
    assert state.get_progress(Phase.INITIAL_GATHERING) == 0.0


def test_resume_audit_preserves_legacy_checkpoint_completion(tmp_path):
    _client, _resources, state, verifier = make_verifier(tmp_path)
    completed = {
        Phase.BRIDGE_CHECK,
        Phase.SPAWN_BOOTSTRAP,
        Phase.INITIAL_GATHERING,
        Phase.BASE_CONSTRUCTION,
        Phase.BOOT_SEQUENCE,
        Phase.FOOD_AND_IRON,
    }
    planner = ObjectivePlanner(default_objectives())
    planner.restore(completed)
    automator = SimpleNamespace(
        planner=planner,
        state=state,
        phase_verifier=verifier,
    )

    EndGameAutomator._revalidate_completed_objectives(automator)

    assert planner.completed_phases() == completed


def test_automator_persists_and_restores_objective_failure_budget(tmp_path):
    _client, _resources, state, _verifier = make_verifier(tmp_path)
    planner = ObjectivePlanner(default_objectives())
    planner.restore({Phase.BRIDGE_CHECK, Phase.SPAWN_BOOTSTRAP})
    objective = planner._by_phase[Phase.INITIAL_GATHERING]
    objective.status = ObjStatus.BLOCKED
    objective.attempts = 2
    objective.interruptions = 5
    objective.no_progress_streak = 2
    objective.last_evidence = "durable-evidence"

    writer = SimpleNamespace(planner=planner, state=state)
    EndGameAutomator._persist_objective_progress(writer)

    restored = ObjectivePlanner(default_objectives())
    reader = SimpleNamespace(planner=restored, state=state)
    EndGameAutomator._restore_planner(reader)
    recovered = restored._by_phase[Phase.INITIAL_GATHERING]

    assert recovered.attempts == 2
    assert recovered.interruptions == 5
    assert recovered.no_progress_streak == 2
    assert recovered.last_evidence == "durable-evidence"


def test_resource_refresh_reports_inventory_for_durable_observation(tmp_path):
    inventory = [{"id": "minecraft:cobblestone", "count": 32}]
    _client, resources, state, _verifier = make_verifier(
        tmp_path,
        inventory,
    )
    observed = []
    resources.inventory_observer = lambda counts: observed.append(dict(counts))

    resources.refresh_inventory()

    assert observed == [{"minecraft:cobblestone": 32}]
