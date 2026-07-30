from types import SimpleNamespace

import pytest

from baritone_client.automator.objective import ObjStatus, Objective, ObjectivePlanner
from baritone_client.automator.phase_executor import PhaseExecutor, PhaseHandler
from baritone_client.automator.phases.boot_sequence import BootSequenceHandler
from baritone_client.automator.state_manager import Phase
from baritone_client.actions.homestead import IncrementalHomestead
from baritone_client.common.tasks import IncrementalProgressRequired, SurvivalRecoveryRequired


def _state_with_payloads(custom_data):
    if "homestead" in custom_data:
        structures = custom_data.setdefault("structures", {})
        structures.setdefault(
            "bootstrap_base",
            {
                "origin": [0, 64, 0],
                "crafting_table": [1, 64, 1],
                "furnace": [2, 64, 1],
                "supply_chest": [1, 64, 2],
            },
        )
        structures.setdefault(
            "food_source",
            {
                "verified": True,
                "plots": [[0, 65, 0, "minecraft:wheat"]],
            },
        )
    records = []

    def record_phase_payload(phase, payload):
        records.append((phase, payload))

    return SimpleNamespace(
        custom_data=custom_data,
        record_phase_payload=record_phase_payload,
        phase_payloads=records,
    )


def _transport_for_blocks(blocks=None, state_payload=None):
    live_blocks = {
        (0, 63, 0): "minecraft:grass_block",
        (1, 64, 1): "minecraft:crafting_table",
        (2, 64, 1): "minecraft:furnace",
        (1, 64, 2): "minecraft:chest",
        (0, 65, 0): "minecraft:wheat",
    }
    if blocks is None:
        blocks = live_blocks
    else:
        for position, block_id in live_blocks.items():
            blocks.setdefault(position, block_id)
    default_state = {"dimension": "minecraft:overworld", "world_time": 1000, "health": 20, "food_level": 20, "block_position": {"x": 0, "y": 64, "z": 0}}

    def dispatch(route, payload):
        if route == "get_state":
            return dict(default_state, **(state_payload or {}))
        if route == "get_block":
            return {"id": blocks.get((payload["x"], payload["y"], payload["z"]), "minecraft:air")}
        return {}

    return SimpleNamespace(dispatch=dispatch), blocks


def test_boot_sequence_executes_only_next_unverified_step(monkeypatch):
    state = _state_with_payloads({})
    handler = BootSequenceHandler()
    transport, _ = _transport_for_blocks(state_payload={"dimension": "minecraft:overworld", "world_time": 1000, "food_level": 20, "health": 20})
    executed = []

    monkeypatch.setattr(
        handler,
        "_run_dry_anchor_step",
        lambda *args, **kwargs: executed.append("dry_anchor") or True,
    )
    for step in (
        "_run_wood_reserve_step",
        "_run_stone_reserve_step",
        "_run_infrastructure_step",
        "_run_micro_farm_step",
        "_run_charcoal_supply_step",
        "_run_torch_supply_step",
        "_run_light_perimeter_step",
    ):
        monkeypatch.setattr(
            handler,
            step,
            lambda *args, **kwargs: (_ for _ in ()).throw(
                AssertionError(f"{step} must not execute in first invocation")
            ),
        )
    monkeypatch.setattr(handler, "_enforce_anchor_and_pacing", lambda *_args, **_kwargs: None)

    with pytest.raises(IncrementalProgressRequired):
        handler.execute(transport, SimpleNamespace(), state)

    assert executed == ["dry_anchor"]


def test_boot_sequence_requeue_does_not_consume_attempts_or_interruptions():
    objective = Objective(Phase.BOOT_SEQUENCE, max_interruptions=2)
    planner = ObjectivePlanner([objective])
    entry = planner._by_phase[Phase.BOOT_SEQUENCE]
    entry.status = ObjStatus.ACTIVE
    entry.attempts = 2
    entry.interruptions = 1
    entry.no_progress_streak = 1

    planner.mark_active(entry)
    assert planner.mark_incremental_yield(entry, "incremental_progress")
    assert entry.status is ObjStatus.BLOCKED
    assert entry.attempts == 2
    assert entry.interruptions == 1
    assert entry.no_progress_streak == 1


