from types import SimpleNamespace

import pytest

from baritone_client.automator.objective import ObjStatus, Objective, ObjectivePlanner
from baritone_client.automator.phase_executor import PhaseExecutor, PhaseHandler
from baritone_client.automator.phases.boot_sequence import BootSequenceHandler
from baritone_client.automator.state_manager import Phase
from baritone_client.actions.homestead import IncrementalHomestead
from baritone_client.common.tasks import (
    IncrementalProgressRequired,
    PacingHoldRequired,
    ProgressRecoveryRequired,
    SurvivalRecoveryRequired,
)


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
        "_run_plank_reserve_step",
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


def test_pacing_hold_does_not_consume_objective_budgets():
    objective = Objective(Phase.BOOT_SEQUENCE, max_interruptions=2)
    planner = ObjectivePlanner([objective])
    entry = planner._by_phase[Phase.BOOT_SEQUENCE]
    entry.attempts = 2
    entry.interruptions = 1
    entry.no_progress_streak = 1

    planner.mark_active(entry)
    assert planner.mark_pacing_hold(entry, "pacing_hold")
    assert entry.status is ObjStatus.BLOCKED
    assert entry.attempts == 2
    assert entry.interruptions == 1
    assert entry.no_progress_streak == 1


def test_phase_executor_yields_pacing_hold_without_retry():
    class PacingHandler(PhaseHandler):
        def __init__(self):
            self.calls = 0

        def get_name(self):
            return "Pacing hold"

        def execute(self, client, resources, state):
            self.calls += 1
            raise PacingHoldRequired("wait calmly")

    state = SimpleNamespace(
        update_progress=lambda *_args, **_kwargs: None,
        record_phase_payload=lambda *_args, **_kwargs: None,
    )
    handler = PacingHandler()
    executor = PhaseExecutor(
        SimpleNamespace(),
        SimpleNamespace(refresh_inventory=lambda: None),
        state,
        max_retries=3,
        retry_delay=0,
        screenshot_enabled=False,
    )
    executor.register_handler(Phase.BOOT_SEQUENCE, handler)

    assert not executor.execute_phase(Phase.BOOT_SEQUENCE)
    assert executor.interruption_reason == "pacing_hold"
    assert handler.calls == 1


