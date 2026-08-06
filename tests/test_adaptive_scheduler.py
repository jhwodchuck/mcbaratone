from types import SimpleNamespace

from baritone_client.automator import adaptive_scheduler as adaptive
from baritone_client.automator.adaptive_scheduler import (
    AdaptiveScheduler,
    GameSignals,
    LocalOpportunity,
    OpportunityKind,
    OpportunityResult,
    collect_game_signals,
    score_phase,
)
from baritone_client.automator.objective import (
    ObjStatus,
    ObjectivePlanner,
    default_objectives,
)
from baritone_client.automator.state_manager import Phase
from baritone_client.automator.end_readiness import FleetRole
from baritone_client.common.forestry import WoodCycleResult


def _state(custom_data=None):
    return SimpleNamespace(custom_data=custom_data or {})


def _signals(**overrides):
    values = {
        "observed": True,
        "entities_observed": True,
        "dimension": "minecraft:overworld",
        "health": 20.0,
        "food": 20,
        "world_time": 1000,
        "position": (0, 64, 0),
        "inventory": {},
        "adult_animals": {},
    }
    values.update(overrides)
    return GameSignals(**values)


def _post_food_planner():
    planner = ObjectivePlanner(default_objectives())
    planner.restore(
        [
            Phase.BRIDGE_CHECK,
            Phase.SPAWN_BOOTSTRAP,
            Phase.INITIAL_GATHERING,
            Phase.BOOT_SEQUENCE,
            Phase.BASE_CONSTRUCTION,
            Phase.FOOD_AND_IRON,
        ]
    )
    return planner


def test_no_live_signal_preserves_stable_nether_priority():
    planner = _post_food_planner()
    signals = GameSignals()

    selected = planner.select(
        planner.runnable(),
        utility=lambda objective: AdaptiveScheduler.objective_score(
            objective, signals
        ),
    )

    assert selected.phase is Phase.NETHER_AND_BLAZE


def test_breedable_leather_animals_prioritize_enchanting_sibling():
    planner = _post_food_planner()
    signals = _signals(
        adult_animals={"cow": 2},
        inventory={"minecraft:wheat": 2},
    )

    selected = planner.select(
        planner.runnable(),
        utility=lambda objective: AdaptiveScheduler.objective_score(
            objective, signals
        ),
    )

    assert selected.phase is Phase.ENCHANTING_PIPELINE
    assert "renewable leather pair" in " ".join(
        score_phase(selected.phase, signals).reasons
    )


def test_villagers_and_crops_prioritize_villager_infrastructure():
    planner = _post_food_planner()
    signals = _signals(
        adult_villagers=2,
        crop_location=(4, 64, 4),
        inventory={"minecraft:wheat": 18},
    )

    selected = planner.select(
        planner.runnable(),
        utility=lambda objective: AdaptiveScheduler.objective_score(
            objective, signals
        ),
    )

    assert selected.phase is Phase.VILLAGER_INFRA


def test_same_runnable_frontier_produces_different_work_per_bot_state():
    planner = _post_food_planner()
    animal_bot = _signals(
        adult_animals={"cow": 2},
        inventory={"minecraft:wheat": 2},
    )
    village_bot = _signals(
        adult_villagers=2,
        inventory={"minecraft:bread": 6},
    )
    nether_bot = _signals(dimension="minecraft:the_nether")

    def choose(signals):
        return planner.select(
            planner.runnable(),
            utility=lambda objective: AdaptiveScheduler.objective_score(
                objective, signals
            ),
        ).phase

    assert choose(animal_bot) is Phase.ENCHANTING_PIPELINE
    assert choose(village_bot) is Phase.VILLAGER_INFRA
    assert choose(nether_bot) is Phase.NETHER_AND_BLAZE