def test_phase_executor_yields_incremental_progress_without_retry():
    class IncrementalHandler(PhaseHandler):
        def __init__(self):
            self.calls = 0
            self.exited = False

        def get_name(self):
            return "Incremental homestead"

        def execute(self, client, resources, state):
            self.calls += 1
            raise IncrementalProgressRequired("one durable improvement")

        def on_exit(self, client, resources, state):
            self.exited = True

    state = SimpleNamespace(
        update_progress=lambda *_args, **_kwargs: None,
        record_phase_payload=lambda *_args, **_kwargs: None,
    )
    resources = SimpleNamespace(refresh_inventory=lambda: None)
    handler = IncrementalHandler()
    executor = PhaseExecutor(
        SimpleNamespace(),
        resources,
        state,
        max_retries=3,
        retry_delay=0,
        screenshot_enabled=False,
    )
    executor.register_handler(Phase.BOOT_SEQUENCE, handler)

    assert not executor.execute_phase(Phase.BOOT_SEQUENCE)
    assert executor.interruption_reason == "incremental_progress"
    assert handler.calls == 1
    assert handler.exited


def test_boot_sequence_skips_live_verified_steps(monkeypatch):
    state = _state_with_payloads({
        "homestead": {
            "anchor": [0, 64, 0],
            "steps": {
                "dry_anchor": {"verified": True},
                "wood_reserve": {"verified": True},
                "stone_reserve": {"verified": True},
                "infrastructure": {"verified": True},
                "micro_farm": {"verified": True},
                "charcoal_supply": {"verified": True},
                "torch_supply": {"verified": False},
                "light_perimeter": {"verified": False},
            },
        },
    })
    transport, _ = _transport_for_blocks(
        state_payload={
            "dimension": "minecraft:overworld",
            "world_time": 1000,
            "food_level": 20,
            "health": 20,
            "block_position": {"x": 0, "y": 64, "z": 0},
        }
    )
    handler = BootSequenceHandler()
    calls = []
    monkeypatch.setattr(
        handler,
        "_run_torch_supply_step",
        lambda *_args, **_kwargs: calls.append("torch") or True,
    )
    monkeypatch.setattr(
        handler,
        "_run_light_perimeter_step",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("light perimeter must not run until torch supply verified")
        ),
    )
    with pytest.raises(IncrementalProgressRequired):
        handler.execute(transport, SimpleNamespace(), state)
    assert calls == ["torch"]


def _transport_with_blocks(state_payload, blocks):
    transport, _ = _transport_for_blocks(
        blocks=blocks,
        state_payload=state_payload,
    )
    return transport


def test_boot_light_perimeter_repairs_one_missing_torch_per_invocation(monkeypatch):
    homestead_payload = {
        "anchor": [0, 64, 0],
        "steps": {
            "dry_anchor": {"verified": True},
            "wood_reserve": {"verified": True},
            "stone_reserve": {"verified": True},
            "infrastructure": {"verified": True},
            "micro_farm": {"verified": True},
            "charcoal_supply": {"verified": True},
            "torch_supply": {"verified": True},
            "light_perimeter": {
                "verified": False,
                "intended": [[1, 65, 0], [2, 65, 0], [3, 65, 0]],
                "verified_positions": [[1, 65, 0]],
            },
        },
    }
    blocks = {
        (1, 65, 0): "minecraft:torch",
        (2, 65, 0): "minecraft:air",
        (3, 65, 0): "minecraft:air",
    }
    state = _state_with_payloads({"homestead": homestead_payload})
    state.custom_data["homestead"]["steps"]["light_perimeter"]["intended"] = [
        [1, 65, 0],
        [2, 65, 0],
        [3, 65, 0],
    ]

    transport = _transport_with_blocks(
        {
            "dimension": "minecraft:overworld",
            "world_time": 1000,
            "food_level": 20,
            "health": 20,
            "block_position": {"x": 0, "y": 64, "z": 0},
        },
        blocks,
    )
    handler = BootSequenceHandler()

    placements = []

    def place(client, x, y, z):
        placements.append((x, y, z))
        if len(placements) <= 2:
            blocks[(x, y, z)] = "minecraft:torch"
        return True

    monkeypatch.setattr("baritone_client.actions.homestead.place_torch", place)
    handler.state = state

    with pytest.raises(IncrementalProgressRequired):
        handler.execute(transport, SimpleNamespace(), state)
    payload = state.custom_data["homestead"]["steps"]["light_perimeter"]
    assert payload["verified"] is False
    assert len(placements) == 1
    assert len(payload.get("verified_positions", [])) == 2

    with pytest.raises(IncrementalProgressRequired):
        handler.execute(transport, SimpleNamespace(), state)
    payload = state.custom_data["homestead"]["steps"]["light_perimeter"]
    assert payload["verified"] is True
    assert len(placements) == 2
    assert len(payload.get("verified_positions", [])) == 3