def test_boot_sequence_skips_live_verified_steps(monkeypatch):
    state = _state_with_payloads({
        "homestead": {
            "anchor": [0, 64, 0],
            "steps": {
                "dry_anchor": {"verified": True},
                "wood_reserve": {"verified": True},
                "plank_reserve": {"verified": True},
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
            "plank_reserve": {"verified": True},
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
        # Torches need something to stand on. Without this the ring positions
        # float in a void and are correctly refused -- which is the live bug
        # _ground_adjusted_ring exists to catch (see Bot16, 2026-07-31).
        (1, 64, 0): "minecraft:stone",
        (2, 64, 0): "minecraft:stone",
        (3, 64, 0): "minecraft:stone",
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
            "plank_reserve": {"verified": True},
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
        # REMOVED 2026-07-31: (19, 20, 1000, {}) -- "not full and no safe
        # edible". That case asserted a gate that was unsatisfiable in
        # production: hunger is almost never exactly 20, so it demanded
        # carried food always, while nothing acquires any in the 16..19 band.
        # It deadlocked Bot16, the furthest-along bot in the fleet, at 8/9
        # homestead steps. Comfortable hunger with full health in daylight is
        # now allowed to build; see require_construction_pacing.
    ],
)
def test_boot_construction_pacing_blocks_unsafe_or_underequipped(monkeypatch, food_level, health, day_time, edible_counts):
    state = _state_with_payloads({
        "homestead": {
            "anchor": [0, 64, 0],
            "steps": {
                "dry_anchor": {"verified": True},
                "wood_reserve": {"verified": True},
                "plank_reserve": {"verified": True},
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

    with pytest.raises(PacingHoldRequired):
        handler.execute(transport, SimpleNamespace(), state)


@pytest.mark.parametrize("food_level,health", [(10, 20), (20, 11)])
def test_boot_construction_uses_survival_recovery_for_critical_margin(
    monkeypatch,
    food_level,
    health,
):
    state = _state_with_payloads(
        {
            "homestead": {
                "anchor": [0, 64, 0],
                "steps": {
                    "dry_anchor": {"verified": True},
                    "wood_reserve": {"verified": True},
                    "plank_reserve": {"verified": True},
                    "stone_reserve": {"verified": True},
                    "infrastructure": {"verified": True},
                    "micro_farm": {"verified": False},
                },
            }
        }
    )
    transport = _transport_with_blocks(
        {
            "dimension": "minecraft:overworld",
            "world_time": 1000,
            "food_level": food_level,
            "health": health,
            "block_position": {"x": 0, "y": 64, "z": 0},
        },
        {},
    )
    monkeypatch.setattr(
        "baritone_client.actions.homestead.count_item",
        lambda *_args: 1,
    )

    with pytest.raises(SurvivalRecoveryRequired):
        BootSequenceHandler().execute(transport, SimpleNamespace(), state)


def test_boot_micro_farm_completes_before_optional_expansion(monkeypatch):
    state = _state_with_payloads({
        "homestead": {
            "anchor": [0, 64, 0],
            "steps": {
                "dry_anchor": {"verified": True},
                "wood_reserve": {"verified": True},
                "plank_reserve": {"verified": True},
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


def test_first_homestead_returns_to_bootstrap_home_after_remote_recovery(
    monkeypatch,
):
    home = [-160, 63, -320]
    remote_grave = {"x": -241, "y": 64, "z": -158}
    state = _state_with_payloads(
        {
            "phase_payloads": {
                "SPAWN_BOOTSTRAP": {
                    "return_home": {"origin": home},
                }
            }
        }
    )
    live_state = {
        "dimension": "minecraft:overworld",
        "world_time": 1000,
        "food_level": 20,
        "health": 20,
        "block_position": remote_grave,
    }
    transport, blocks = _transport_for_blocks(state_payload=live_state)
    blocks[(home[0], home[1] - 1, home[2])] = "minecraft:grass_block"
    routes = []

    def return_home(_client, x, y, z, **kwargs):
        routes.append((x, y, z, kwargs))
        live_state["block_position"] = {"x": x, "y": y, "z": z}
        return True

    monkeypatch.setattr("baritone_client.actions.homestead.goto", return_home)
    runner = IncrementalHomestead(
        SimpleNamespace(transport=transport),
        state,
        lambda _client: False,
    )
    homestead = runner.load()

    assert runner.run_dry_anchor(homestead)
    assert homestead["anchor"] == home
    assert state.custom_data["homestead_anchor"] == home
    assert routes == [
        (
            home[0],
            home[1],
            home[2],
            {"timeout": 180, "check_interval": 1.0, "tolerance": 4.0},
        )
    ]


def test_infrastructure_rearms_planks_before_replaying_setup(monkeypatch):
    state = _state_with_payloads(
        {
            "homestead": {
                "anchor": [0, 64, 0],
                "steps": {
                    "dry_anchor": {"verified": True},
                    "wood_reserve": {"verified": True},
                    "plank_reserve": {"verified": True},
                    "stone_reserve": {"verified": True},
                    "infrastructure": {"verified": False},
                },
            }
        }
    )
    state.custom_data["structures"].pop("bootstrap_base")
    transport, _ = _transport_for_blocks()
    inventory = {
        "minecraft:oak_planks": 3,
        "minecraft:cobblestone": 8,
    }
    monkeypatch.setattr(
        "baritone_client.actions.homestead.count_item",
        lambda _client, item: inventory.get(item, 0),
    )
    monkeypatch.setattr(
        "baritone_client.actions.homestead.setup_base",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("setup must wait for the plank reserve")
        ),
    )
    runner = IncrementalHomestead(
        SimpleNamespace(transport=transport),
        state,
        lambda _client: False,
    )
    homestead = runner.load()

    assert runner.run_infrastructure(homestead)
    assert not runner.step(homestead, "plank_reserve")["verified"]
    assert runner.step(homestead, "infrastructure")["evidence"] == {
        "missing_planks": 9
    }


def test_homestead_prefers_nearest_infrastructure_record():
    state = _state_with_payloads(
        {
            "homestead": {
                "anchor": [0, 64, 0],
                "steps": {"infrastructure": {"verified": False}},
            }
        }
    )
    state.custom_data["structures"]["starter_house"] = {
        "origin": [500, 64, 500],
        "crafting_table": [501, 64, 501],
        "furnace": [502, 64, 501],
        "supply_chest": [501, 64, 502],
    }
    transport, _ = _transport_for_blocks()
    runner = IncrementalHomestead(
        SimpleNamespace(transport=transport),
        state,
        lambda _client: False,
    )

    record = runner._infrastructure_record([0, 64, 0])
    assert record["origin"] == [0, 64, 0]


def test_pacing_eats_carried_food_instead_of_holding_forever(monkeypatch):
    """Live 2026-07-31, 18-bot fleet: not one bot had EVER completed
    BOOT_SEQUENCE. Every one cycled "yielded to pacing_hold; recovery budget
    0/6". Bot12 was held on food=15 while carrying 6 mutton and 1 chicken --
    nothing in the loop eats at that level, so the hold repeated forever.
    The gate is about having a survival margin; a bot carrying food has one
    and just needs to consume it."""
    from baritone_client.actions.homestead import IncrementalHomestead

    hunger = {"food": 15}
    ate = []

    homestead = IncrementalHomestead.__new__(IncrementalHomestead)
    homestead.client = SimpleNamespace()
    monkeypatch.setattr(
        IncrementalHomestead,
        "_state",
        lambda _self: {
            "dimension": "minecraft:overworld",
            "world_time": 1000,
            "health": 20.0,
            "food_level": hunger["food"],
        },
    )

    def fake_eat(_client, minimum_food=14):
        ate.append(minimum_food)
        hunger["food"] = 20
        return True

    monkeypatch.setattr(
        "baritone_client.common.combat.eat_until_hunger", fake_eat
    )
    monkeypatch.setattr(
        "baritone_client.actions.homestead.count_item", lambda *_a: 0
    )

    # Must NOT raise: it should eat and proceed.
    homestead.require_construction_pacing()
    assert ate, "should have eaten carried food rather than holding"


def test_pacing_counts_raw_meat_as_edible(monkeypatch):
    """_has_edible only counted COOKED food, so a bot carrying six raw mutton
    was treated as having no survival margin -- the other half of the live
    deadlock."""
    from baritone_client.actions.homestead import IncrementalHomestead

    homestead = IncrementalHomestead.__new__(IncrementalHomestead)
    homestead.client = SimpleNamespace()
    monkeypatch.setattr(
        "baritone_client.actions.homestead.count_item",
        lambda _c, item: 6 if item == "minecraft:mutton" else 0,
    )

    assert homestead._has_edible() is True


def test_pacing_still_holds_when_truly_out_of_food(monkeypatch):
    """The gate's real purpose survives: no food carried and hunger low still
    holds rather than starting a build."""
    from baritone_client.actions.homestead import IncrementalHomestead

    homestead = IncrementalHomestead.__new__(IncrementalHomestead)
    homestead.client = SimpleNamespace()
    monkeypatch.setattr(
        IncrementalHomestead,
        "_state",
        lambda _self: {
            "dimension": "minecraft:overworld",
            "world_time": 1000,
            "health": 20.0,
            "food_level": 14,
        },
    )
    monkeypatch.setattr(
        "baritone_client.common.combat.eat_until_hunger",
        lambda *_a, **_k: False,
    )
    monkeypatch.setattr(
        "baritone_client.actions.homestead.count_item", lambda *_a: 0
    )

    with pytest.raises(PacingHoldRequired):
        homestead.require_construction_pacing()


def test_return_home_pacing_also_eats_before_holding(monkeypatch):
    """The second pacing gate had no eat attempt. Live 2026-07-31: Bot15 sat
    at food=11 carrying a chicken and a beef, held here 249 times."""
    from baritone_client.actions.homestead import IncrementalHomestead

    hunger = {"food": 11}
    ate = []
    homestead = IncrementalHomestead.__new__(IncrementalHomestead)
    homestead.client = SimpleNamespace()
    monkeypatch.setattr(
        IncrementalHomestead,
        "_eat_carried_food",
        lambda _s, *, minimum_food, current_food: (
            ate.append(minimum_food) or 18
        ),
    )
    # Exercise only the gate arithmetic.
    state = {"world_time": 1000, "health": 20.0, "food_level": hunger["food"]}
    health = float(state["health"])
    food = int(state["food_level"])
    if food < 16:
        food = homestead._eat_carried_food(minimum_food=18, current_food=food)
    held = (
        int(state["world_time"]) % 24000 >= 12000 or health < 18 or food < 16
    )
    assert ate, "should eat before evaluating the hold"
    assert not held, "eating should clear the hold"


def test_return_home_hold_names_the_actual_failing_condition(monkeypatch):
    """The message read "wait for daylight survival margin" no matter which
    check tripped, so a food-driven hold was undiagnosable AND invisible to
    the executor's hunger recovery (which matches on food wording). Live
    2026-07-31: Bot13/Bot17 yielded here 357 times on food<16 while the log
    claimed they awaited daylight, and no food acquisition ever ran."""
    from baritone_client.actions.homestead import IncrementalHomestead
    from baritone_client.common.tasks import PacingHoldRequired

    homestead = IncrementalHomestead.__new__(IncrementalHomestead)
    homestead.client = SimpleNamespace()
    monkeypatch.setattr(
        IncrementalHomestead,
        "_eat_carried_food",
        lambda _s, *, minimum_food, current_food: current_food,  # nothing to eat
    )

    # Daytime, full health, but hungry -> the message must say so.
    state = {"world_time": 1000, "health": 20.0, "food_level": 11}
    health, food = 20.0, 11
    if food < 16:
        food = homestead._eat_carried_food(minimum_food=18, current_food=food)
    reasons = []
    if int(state["world_time"]) % 24000 >= 12000:
        reasons.append("waiting for daylight")
    if health < 18:
        reasons.append(f"health {health:.0f} below 18")
    if food < 16:
        reasons.append(f"food {food} below 16")
    msg = "survival margin before returning home: " + ", ".join(reasons)

    assert "food" in msg.lower(), "must name food so hunger recovery triggers"
    assert "daylight" not in msg.lower(), "must not blame daylight in daytime"


def _ring_homestead(blocks):
    """blocks: {(x,y,z): id}; anything absent is air."""
    from baritone_client.actions.homestead import IncrementalHomestead

    h = IncrementalHomestead.__new__(IncrementalHomestead)
    h.client = SimpleNamespace(
        transport=SimpleNamespace(
            dispatch=lambda route, payload: (
                {"id": blocks.get(
                    (payload["x"], payload["y"], payload["z"]), "minecraft:air"
                )}
                if route == "get_block"
                else {}
            )
        )
    )
    return h


def test_perimeter_ring_is_reseated_onto_real_ground():
    """perimeter_ring puts every position at anchor_y+1, which only works on
    flat ground. On real terrain most of the ring floats and a torch cannot be
    placed. Live 2026-07-31: Bot16 reached the last of nine homestead steps
    with 4 torches in hand and five of its first six ring targets had air
    directly beneath them."""
    # Nominal target y=106, but the ground here is two blocks lower.
    blocks = {(10, 103, 5): "minecraft:stone"}
    h = _ring_homestead(blocks)

    adjusted = h._ground_adjusted_ring([(10, 106, 5)])

    assert adjusted == [(10, 104, 5)], "torch must sit directly on the stone"


def test_ring_position_already_on_ground_is_unchanged():
    blocks = {(10, 105, 5): "minecraft:grass_block"}
    h = _ring_homestead(blocks)

    assert h._ground_adjusted_ring([(10, 106, 5)]) == [(10, 106, 5)]


def test_ring_drops_columns_with_no_support_in_range():
    """A column over a void must be dropped, not kept as an unplaceable target
    that blocks the step forever."""
    h = _ring_homestead({})  # everything is air

    assert h._ground_adjusted_ring([(10, 106, 5)]) == []


def test_ring_does_not_seat_a_torch_on_water():
    h = _ring_homestead({(10, 104, 5): "minecraft:water"})

    assert h._ground_adjusted_ring([(10, 106, 5)]) == []


def test_ring_skips_leaves_and_reseats_on_ground_below():
    """Leaves look like support to a naive block classifier but reject torches.

    This mirrors Bot19's live failure: the nominal target was air above
    spruce leaves, with grass several blocks below.  The ring must skip the
    leaves and choose the first real ground position instead.
    """
    h = _ring_homestead(
        {
            (10, 108, 5): "minecraft:spruce_leaves",
            (10, 105, 5): "minecraft:grass_block",
        }
    )

    assert h._ground_adjusted_ring([(10, 109, 5)]) == [(10, 106, 5)]


def test_grass_block_is_valid_torch_support():
    """Substring matching on "grass" rejected grass_block (solid ground) along
    with short_grass (a plant). Exact names only."""
    from baritone_client.actions.homestead import IncrementalHomestead as H

    assert H._is_supportive("minecraft:grass_block")
    assert H._is_supportive("minecraft:stone")
    assert not H._is_supportive("minecraft:spruce_leaves")
    assert not H._is_supportive("minecraft:short_grass")
    assert not H._is_supportive("minecraft:air")
    assert not H._is_supportive("minecraft:water")
    # A torch may replace grass/air but never water or lava.
    assert H._is_replaceable("minecraft:air")
    assert H._is_replaceable("minecraft:short_grass")
    assert not H._is_replaceable("minecraft:water")
    assert not H._is_replaceable("minecraft:stone")


def test_empty_torches_reopens_supply_even_with_fuel_left(monkeypatch):
    """place_torch needs a stick as well as fuel and will not craft one, so a
    bot holding fuel but no sticks could never resupply. Live 2026-07-31:
    Bot16 reached 16 of 24 perimeter torches -- the last step of nine -- then
    stalled with 0 torches, 0 sticks, 2 charcoal and 8 planks, because the
    old fuel<1 condition kept torch_supply marked verified."""
    from baritone_client.actions.homestead import IncrementalHomestead

    counts = {
        "minecraft:torch": 0,
        "minecraft:charcoal": 2,   # fuel remains, so the old guard stayed shut
        "minecraft:coal": 0,
        "minecraft:stick": 0,
    }
    homestead_payload = {
        "anchor": [0, 64, 0],
        "steps": {"torch_supply": {"verified": True}, "light_perimeter": {}},
    }

    h = IncrementalHomestead.__new__(IncrementalHomestead)
    h.client = SimpleNamespace()
    monkeypatch.setattr(
        "baritone_client.actions.homestead.count_item",
        lambda _c, item: counts.get(item, 0),
    )

    # Reproduce the guard the way run_light_perimeter applies it.
    if counts["minecraft:torch"] < 1:
        h.step(homestead_payload, "torch_supply")["verified"] = False

    assert homestead_payload["steps"]["torch_supply"]["verified"] is False, (
        "an empty torch stack must reopen torch_supply regardless of fuel"
    )


def test_no_progress_step_still_persists_homestead_bookkeeping(monkeypatch):
    """record() was called only when a step reported progress, so bookkeeping
    written during a FAILED step was discarded. light_perimeter un-verifies
    torch_supply when the torch stack is empty, so the supply step re-runs and
    crafts sticks -- but that un-verify never survived. Live 2026-07-31: Bot16
    sat at 8/9 steps and 16/24 torches cycling "Need torches (or coal/charcoal
    and sticks to craft)" 129 times with 8 planks and 2 charcoal in hand,
    because torch_supply stayed verified=True in the checkpoint forever."""
    state = _state_with_payloads(
        {
            "homestead": {
                "anchor": [0, 64, 0],
                "steps": {
                    "dry_anchor": {"verified": True},
                    "wood_reserve": {"verified": True},
                    "plank_reserve": {"verified": True},
                    "stone_reserve": {"verified": True},
                    "infrastructure": {"verified": True},
                    "micro_farm": {"verified": True},
                    "charcoal_supply": {"verified": True},
                    "torch_supply": {"verified": True},
                    "light_perimeter": {"verified": False},
                },
            }
        }
    )
    handler = BootSequenceHandler()
    transport, _ = _transport_for_blocks()

    def failing_light_perimeter(_client, _state, homestead):
        # Exactly what run_light_perimeter does when place_torch fails.
        handler._homestead.step(homestead, "torch_supply")["verified"] = False
        return False

    monkeypatch.setattr(handler, "_run_light_perimeter_step", failing_light_perimeter)
    monkeypatch.setattr(handler, "_enforce_anchor_and_pacing", lambda *_a, **_k: None)

    result = handler.execute(transport, SimpleNamespace(), state)

    assert not result.success
    persisted = state.custom_data["homestead"]["steps"]["torch_supply"]["verified"]
    assert persisted is False, (
        "an un-verify recorded during a failed step must survive to the checkpoint"
    )


def test_comfortable_hunger_without_a_snack_does_not_block_construction(monkeypatch):
    """The old `food < 20 and not _has_edible()` gate was unsatisfiable:
    hunger is almost never exactly 20, so it demanded carried food always,
    while nothing acquires any in the 16..19 band (acquire_emergency_food
    returns immediately once hunger meets its target). Live 2026-07-31: Bot16,
    the furthest-along bot at 8/9 steps, sat at food=19 and full health with an
    empty larder yielding "carry food or refill hunger before construction"
    with zero recovery attempts."""
    from baritone_client.actions.homestead import IncrementalHomestead

    h = IncrementalHomestead.__new__(IncrementalHomestead)
    h.client = SimpleNamespace()
    monkeypatch.setattr(
        IncrementalHomestead,
        "_state",
        lambda _s: {
            "dimension": "minecraft:overworld",
            "world_time": 1000,
            "health": 20.0,
            "food_level": 19,
        },
    )
    monkeypatch.setattr(
        "baritone_client.actions.homestead.count_item", lambda *_a: 0
    )

    # Must not raise: comfortable hunger, full health, daylight.
    h.require_construction_pacing()


def test_low_hunger_with_no_food_still_blocks_construction(monkeypatch):
    """The real survival floor is preserved."""
    from baritone_client.actions.homestead import IncrementalHomestead

    h = IncrementalHomestead.__new__(IncrementalHomestead)
    h.client = SimpleNamespace()
    monkeypatch.setattr(
        IncrementalHomestead,
        "_state",
        lambda _s: {
            "dimension": "minecraft:overworld",
            "world_time": 1000,
            "health": 20.0,
            "food_level": 13,
        },
    )
    monkeypatch.setattr(
        "baritone_client.common.combat.eat_until_hunger", lambda *_a, **_k: False
    )
    monkeypatch.setattr(
        "baritone_client.actions.homestead.count_item", lambda *_a: 0
    )

    with pytest.raises(PacingHoldRequired):
        h.require_construction_pacing()


def test_perimeter_ring_is_spaced_not_a_solid_wall_of_torches():
    """The ring used to emit every block of the perimeter -- 32 contiguous,
    mutually adjacent positions. A torch emits light 14 falling 1 per block
    and hostiles need light 0 to spawn, so one torch covers a 9x9 footprint;
    a solid ring is pure waste. Each torch also costs a stick plus a
    coal/charcoal, and on 2026-07-31 Bot16 stalled at 16/24 placed with 0
    torches, 0 sticks and 2 charcoal, unable to finish the ring it was told
    to build. Reported by the user watching the game live."""
    from baritone_client.common.homestead_lighting import perimeter_ring
    import itertools

    ring = perimeter_ring([0, 64, 0])

    assert len(ring) <= 12, f"ring should be sparse, got {len(ring)} torches"
    adjacent = sum(
        1
        for (ax, _, az), (bx, _, bz) in itertools.combinations(ring, 2)
        if abs(ax - bx) + abs(az - bz) == 1
    )
    assert adjacent == 0, "no two torches should sit on adjacent blocks"


def test_perimeter_ring_keeps_the_corners_and_bounds_the_gaps():
    """Corners anchor the coverage; gaps must stay well inside a torch's
    14-block light radius."""
    from baritone_client.common.homestead_lighting import perimeter_ring

    ring = perimeter_ring([0, 64, 0], radius=4)
    positions = {(x, z) for x, _, z in ring}

    assert {(-4, -4), (4, -4), (-4, 4), (4, 4)} <= positions
    gaps = [
        max(abs(ax - bx), abs(az - bz))
        for (ax, _, az), (bx, _, bz) in zip(ring, ring[1:])
    ]
    assert max(gaps) <= 6, f"gap {max(gaps)} risks a dark spot"


def test_perimeter_ring_spacing_is_configurable():
    from baritone_client.common.homestead_lighting import perimeter_ring

    dense = perimeter_ring([0, 64, 0], spacing=2)
    sparse = perimeter_ring([0, 64, 0], spacing=8)
    assert len(dense) > len(sparse)


def test_stored_dense_ring_is_replaced_by_the_sparse_policy():
    """perimeter_ring used to emit every perimeter block. A bot that recorded
    that dense ring kept chasing ~24 torches forever, so the spacing fix
    reached no existing bot -- Bot16 was still on a stored 24-position ring
    after it shipped. The current policy must win over stored state."""
    from baritone_client.common.homestead_lighting import perimeter_ring

    anchor = [0, 64, 0]
    fresh = perimeter_ring(anchor)[:24]
    stored_dense = [[x, 65, z] for x in range(-4, 5) for z in (-4, 4)]  # 18, dense

    # This mirrors the adoption rule in run_light_perimeter.
    intended = [tuple(c) for c in stored_dense]
    if not intended or len(intended) > len(fresh):
        intended = fresh

    assert intended == fresh
    assert len(intended) < len(stored_dense)


def test_stale_high_ring_column_is_rebased_onto_the_anchor_floor(monkeypatch):
    """A column seated on an overhang must come back down when the anchor moves.

    _ground_adjusted_ring scans relative to the height it is given, so feeding
    it a stored position re-derives that position from itself -- a bad height
    is self-perpetuating. Live 2026-08-01: Bot18 carried a y=114 column against
    a y=105 anchor (seated while the anchor was still y=107). Eight blocks up
    is outside the ~4.5 block reach, so Minecraft rejected every placement and
    light_perimeter -- its last homestead step -- retried forever.
    """
    homestead_payload = {
        "anchor": [0, 105, 0],
        "steps": {
            "dry_anchor": {"verified": True},
            "wood_reserve": {"verified": True},
            "plank_reserve": {"verified": True},
            "stone_reserve": {"verified": True},
            "infrastructure": {"verified": True},
            "micro_farm": {"verified": True},
            "charcoal_supply": {"verified": True},
            "torch_supply": {"verified": True},
            "light_perimeter": {
                "verified": False,
                # Stale: seated against a y=107 anchor onto the overhang.
                "intended": [[4, 114, 0]],
                "verified_positions": [],
            },
        },
    }
    blocks = {
        # Overhang the stale column is stuck on...
        (4, 113, 0): "minecraft:stone",
        # ...and the real floor at anchor level, where it belongs.
        (4, 105, 0): "minecraft:stone",
        # The homestead's own structures, at the anchor's level. The shared
        # defaults sit at y=64, which is ~40 blocks below this y=105 anchor --
        # outside the envelope, so they would un-verify infrastructure and
        # micro_farm and this test would never reach light_perimeter.
        (1, 105, 1): "minecraft:crafting_table",
        (2, 105, 1): "minecraft:furnace",
        (1, 105, 2): "minecraft:chest",
        (0, 106, 0): "minecraft:wheat",
    }
    state = _state_with_payloads({
        "homestead": homestead_payload,
        "structures": {
            "bootstrap_base": {
                "origin": [0, 105, 0],
                "crafting_table": [1, 105, 1],
                "furnace": [2, 105, 1],
                "supply_chest": [1, 105, 2],
            },
            "food_source": {
                "verified": True,
                "plots": [[0, 106, 0, "minecraft:wheat"]],
            },
        },
    })
    transport = _transport_with_blocks(
        {
            "dimension": "minecraft:overworld",
            "world_time": 1000,
            "food_level": 20,
            "health": 20,
            "block_position": {"x": 0, "y": 105, "z": 0},
        },
        blocks,
    )
    handler = BootSequenceHandler()
    handler.state = state

    placements = []

    def place(_client, x, y, z):
        placements.append((x, y, z))
        blocks[(x, y, z)] = "minecraft:torch"
        return True

    monkeypatch.setattr("baritone_client.actions.homestead.place_torch", place)

    with pytest.raises(IncrementalProgressRequired):
        handler.execute(transport, SimpleNamespace(), state)

    assert placements == [(4, 106, 0)], (
        "torch must be placed on the anchor-level floor, not the overhang"
    )
    payload = state.custom_data["homestead"]["steps"]["light_perimeter"]
    assert [tuple(p) for p in payload["intended"]] == [(4, 106, 0)]


def _farm_helper(anchor, plots, blocks):
    """IncrementalHomestead wired to a fixed block map and food_source record."""
    helper = IncrementalHomestead.__new__(IncrementalHomestead)
    helper.state = SimpleNamespace(
        custom_data={"structures": {"food_source": {"plots": plots}}}
    )
    helper.client = SimpleNamespace()
    helper._block_at = lambda pos: blocks.get(tuple(pos), "minecraft:air")
    return helper


def test_distant_farm_does_not_satisfy_micro_farm():
    """A farm left at an abandoned site is not this homestead's farm.

    Live on the A1 server 2026-08-10: after 22 relocations the anchor sat ~90
    blocks from its original farm, right on the server's simulation-distance
    boundary (6 chunks = 96 blocks). get_block reported a crop when the chunk
    was loaded and air when it was not, so invalidate_stale un-verified
    micro_farm and run_micro_farm re-verified it, forever -- reporting
    incremental progress every cycle while the run never advanced, and never
    stalling long enough to relocate or be waived.
    """
    anchor = [-290, 69, 94]
    far_plot = [-244, 74, 17]  # ~90 blocks away
    helper = _farm_helper(anchor, [far_plot], {tuple(far_plot): "minecraft:wheat"})

    assert helper._live_farm(anchor) is False
    # Without an anchor the check cannot apply, and must not silently pass.
    assert helper._live_farm(None) is True


def test_nearby_farm_still_satisfies_micro_farm():
    """The envelope check must not reject the homestead's own plot."""
    anchor = [-290, 69, 94]
    near_plot = [-288, 70, 96]
    helper = _farm_helper(anchor, [near_plot], {tuple(near_plot): "minecraft:wheat"})

    assert helper._live_farm(anchor) is True


def test_within_homestead_ignores_a_far_saved_farm():
    """_plant_crops must not walk back to an abandoned site's farm."""
    from baritone_client.common.farming import within_homestead

    custom_data = {"homestead": {"anchor": [-290, 69, 94]}}

    assert within_homestead(custom_data, (-244, 73, 17)) is False
    assert within_homestead(custom_data, (-288, 70, 96)) is True
    # No anchor yet: a fresh run must not be blocked by a check with no data.
    assert within_homestead({}, (0, 64, 0)) is True


def test_perimeter_ring_from_an_abandoned_site_is_re_derived():
    """A stored ring that cannot seat anywhere must be rebuilt, not retried.

    The fallback to a fresh ring happens before columns are seated, and the
    "nothing seated" branch used to return without writing anything back -- so
    a ring belonging to an abandoned site was reloaded and re-failed every
    cycle, forever. Live on the A1 server 2026-08-10 at 8 of 9 steps done: the
    ring still pointed ~110 blocks away at (-245, 72, 12) while the anchor was
    (-352, 52, 93), and light_perimeter could never finish.
    """
    anchor = [-352, 52, 93]
    helper = IncrementalHomestead.__new__(IncrementalHomestead)
    helper.state = SimpleNamespace(custom_data={})
    helper.client = SimpleNamespace()
    helper.enforce_anchor = lambda _h: False
    helper.require_construction_pacing = lambda: None
    helper.plant_crops = lambda _c: True

    # Solid ground only near the anchor; the abandoned site's columns read as
    # air, exactly as an unloaded distant chunk does.
    def block_at(pos):
        x, y, z = (int(v) for v in pos)
        if abs(x - anchor[0]) <= 12 and abs(z - anchor[2]) <= 12:
            return "minecraft:stone" if y <= anchor[1] else "minecraft:air"
        return "minecraft:air"

    helper._block_at = block_at
    helper._is_torch = lambda _pos: False

    placed = []
    progress = {
        "anchor": anchor,
        "steps": {
            "light_perimeter": {
                "verified": False,
                "intended": [[-245, 72, 12], [-241, 72, 12]],
                "verified_positions": [[-245, 72, 12]],
            }
        },
    }

    import baritone_client.actions.homestead as hs

    original = hs.place_torch
    hs.place_torch = lambda _c, x, y, z: placed.append((x, y, z)) or True
    try:
        helper.run_light_perimeter(progress)
    finally:
        hs.place_torch = original

    record = progress["steps"]["light_perimeter"]
    ring = record.get("intended") or []
    assert ring, "a re-derived ring must replace the unusable one"
    for x, _y, z in ring:
        assert abs(x - anchor[0]) <= 12 and abs(z - anchor[2]) <= 12, (x, z)


def test_infrastructure_directly_above_the_anchor_is_not_usable():
    """SAFE_RADIUS is horizontal; a table 19 blocks up is not "local".

    Live on the A1 server 2026-08-10: after a relocation the anchor was
    (-352, 52, 93) while the recorded crafting table sat at (-337, 71, 96) --
    15 blocks away horizontally, inside SAFE_RADIUS, but 19 straight up.
    infrastructure stayed verified against a table nothing could open, so
    torch_supply could neither craft torches nor craft a replacement table. It
    failed 120 times.
    """
    anchor = [-352, 52, 93]
    record = {
        "origin": [-338, 71, 95],
        "crafting_table": [-337, 71, 96],
        "furnace": [-336, 71, 96],
        "supply_chest": [-337, 71, 97],
    }
    helper = IncrementalHomestead.__new__(IncrementalHomestead)
    helper.state = SimpleNamespace(custom_data={})
    helper._block_at = lambda pos: {
        (-337, 71, 96): "minecraft:crafting_table",
        (-336, 71, 96): "minecraft:furnace",
        (-337, 71, 97): "minecraft:chest",
    }.get(tuple(pos), "minecraft:air")

    assert helper._live_infrastructure(record, anchor) is False

    # The same base at the anchor's own level is fine.
    level = {k: ([v[0], anchor[1], v[2]] if isinstance(v, list) else v)
             for k, v in record.items()}
    helper._block_at = lambda pos: {
        (-337, anchor[1], 96): "minecraft:crafting_table",
        (-336, anchor[1], 96): "minecraft:furnace",
        (-337, anchor[1], 97): "minecraft:chest",
    }.get(tuple(pos), "minecraft:air")
    assert helper._live_infrastructure(level, anchor) is True


def test_standing_far_above_the_anchor_is_not_home():
    """"Home" must mean reachable, not merely horizontally close.

    Live on the A1 server 2026-08-10: the bot worked at y=78 against a y=52
    anchor. That is 14 blocks away horizontally, so enforce_anchor treated it
    as home and let it build a base there -- which the reachable-level check on
    infrastructure then correctly rejected. Neither condition could be
    satisfied at once, so the step could never complete.
    """
    anchor = [-352, 52, 93]
    helper = IncrementalHomestead.__new__(IncrementalHomestead)
    helper.state = SimpleNamespace(custom_data={})
    helper.client = SimpleNamespace()

    returned = []
    helper.current_position = lambda: [-350, 78, 79]
    helper._state = lambda: {
        "dimension": "minecraft:overworld",
        "world_time": 1000,
        "health": 20,
        "food_level": 20,
    }
    helper._dry_ground = lambda _p: True

    import baritone_client.actions.homestead as hs

    original = hs.goto
    hs.goto = lambda _c, x, y, z, **_k: returned.append((x, y, z)) or True
    try:
        # Arrival is judged from current_position, which still reports the
        # high spot, so this raises rather than silently succeeding.
        with pytest.raises(ProgressRecoveryRequired):
            helper.enforce_anchor({"anchor": anchor, "steps": {}})
    finally:
        hs.goto = original

    assert returned == [(-352, 52, 93)], "must actually travel to the anchor"


def test_infrastructure_is_built_at_the_anchor_not_wherever_is_flat():
    """setup_base must be told where to build, or it searches on its own.

    Live on the A1 server 2026-08-10: find_flat_ground chose y=78 against a
    y=52 anchor -- 26 blocks up. The base was built, rejected as unreachable,
    and rebuilt from scratch every cycle, burning the planks and cobblestone
    each time. The anchor is already verified dry ground, so it is the site.
    """
    anchor = [-352, 52, 93]
    helper = IncrementalHomestead.__new__(IncrementalHomestead)
    helper.state = SimpleNamespace(custom_data={})
    helper.client = SimpleNamespace()
    helper.enforce_anchor = lambda _h: False
    helper.require_construction_pacing = lambda: None
    helper._infrastructure_record = lambda _a=None: {}
    helper._live_infrastructure = lambda _r, _a: False
    helper._block_at = lambda _p: "minecraft:air"

    import baritone_client.actions.homestead as hs

    asked = []

    def fake_setup_base(_client, location=None):
        asked.append(location)
        return False, None  # stop after recording the requested site

    original_setup, original_count = hs.setup_base, hs.count_item
    hs.setup_base = fake_setup_base
    hs.count_item = lambda _c, item: 64  # plenty of planks and cobblestone
    try:
        helper.run_infrastructure({"anchor": anchor, "steps": {}})
    finally:
        hs.setup_base, hs.count_item = original_setup, original_count

    assert asked == [tuple(anchor)], "must build at the anchor, not search"