def test_completed_nether_supplier_returns_a_role_hold(tmp_path, monkeypatch):
    planner = _post_food_planner()
    planner._by_phase[Phase.NETHER_AND_BLAZE].status = ObjStatus.DONE
    state = SimpleNamespace(
        custom_data={},
        checkpoint_dir=tmp_path / "Bot16" / "controller",
    )
    scheduler = AdaptiveScheduler(SimpleNamespace(), SimpleNamespace(), state)
    monkeypatch.setattr(scheduler, "observe", lambda: _signals())

    decision = scheduler.next_step(planner)

    assert decision.objective is None
    assert decision.role_hold is True
    assert "completed its assignment" in decision.summary


def test_live_signal_never_bypasses_graph_prerequisites():
    planner = ObjectivePlanner(default_objectives())
    signals = _signals(adult_villagers=20, crop_location=(1, 64, 1))

    selected = planner.select(
        planner.runnable(),
        utility=lambda objective: AdaptiveScheduler.objective_score(
            objective, signals
        ),
    )

    assert selected.phase is Phase.BRIDGE_CHECK


def test_animal_farm_is_selected_when_pair_and_food_are_local():
    scheduler = AdaptiveScheduler(SimpleNamespace(), SimpleNamespace(), _state())
    opportunity = scheduler.select_local_opportunity(
        _signals(
            adult_animals={"cow": 2},
            inventory={"minecraft:wheat": 2},
        ),
        [Phase.SPAWN_BOOTSTRAP],
        now=1000.0,
    )

    assert opportunity is not None
    assert opportunity.kind is OpportunityKind.ANIMAL_FARM
    assert opportunity.animal_type == "cow"


def test_crop_farm_is_selected_when_crop_patch_can_replenish_food():
    scheduler = AdaptiveScheduler(SimpleNamespace(), SimpleNamespace(), _state())
    opportunity = scheduler.select_local_opportunity(
        _signals(
            crop_location=(5, 64, 5),
            crop_block="minecraft:wheat",
            inventory={"minecraft:wheat_seeds": 4},
        ),
        [Phase.SPAWN_BOOTSTRAP],
        now=1000.0,
    )

    assert opportunity is not None
    assert opportunity.kind is OpportunityKind.CROP_FARM
    assert opportunity.location == (5, 64, 5)


def test_wood_role_selects_forestry_only_after_safe_bootstrap():
    scheduler = AdaptiveScheduler(SimpleNamespace(), SimpleNamespace(), _state())
    signals = _signals()

    assert scheduler.select_local_opportunity(
        signals,
        [Phase.SPAWN_BOOTSTRAP],
        now=1000.0,
        role=FleetRole.WOOD_SUPPLY,
    ) is None
    opportunity = scheduler.select_local_opportunity(
        signals,
        [Phase.SPAWN_BOOTSTRAP, Phase.BOOT_SEQUENCE],
        now=1000.0,
        role=FleetRole.WOOD_SUPPLY,
    )

    assert opportunity is not None
    assert opportunity.kind is OpportunityKind.WOOD_FARM


def test_iron_role_selects_bounded_mining_after_boot_even_at_night():
    scheduler = AdaptiveScheduler(SimpleNamespace(), SimpleNamespace(), _state())
    signals = _signals(world_time=18000)

    assert scheduler.select_local_opportunity(
        signals,
        [Phase.SPAWN_BOOTSTRAP],
        now=1000.0,
        role=FleetRole.IRON_SUPPLY,
    ) is None
    opportunity = scheduler.select_local_opportunity(
        signals,
        [Phase.SPAWN_BOOTSTRAP, Phase.BOOT_SEQUENCE],
        now=1000.0,
        role=FleetRole.IRON_SUPPLY,
    )

    assert opportunity is not None
    assert opportunity.kind is OpportunityKind.IRON_MINE


def test_local_farming_is_skipped_when_hostile_or_before_boot():
    scheduler = AdaptiveScheduler(SimpleNamespace(), SimpleNamespace(), _state())
    signals = _signals(
        nearby_hostiles=1,
        adult_animals={"cow": 2},
        inventory={"minecraft:wheat": 2},
    )

    assert scheduler.select_local_opportunity(
        signals, [Phase.SPAWN_BOOTSTRAP], now=1000.0
    ) is None
    assert scheduler.select_local_opportunity(
        _signals(
            adult_animals={"cow": 2},
            inventory={"minecraft:wheat": 2},
        ),
        [],
        now=1000.0,
    ) is None
    assert scheduler.select_local_opportunity(
        _signals(
            world_time=13000,
            adult_animals={"cow": 2},
            inventory={"minecraft:wheat": 2},
        ),
        [Phase.SPAWN_BOOTSTRAP],
        now=1000.0,
    ) is None