def test_boot_light_perimeter_failed_placement_not_persisted_as_verified(monkeypatch):
    homestead_payload = {
        "anchor": [0, 64, 0],
        "steps": {
            "dry_anchor": {"verified": True},
            "wood_reserve": {"verified": True},
            "stone_reserve": {"verified": True},
            "infrastructure": {"verified": True},
            "micro_farm": {"verified": True},
            "charcoal_supply": {"verified": True},
            "torch_supply": {"verified": True},
            "light_perimeter": {
                "verified": False,
                "intended": [[1, 65, 0], [2, 65, 0]],
                "verified_positions": [[1, 65, 0]],
            },
        },
    }
    blocks = {(1, 65, 0): "minecraft:torch"}
    state = _state_with_payloads({"homestead": homestead_payload})
    transport = _transport_with_blocks(
        {
            "dimension": "minecraft:overworld",
            "world_time": 1000,
            "food_level": 20,
            "health": 20,
            "block_position": {"x": 0, "y": 64, "z": 0},
        },
        blocks,
    )
    handler = BootSequenceHandler()

    monkeypatch.setattr(
        "baritone_client.actions.homestead.place_torch",
        lambda *_args, **_kwargs: True,
    )

    result = handler.execute(transport, SimpleNamespace(), state)
    payload = state.custom_data["homestead"]["steps"]["light_perimeter"]

    assert not payload.get("verified")
    assert result.success is False
    assert payload["verified_positions"] == [[1, 65, 0]]


@pytest.mark.parametrize(
    "food_level,health,day_time,edible_counts",
    [
        (20, 20, 14000, {"minecraft:bread": 2}),  # night
        (20, 17, 1000, {"minecraft:bread": 2}),  # low health
        (15, 20, 1000, {"minecraft:bread": 2}),  # low food
        (19, 20, 1000, {}),  # not full and no safe edible
    ],
)
def test_boot_construction_pacing_blocks_unsafe_or_underequipped(monkeypatch, food_level, health, day_time, edible_counts):
    state = _state_with_payloads({
        "homestead": {
            "anchor": [0, 64, 0],
            "steps": {
                "dry_anchor": {"verified": True},
                "wood_reserve": {"verified": True},
                "stone_reserve": {"verified": True},
                "infrastructure": {"verified": True},
                "micro_farm": {"verified": False},
            },
        },
    })
    transport = _transport_with_blocks(
        {
            "dimension": "minecraft:overworld",
            "world_time": day_time,
            "food_level": food_level,
            "health": health,
            "block_position": {"x": 0, "y": 64, "z": 0},
        },
        {},
    )
    handler = BootSequenceHandler()
    monkeypatch.setattr(
        handler,
        "_plant_crops",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("micro farm must not run while pacing gate fails")
        ),
    )
    monkeypatch.setattr(
        "baritone_client.actions.homestead.count_item",
        lambda *_args: edible_counts.get(_args[1], 0),
    )
    monkeypatch.setattr(
        handler,
        "_enforce_anchor_and_pacing",
        lambda *_args, **_kwargs: None,
    )

    with pytest.raises(SurvivalRecoveryRequired):
        handler.execute(transport, SimpleNamespace(), state)


