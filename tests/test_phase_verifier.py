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
from baritone_client.common.tasks import TaskResult
from baritone_client.common.storage_catalog import catalog_for
from baritone_client.world_identity import WorldIdentity


class FakeTransport:
    def __init__(self, inventory=None, blocks=None, *, state=None, entities=None):
        self.inventory = inventory or []
        self.blocks = blocks or {}
        self.state = state or {}
        self.entities = entities or []

    def dispatch(self, route, payload, **_kwargs):
        if route == "get_state":
            return {
                "health": 20,
                "food_level": 20,
                "is_dead": False,
                "dimension": "minecraft:overworld",
                "block_position": {"x": 10, "y": 64, "z": 10},
                **self.state,
            }
        if route == "get_entities":
            return {"entities": list(self.entities), "count": len(self.entities)}
        if route == "get_inventory":
            return {"inventory": list(self.inventory), "armor": [], "offhand": []}
        if route == "get_block":
            position = (payload["x"], payload["y"], payload["z"])
            return {"id": self.blocks.get(position, "minecraft:air")}
        if route == "get_entities":
            return {"entities": list(self.entities)}
        return {}


class SuccessfulHandler(PhaseHandler):
    def get_name(self):
        return "Unverified success"

    def execute(self, client, resources, state):
        return TaskResult.ok("handler said success")


def make_verifier(tmp_path, inventory=None, blocks=None, *, live_state=None, entities=None):
    client = SimpleNamespace(
        transport=FakeTransport(inventory, blocks, state=live_state, entities=entities)
    )
    resources = ResourceManager(client)
    state = StateManager(checkpoint_dir=tmp_path)
    return client, resources, state, PhaseVerifier(client, resources, state)


def live_boot_blocks(include_torch=True):
    blocks = {
        (10, 63, 10): "minecraft:grass_block",
        (11, 64, 11): "minecraft:crafting_table",
        (12, 64, 11): "minecraft:furnace",
        (11, 64, 12): "minecraft:chest",
        (10, 65, 10): "minecraft:wheat",
    }
    if include_torch:
        blocks[(14, 65, 10)] = "minecraft:torch"
    return blocks


def test_verifier_covers_every_survival_gate_exactly_once(tmp_path):
    _client, _resources, _state, verifier = make_verifier(tmp_path)
    gate_ids = [gate for spec in verifier.specs.values() for gate in spec.gate_ids]
    survival_ids = [gate for gate in gate_ids if gate.startswith("T")]
    assert survival_ids == [f"T{number}" for number in range(1200, 1216)]


def test_postgame_verifiers_require_completed_persisted_operations(tmp_path):
    _client, _resources, state, verifier = make_verifier(tmp_path)
    terraform_data = {
        "verified_operations": True,
        "progress_complete": True,
        "chunks_completed": 9,
        "chunks_total": 9,
        "chunks_failed": 0,
        "chunks_unverified": 0,
        "progress_entries_total": 9,
        "terraform_plan": {"center": [0, 0], "target_y": 64},
    }
    # terraform_progress accumulates ledger-wide across every retry this
    # phase has ever made -- deliberately left with a stale entry here to
    # prove the gate reads the scoped payload counts, not this mirror.
    state.custom_data["terraform_progress"] = {
        "next_index": 9, "total": 9,
        "counts": {"done": 8, "failed": 1, "unverified": 0, "skipped": 0},
    }
    assert verifier.verify(
        Phase.TERRAFORM, TaskResult.ok("done", **terraform_data)
    ).success

    # The cursor can reach the end of a sweep (record-and-continue) while
    # chunks are still failed/unverified -- that must not pass the gate even
    # though next_index == total, which is the exact case that used to slip
    # through when this check only compared the cursor to the total.
    still_failing = dict(terraform_data, chunks_failed=1)
    assert not verifier.verify(
        Phase.TERRAFORM, TaskResult.ok("swept but incomplete", **still_failing)
    ).success

    incomplete = dict(terraform_data, chunks_completed=8)
    assert not verifier.verify(
        Phase.TERRAFORM, TaskResult.ok("partial", **incomplete)
    ).success

    city_data = {
        "verified_operations": True,
        "progress_complete": True,
        "rings_completed": 3,
        "districts_completed": 25,
    }
    state.custom_data["city_progress"] = {
        "ring": 3,
        "districts_completed": 25,
    }
    assert verifier.verify(
        Phase.CITY_BUILD, TaskResult.ok("done", **city_data)
    ).success

    state.custom_data["city_progress"]["districts_completed"] = 24
    assert not verifier.verify(
        Phase.CITY_BUILD, TaskResult.ok("stale", **city_data)
    ).success