def test_opportunity_cooldown_prevents_repeating_same_failed_work():
    state = _state(
        {
            "adaptive_scheduler": {
                "opportunities": {
                    "animal_farm": {"last_attempt": 900.0, "success": False}
                }
            }
        }
    )
    scheduler = AdaptiveScheduler(SimpleNamespace(), SimpleNamespace(), state)

    assert scheduler.select_local_opportunity(
        _signals(
            adult_animals={"cow": 2},
            inventory={"minecraft:wheat": 2},
        ),
        [Phase.SPAWN_BOOTSTRAP],
        now=1000.0,
    ) is None


def test_distant_checkpointed_farm_does_not_cause_side_trip():
    scheduler = AdaptiveScheduler(SimpleNamespace(), SimpleNamespace(), _state())

    assert scheduler.select_local_opportunity(
        _signals(
            known_farm_location=(200, 64, 0),
            inventory={"minecraft:wheat_seeds": 4},
        ),
        [Phase.SPAWN_BOOTSTRAP],
        now=1000.0,
    ) is None


def test_collect_game_signals_reads_entities_inventory_and_crop(monkeypatch):
    class Transport:
        def dispatch(self, route, payload=None, **_kwargs):
            if route == "get_state":
                return {
                    "health": 20,
                    "food_level": 18,
                    "experience_level": 27,
                    "dimension": "minecraft:overworld",
                    "block_position": {"x": 10, "y": 64, "z": 10},
                }
            if route == "get_entities":
                return {
                    "entities": [
                        {"type": "minecraft:cow", "is_baby": False},
                        {"type": "minecraft:cow", "is_baby": False},
                        {"type": "minecraft:villager", "is_baby": False},
                    ]
                }
            if route == "get_block":
                return {"id": "minecraft:wheat"}
            return {}

    client = SimpleNamespace(transport=Transport())
    resources = SimpleNamespace(
        refresh_inventory=lambda: {"minecraft:wheat": 3}
    )
    monkeypatch.setattr(
        adaptive,
        "find_nearby_block",
        lambda *_args, **_kwargs: (12, 64, 12),
    )

    signals = collect_game_signals(client, resources, _state())

    assert signals.observed
    assert signals.position == (10, 64, 10)
    assert signals.adult_animals == {"cow": 2}
    assert signals.adult_villagers == 1
    assert signals.crop_location == (12, 64, 12)
    assert signals.experience_level == 27


def test_animal_opportunity_records_verified_herd_growth(monkeypatch):
    state = _state()
    scheduler = AdaptiveScheduler(SimpleNamespace(), SimpleNamespace(), state)
    observations = iter(
        [
            _signals(adult_animals={"cow": 2}),
            _signals(adult_animals={"cow": 3}),
        ]
    )
    monkeypatch.setattr(scheduler, "observe", lambda: next(observations))
    monkeypatch.setattr(adaptive, "breed_pair", lambda *_args, **_kwargs: True)
    opportunity = LocalOpportunity(
        OpportunityKind.ANIMAL_FARM,
        100,
        "test herd",
        animal_type="cow",
    )

    result = scheduler.run_local_opportunity(opportunity)

    assert result.success
    assert result.before == 2
    assert result.after == 3
    record = state.custom_data["adaptive_scheduler"]["opportunities"][
        "animal_farm"
    ]
    assert record["success"] is True