def test_boot_micro_farm_completes_before_optional_expansion(monkeypatch):
    state = _state_with_payloads({
        "homestead": {
            "anchor": [0, 64, 0],
            "steps": {
                "dry_anchor": {"verified": True},
                "wood_reserve": {"verified": True},
                "stone_reserve": {"verified": True},
                "infrastructure": {"verified": True},
                "micro_farm": {"verified": False},
                "torch_supply": {"verified": False},
                "light_perimeter": {"verified": False},
            },
        },
    })
    transport = _transport_with_blocks(
        {
            "dimension": "minecraft:overworld",
            "world_time": 1000,
            "food_level": 20,
            "health": 20,
            "block_position": {"x": 0, "y": 64, "z": 0},
        },
        {},
    )
    handler = BootSequenceHandler()
    calls = []

    monkeypatch.setattr(
        handler,
        "_run_micro_farm_step",
        lambda *_args, **_kwargs: calls.append("micro") or True,
    )
    monkeypatch.setattr(
        handler,
        "_run_torch_supply_step",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("torch step must wait until micro farm completes")
        ),
    )
    monkeypatch.setattr(
        handler,
        "_run_light_perimeter_step",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("light perimeter must wait until micro farm completes")
        ),
    )

    with pytest.raises(IncrementalProgressRequired):
        handler.execute(transport, SimpleNamespace(), state)
    assert calls == ["micro"]


def test_charcoal_step_gathers_only_bounded_local_wood(monkeypatch):
    state = _state_with_payloads(
        {
            "homestead": {
                "anchor": [0, 64, 0],
                "steps": {"charcoal_supply": {"verified": False}},
            }
        }
    )
    transport, _ = _transport_for_blocks()
    inventory = {}
    gather_calls = []

    monkeypatch.setattr(
        "baritone_client.actions.homestead.count_item",
        lambda _client, item: inventory.get(item, 0),
    )

    def gather(_client, **kwargs):
        gather_calls.append(kwargs)
        inventory["minecraft:oak_log"] = 2
        return True

    monkeypatch.setattr("baritone_client.actions.homestead.gather_wood", gather)
    runner = IncrementalHomestead(
        SimpleNamespace(transport=transport),
        state,
        lambda _client: False,
    )
    homestead = runner.load()

    assert runner.run_charcoal_supply(homestead)
    assert not runner.step(homestead, "charcoal_supply")["verified"]
    assert gather_calls == [
        {
            "count": 2,
            "timeout": 120,
            "latest_world_time": 11500,
            "max_distance_from_origin": 24.0,
            "abort_on_threats": True,
            "minimum_health": 18.0,
        }
    ]


def test_charcoal_step_requests_raw_log_when_only_planks_remain(monkeypatch):
    state = _state_with_payloads(
        {
            "homestead": {
                "anchor": [0, 64, 0],
                "steps": {"charcoal_supply": {"verified": False}},
            }
        }
    )
    transport, _ = _transport_for_blocks()
    inventory = {"minecraft:oak_planks": 8}
    requested = []

    monkeypatch.setattr(
        "baritone_client.actions.homestead.count_item",
        lambda _client, item: inventory.get(item, 0),
    )

    def gather(_client, **kwargs):
        requested.append(kwargs["count"])
        inventory["minecraft:oak_log"] = 1
        return True

    monkeypatch.setattr("baritone_client.actions.homestead.gather_wood", gather)
    runner = IncrementalHomestead(
        SimpleNamespace(transport=transport),
        state,
        lambda _client: False,
    )

    assert runner.run_charcoal_supply(runner.load())
    assert requested == [3]


def test_homestead_step_returns_home_before_resource_work(monkeypatch):
    state = _state_with_payloads(
        {
            "homestead": {
                "anchor": [0, 64, 0],
                "steps": {
                    "dry_anchor": {"verified": True},
                    "wood_reserve": {"verified": False},
                },
            }
        }
    )
    live_state = {
        "dimension": "minecraft:overworld",
        "world_time": 1000,
        "food_level": 20,
        "health": 20,
        "block_position": {"x": 30, "y": 64, "z": 0},
    }
    transport, _ = _transport_for_blocks(state_payload=live_state)
    routes = []

    def return_home(_client, x, y, z, **kwargs):
        routes.append((x, y, z, kwargs))
        live_state["block_position"] = {"x": x, "y": y, "z": z}
        return True

    monkeypatch.setattr("baritone_client.actions.homestead.goto", return_home)
    monkeypatch.setattr(
        "baritone_client.actions.homestead.gather_wood",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("resource work must wait for the next pass")
        ),
    )
    runner = IncrementalHomestead(
        SimpleNamespace(transport=transport),
        state,
        lambda _client: False,
    )
    homestead = runner.load()

    assert runner.run_wood_reserve(homestead)
    assert homestead["last_return_home"] == [0, 64, 0]
    assert routes == [
        (
            0,
            64,
            0,
            {"timeout": 180, "check_interval": 1.0, "tolerance": 4.0},
        )
    ]