def test_food_and_iron_requires_live_items_and_persisted_food_source(tmp_path):
    inventory = [
        {"id": "minecraft:bread", "count": 16},
        {"id": "minecraft:iron_pickaxe", "count": 1},
        {"id": "minecraft:shield", "count": 1},
        {"id": "minecraft:iron_helmet", "count": 1},
        {"id": "minecraft:iron_chestplate", "count": 1},
        {"id": "minecraft:iron_leggings", "count": 1},
    ]
    _client, _resources, state, verifier = make_verifier(
        tmp_path,
        inventory,
        {(10, 64, 10): "minecraft:wheat"},
    )

    missing = verifier.verify(Phase.FOOD_AND_IRON, TaskResult.ok())
    assert not missing.success
    assert "renewable food source persisted" in missing.reason

    state.custom_data["farm_location"] = [10, 64, 10]
    verified = verifier.verify(Phase.FOOD_AND_IRON, TaskResult.ok())
    assert verified.success
    assert verified.gate_ids == ("T1204",)


def test_boot_requires_real_survival_capabilities(tmp_path):
    inventory = [{"id": "minecraft:stone_pickaxe", "count": 1}]
    _client, _resources, state, verifier = make_verifier(
        tmp_path,
        inventory,
        live_boot_blocks(),
    )
    state.record_phase_payload(
        Phase.BOOT_SEQUENCE,
        {"completed_actions": 10, "sequence_result": "boot ready"},
    )
    state.custom_data["structures"] = {
        "bootstrap_base": {"verified": True},
    }

    missing = verifier.verify(Phase.BOOT_SEQUENCE, TaskResult.ok())
    assert not missing.success
    assert "homestead dry-anchor established" in missing.reason

    state.custom_data["homestead"] = {
        "anchor": [10, 64, 10],
        "steps": {
            "infrastructure": {"verified": True},
            "micro_farm": {"verified": True},
            "light_perimeter": {
                "verified": True,
                "intended": [[14, 65, 10]],
            },
        },
    }
    state.custom_data["structures"] = {
        "bootstrap_base": {
            "origin": [10, 64, 10],
            "crafting_table": [11, 64, 11],
            "furnace": [12, 64, 11],
            "supply_chest": [11, 64, 12],
        },
        "food_source": {
            "verified": True,
            "type": "starter_crop_farm",
            "plots": [[10, 65, 10, "minecraft:wheat"]],
        },
    }
    verified = verifier.verify(Phase.BOOT_SEQUENCE, TaskResult.ok())

    assert verified.success
    assert verified.gate_ids == ("BOOT",)


def test_boot_sequence_requires_perimeter_lighting(tmp_path):
    client, _resources, state, verifier = make_verifier(
        tmp_path,
        blocks=live_boot_blocks(include_torch=False),
    )
    state.custom_data["homestead"] = {
        "anchor": [10, 64, 10],
        "steps": {
            "infrastructure": {"verified": True},
            "micro_farm": {"verified": True},
            "light_perimeter": {
                "verified": False,
                "intended": [[14, 65, 10]],
            },
        },
    }
    state.record_phase_payload(
        Phase.BOOT_SEQUENCE,
        {
            "homestead": {
                "anchor": [10, 64, 10],
                "steps": {
                    "infrastructure": {"verified": True},
                    "micro_farm": {"verified": True},
                    "light_perimeter": {
                        "verified": False,
                        "intended": [[14, 65, 10]],
                    },
                },
            },
        },
    )
    state.custom_data["structures"] = {
        "bootstrap_base": {
            "origin": [10, 64, 10],
            "crafting_table": [11, 64, 11],
            "furnace": [12, 64, 11],
            "supply_chest": [11, 64, 12],
        },
        "food_source": {
            "verified": True,
            "plots": [[10, 65, 10, "minecraft:wheat"]],
        },
    }
    state.custom_data["farm_location"] = [10, 64, 10]

    missing = verifier.verify(Phase.BOOT_SEQUENCE, TaskResult.ok())
    assert not missing.success
    assert "light perimeter verified" in missing.reason

    state.custom_data["homestead"]["steps"]["light_perimeter"] = {
        "verified": True,
        "intended": [[14, 65, 10]],
    }

    still_missing = verifier.verify(Phase.BOOT_SEQUENCE, TaskResult.ok())
    assert not still_missing.success

    client.transport.blocks[(14, 65, 10)] = "minecraft:torch"
    verified = verifier.verify(Phase.BOOT_SEQUENCE, TaskResult.ok())
    assert verified.success