def test_crop_opportunity_verifies_produce_increase(monkeypatch):
    calls = []

    class Transport:
        def dispatch(self, route, payload=None):
            calls.append((route, payload or {}))
            return {}

    client = SimpleNamespace(transport=Transport())
    scheduler = AdaptiveScheduler(client, SimpleNamespace(), _state())
    inventories = iter(
        [
            {"minecraft:wheat": 0, "minecraft:wheat_seeds": 4},
            {"minecraft:wheat": 3, "minecraft:wheat_seeds": 4},
        ]
    )
    monkeypatch.setattr(adaptive, "get_inventory", lambda _client: next(inventories))
    monkeypatch.setattr(adaptive, "goto", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(adaptive.time, "sleep", lambda _seconds: None)
    opportunity = LocalOpportunity(
        OpportunityKind.CROP_FARM,
        80,
        "test crops",
        location=(5, 64, 5),
    )

    result = scheduler.run_local_opportunity(opportunity, crop_timeout=1.0)

    assert result.success
    assert result.detail == "crop produce increased"
    assert (
        "farm",
        {
            "range": 8,
            "x": 5,
            "y": 64,
            "z": 5,
            "replant": True,
        },
    ) in calls
    assert ("cancel", {}) in calls


def test_wood_opportunity_records_banked_log_progress(monkeypatch):
    state = _state()
    scheduler = AdaptiveScheduler(SimpleNamespace(), SimpleNamespace(), state)
    monkeypatch.setattr(
        adaptive,
        "run_wood_cycle",
        lambda *_args, **_kwargs: WoodCycleResult(
            True,
            "harvested 24 logs, planted 5 saplings, banked 24 logs",
            logs_harvested=24,
            saplings_planted=5,
            logs_banked=24,
            total_logs_banked=24,
        ),
    )
    opportunity = LocalOpportunity(
        OpportunityKind.WOOD_FARM,
        200,
        "test forestry",
    )

    result = scheduler.run_local_opportunity(opportunity)

    assert result.success
    assert result.before == 0
    assert result.after == 24
    assert "planted 5 saplings" in result.detail


def test_iron_opportunity_records_banked_team_supply(monkeypatch):
    state = _state()
    scheduler = AdaptiveScheduler(SimpleNamespace(), SimpleNamespace(), state)
    monkeypatch.setattr(
        adaptive,
        "run_scheduled_iron_cycle",
        lambda *_args, **_kwargs: (
            True,
            "mined 24 raw iron, smelted 24 ingots, banked 24",
            0,
            24,
        ),
    )
    opportunity = LocalOpportunity(
        OpportunityKind.IRON_MINE,
        200,
        "test iron mining",
    )

    result = scheduler.run_local_opportunity(opportunity)

    assert result.success
    assert result.before == 0
    assert result.after == 24
    assert "smelted 24 ingots" in result.detail


def test_recorded_phase_decision_is_explainable():
    state = _state()
    scheduler = AdaptiveScheduler(SimpleNamespace(), SimpleNamespace(), state)
    objective = _post_food_planner()._by_phase[Phase.ENCHANTING_PIPELINE]
    signals = _signals(
        adult_animals={"cow": 2},
        inventory={"minecraft:wheat": 2},
    )

    scheduler.record_decision(objective, signals)

    decision = state.custom_data["adaptive_scheduler"]["last_decision"]
    assert decision["phase"] == "ENCHANTING_PIPELINE"
    assert decision["score"] > 10
    assert any("leather" in reason for reason in decision["signals"])


def test_next_step_runs_local_work_before_selecting_a_long_phase(monkeypatch):
    scheduler = AdaptiveScheduler(SimpleNamespace(), SimpleNamespace(), _state())
    opportunity = LocalOpportunity(
        OpportunityKind.ANIMAL_FARM,
        100,
        "local renewable herd",
        animal_type="cow",
    )
    result = OpportunityResult(
        opportunity,
        True,
        "new offspring observed",
        2,
        3,
    )
    monkeypatch.setattr(scheduler, "observe", lambda: _signals())
    monkeypatch.setattr(
        scheduler,
        "select_local_opportunity",
        lambda *_args, **_kwargs: opportunity,
    )
    monkeypatch.setattr(
        scheduler,
        "run_local_opportunity",
        lambda *_args, **_kwargs: result,
    )

    decision = scheduler.next_step(_post_food_planner())

    assert decision.local_work
    assert decision.objective is None
    assert "animal_farm verified" in decision.summary