def test_boot_sequence_reconciles_legacy_infrastructure_and_farm(tmp_path):
    _client, _resources, state, verifier = make_verifier(
        tmp_path,
        blocks=live_boot_blocks(),
    )
    state.custom_data["homestead"] = {"anchor": [10, 64, 10], "steps": {}}
    state.custom_data["structures"] = {
        "bootstrap_base": {
            "origin": [10, 64, 10],
            "crafting_table": [11, 64, 11],
            "furnace": [12, 64, 11],
            "supply_chest": [11, 64, 12],
            "verified": True,
        },
    }
    state.custom_data["farm_location"] = [10, 64, 10]
    state.custom_data["homestead"]["steps"]["light_perimeter"] = {
        "verified": True,
        "intended": [[14, 65, 10]],
    }

    verified = verifier.verify(Phase.BOOT_SEQUENCE, TaskResult.ok())
    assert verified.success
    assert verified.gate_ids == ("BOOT",)


def test_boot_sequence_prefers_valid_bootstrap_over_partial_house(tmp_path):
    _client, _resources, state, verifier = make_verifier(
        tmp_path,
        blocks=live_boot_blocks(),
    )
    state.custom_data["homestead"] = {
        "anchor": [10, 64, 10],
        "steps": {
            "light_perimeter": {
                "verified": True,
                "intended": [[14, 65, 10]],
            },
        },
    }
    state.custom_data["structures"] = {
        "bootstrap_base": {
            "origin": [10, 64, 10],
            "crafting_table": [11, 64, 11],
            "furnace": [12, 64, 11],
            "supply_chest": [11, 64, 12],
            "verified": True,
        },
        "starter_house": {
            "origin": [30, 64, 30],
            "door": [30, 65, 33],
            "crafting_table": [31, 65, 31],
            "supply_chest": [31, 65, 32],
        },
        "food_source": {
            "plots": [[10, 65, 10, "minecraft:wheat"]],
        },
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


def test_villager_and_xp_specs_require_live_world_evidence(tmp_path):
    beds = {(10 + index, 64, 12): "minecraft:white_bed" for index in range(6)}
    beds[(20, 40, 20)] = "minecraft:spawner"
    child = {
        "uuid": "new-baby",
        "type": "minecraft:villager",
        "is_baby": True,
    }
    _client, _resources, _state, verifier = make_verifier(
        tmp_path,
        blocks=beds,
        live_state={"experience_level": 30},
        entities=[child],
    )

    villager_result = TaskResult.ok(
        breeder={
            "location": [11, 64, 10],
            "required_beds": 3,
            "bed_blocks": [[10 + index, 64, 12] for index in range(6)],
            "verified": True,
        },
        breeding={"offspring_observed": True, "offspring_uuid": "new-baby"},
    )
    xp_result = TaskResult.ok(
        farm={"location": [20, 40, 20], "verified": True},
        grind={
            "achieved_level": 30,
            "target_level": 30,
            "encounters": 1,
            "xp_gained": 5,
        },
    )

    assert verifier.verify(Phase.VILLAGER_INFRA, villager_result).success
    assert verifier.verify(Phase.XP_ENGINE, xp_result).success

    verifier.client.transport.entities = []
    verifier.client.transport.state["experience_level"] = 29
    assert not verifier.verify(Phase.VILLAGER_INFRA, villager_result).success
    assert not verifier.verify(Phase.XP_ENGINE, xp_result).success


def test_supported_industrial_handlers_report_exact_bridge_blockers(tmp_path):
    client, resources, state, _verifier = make_verifier(tmp_path)

    iron = IronFarmHandler().execute(client, resources, state)
    trading = ToolPerfectionHandler().execute(client, resources, state)

    assert not iron.success
    assert "adult villager transport source(s) unavailable" in iron.reason
    assert "zombie transport source unavailable" in iron.reason
    assert not trading.success
    assert "diamond or netherite pickaxe is required" in trading.reason.lower()


def test_industrial_phase_verifiers_require_live_real_schema_evidence(tmp_path):
    witnesses = {
        "beds": [[8, 64, 8], [9, 64, 8], [10, 64, 8]],
        "hopper": [10, 63, 10],
        "chest": [10, 62, 10],
        "water_source": [9, 66, 8],
        "lava_source": [10, 65, 11],
        "spawn_platform": [
            [x, 66, z]
            for x in range(9, 12)
            for z in range(9, 12)
        ],
    }
    blocks = {tuple(position): "minecraft:white_bed" for position in witnesses["beds"]}
    blocks[tuple(witnesses["hopper"])] = "minecraft:hopper"
    blocks[tuple(witnesses["chest"])] = "minecraft:chest"
    blocks[tuple(witnesses["water_source"])] = "minecraft:water"
    blocks[tuple(witnesses["lava_source"])] = "minecraft:lava"
    blocks.update({tuple(position): "minecraft:stone" for position in witnesses["spawn_platform"]})
    entities = [
        {
            "id": index,
            "type": "minecraft:villager",
            "is_baby": False,
            "position": {"x": 9 + index, "y": 64, "z": 10},
        }
        for index in range(3)
    ] + [
        {
            "id": 10,
            "type": "minecraft:zombie",
            "position": {"x": 10, "y": 64, "z": 11},
        },
        {
            "id": 11,
            "type": "minecraft:iron_golem",
            "position": {"x": 12, "y": 64, "z": 10},
        },
        {
            "id": 12,
            "type": "minecraft:villager",
            "profession": "minecraft:librarian",
            "is_baby": False,
            "position": {"x": 11, "y": 64, "z": 10},
        },
    ]
    _client, _resources, _state, verifier = make_verifier(
        tmp_path,
        blocks=blocks,
        entities=entities,
    )

    iron_payload = {
        "verification_version": 1,
        "farm_location": [10, 64, 10],
        "structure_witnesses": witnesses,
        "implementation_blocker": None,
    }
    iron = verifier.verify(Phase.IRON_FARM, TaskResult.ok(**iron_payload))
    assert iron.success
    assert iron.gate_ids == ("IRON_FARM",)

    trading_payload = {
        "verification_version": 2,
        "verified_enchantments": ["mending", "efficiency", "unbreaking", "fortune"],
        "tool_perfected": True,
        "implementation_blocker": None,
    }
    trading = verifier.verify(
        Phase.TOOL_PERFECTION,
        TaskResult.ok(**trading_payload),
    )
    assert trading.success
    assert trading.gate_ids == ("LIBRARIAN_BOOKS",)


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


def test_resume_audit_preserves_attested_completion_when_base_is_unloaded(tmp_path):
    _client, _resources, state, _verifier = make_verifier(tmp_path)
    state.has_durable_inventory_observations = True
    completed = {
        Phase.BRIDGE_CHECK,
        Phase.SPAWN_BOOTSTRAP,
        Phase.INITIAL_GATHERING,
        Phase.BOOT_SEQUENCE,
        Phase.BASE_CONSTRUCTION,
    }
    state.phase_progress.update({phase: 1.0 for phase in completed})
    state.custom_data["verified_objective_completions"] = {
        phase.name: {"version": 1} for phase in completed
    }
    planner = ObjectivePlanner(default_objectives())
    planner.restore(completed)

    class NoLiveRevalidation:
        def verify(self, *_args, **_kwargs):
            raise AssertionError("attested completion queried an unloaded base")

    automator = SimpleNamespace(
        planner=planner,
        state=state,
        phase_verifier=NoLiveRevalidation(),
    )

    EndGameAutomator._revalidate_completed_objectives(automator)

    assert planner.completed_phases() == completed
    assert state.get_progress(Phase.BOOT_SEQUENCE) == 1.0
    assert state.get_progress(Phase.BASE_CONSTRUCTION) == 1.0


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


def test_objective_activation_checkpoints_consumed_attempt_before_work(tmp_path):
    _client, _resources, state, _verifier = make_verifier(tmp_path)
    automator = object.__new__(EndGameAutomator)
    automator.state = state
    automator.planner = ObjectivePlanner(default_objectives())
    saves = []
    automator._save_checkpoint = lambda: saves.append(
        state.custom_data["objective_runtime"]["BRIDGE_CHECK"]["attempts"]
    )
    objective = automator.planner._by_phase[Phase.BRIDGE_CHECK]

    automator._activate_objective(objective)

    assert state.get_current_phase() is Phase.BRIDGE_CHECK
    assert saves == [1]
    assert (
        state.custom_data["objective_runtime"]["BRIDGE_CHECK"]["status"]
        == "ACTIVE"
    )


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
