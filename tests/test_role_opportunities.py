from types import SimpleNamespace

from baritone_client.automator.adaptive_scheduler import (
    AdaptiveScheduler,
    GameSignals,
    OpportunityResult,
)
from baritone_client.automator.common import role_opportunities
from baritone_client.automator.end_readiness import FleetRole
from baritone_client.automator.local_opportunity import LocalOpportunity, OpportunityKind
from baritone_client.automator.state_manager import Phase


def _signals(**overrides):
    values = dict(observed=True, entities_observed=True, dimension="minecraft:the_nether", health=20.0, food=20, nearby_hostiles=0, inventory={})
    values.update(overrides)
    return GameSignals(**values)


def test_peaceful_hostile_roles_choose_renewable_work_or_leave_nether(monkeypatch):
    end_work = role_opportunities.select_role_opportunity(
        FleetRole.END_RUNNER,
        _signals(dimension="minecraft:overworld", difficulty="peaceful"),
        SimpleNamespace(custom_data={}),
        cooldown_ready=True,
    )
    assert end_work and end_work.kind is OpportunityKind.WOOD_FARM

    state = SimpleNamespace(custom_data={"nether_portal": [4, 70, 5]})
    nether_exit = role_opportunities.select_role_opportunity(
        FleetRole.NETHER_SUPPLY,
        _signals(difficulty="peaceful"),
        state,
        cooldown_ready=True,
    )
    assert nether_exit and nether_exit.target_item == "minecraft:overworld"
    calls = []
    monkeypatch.setattr(
        role_opportunities,
        "enter_nether_portal",
        lambda *_args, **kwargs: calls.append(kwargs) or True,
    )
    role_opportunities.run_role_opportunity(
        SimpleNamespace(), state, nether_exit
    )
    assert calls[0]["target_dimension"] == "minecraft:overworld"


def test_peaceful_nether_exit_uses_portal_from_current_dimension():
    state = SimpleNamespace(
        custom_data={
            "locations": {
                "nether_portal": [
                    {
                        "x": -156,
                        "y": 64,
                        "z": -278,
                        "dimension": "minecraft:overworld",
                    },
                    {
                        "x": -19,
                        "y": 85,
                        "z": -35,
                        "dimension": "minecraft:the_nether",
                    },
                ]
            }
        }
    )

    chosen = role_opportunities.select_role_opportunity(
        FleetRole.NETHER_SUPPLY,
        _signals(difficulty="peaceful"),
        state,
        cooldown_ready=True,
    )

    assert chosen and chosen.location == (-19, 85, -35)


def test_end_runner_requires_end_city_checkpoint_and_safe_end():
    state = SimpleNamespace(custom_data={"end_city": {"location": [1, 70, 1]}})
    chosen = role_opportunities.select_role_opportunity(
        FleetRole.END_RUNNER, _signals(dimension="minecraft:the_end"), state, cooldown_ready=True
    )
    assert chosen is not None
    assert chosen.kind is OpportunityKind.END_SUPPLY
    assert chosen.target_item == "minecraft:elytra"
    assert role_opportunities.select_role_opportunity(
        FleetRole.END_RUNNER, _signals(dimension="minecraft:the_end", health=8), state, cooldown_ready=True
    ) is None


def test_enchanting_uses_persisted_verified_xp_engine_and_only_xp_delta(monkeypatch):
    state = SimpleNamespace(
        custom_data={
            "structures": {
                "xp_engine": {
                    "location": [4, 30, 5],
                    "source": "minecraft:spawner",
                    "verified": True,
                }
            },
            "locations": {"xp_engine": [{"x": 9, "y": 31, "z": 6, "tags": ["verified"]}]},
        },
        phase_payloads={"XP_ENGINE": {"farm": {"location": [8, 32, 7], "verified": True}}},
    )
    chosen = role_opportunities.select_role_opportunity(
        FleetRole.ENCHANTING, _signals(dimension="minecraft:overworld"), state, cooldown_ready=True
    )
    assert chosen is not None
    assert chosen.location == (4, 30, 5)
    monkeypatch.setattr(role_opportunities, "grind_xp_at_location", lambda *_args, **_kwargs: {"xp_gained": 9, "start_level": 21, "achieved_level": 22})
    success, _detail, before, after = role_opportunities.run_role_opportunity(SimpleNamespace(), state, chosen)
    assert success is True
    assert (before, after) == (21, 22)


def test_enchanting_without_xp_engine_acquires_bounded_book_delta(monkeypatch):
    state = SimpleNamespace(custom_data={})
    chosen = role_opportunities.select_role_opportunity(
        FleetRole.ENCHANTING,
        _signals(dimension="minecraft:overworld", inventory={}),
        state,
        cooldown_ready=True,
    )
    assert chosen and chosen.kind is OpportunityKind.ENCHANTING_MATERIAL
    counts = iter((2, 3))
    monkeypatch.setattr(role_opportunities, "count_item", lambda *_args: next(counts))
    requested = []
    monkeypatch.setattr(
        role_opportunities,
        "ensure_supplies",
        lambda _client, supplies, **_kwargs: requested.append(supplies),
    )
    assert role_opportunities.run_role_opportunity(SimpleNamespace(), state, chosen)[0] is True
    assert requested == [{"minecraft:book": 3}]


def test_peaceful_enchanter_without_book_inputs_banks_renewable_wood():
    chosen = role_opportunities.select_role_opportunity(
        FleetRole.ENCHANTING,
        _signals(
            dimension="minecraft:overworld",
            difficulty="peaceful",
            inventory={"minecraft:oak_log": 2},
        ),
        SimpleNamespace(custom_data={}),
        cooldown_ready=True,
    )

    assert chosen and chosen.kind is OpportunityKind.WOOD_FARM


def test_enchanting_material_uses_its_own_cooldown_in_overworld():
    state = SimpleNamespace(
        custom_data={
            "adaptive_scheduler": {
                "opportunities": {"enchanting_material": {"last_attempt": 1000.0}}
            }
        }
    )
    scheduler = AdaptiveScheduler(SimpleNamespace(), SimpleNamespace(), state)
    signals = _signals(
        dimension="minecraft:overworld",
        inventory={"minecraft:paper": 3, "minecraft:leather": 1},
    )
    assert scheduler.select_local_opportunity(
        signals, [Phase.SPAWN_BOOTSTRAP], now=1100, role=FleetRole.ENCHANTING
    ) is None
    chosen = scheduler.select_local_opportunity(
        signals, [Phase.SPAWN_BOOTSTRAP], now=1181, role=FleetRole.ENCHANTING
    )
    assert chosen and chosen.kind is OpportunityKind.ENCHANTING_MATERIAL


def test_overworld_roles_select_checkpoint_backed_dimension_entry(monkeypatch):
    state = SimpleNamespace(
        custom_data={
            "locations": {"nether_portal": [{"x": 1, "y": 64, "z": 2}]},
            "end_portal": [3, 25, 4],
            "end_city": [900, 70, 900],
        }
    )
    nether = role_opportunities.select_role_opportunity(
        FleetRole.NETHER_SUPPLY, _signals(dimension="minecraft:overworld"), state, cooldown_ready=True
    )
    end = role_opportunities.select_role_opportunity(
        FleetRole.END_RUNNER, _signals(dimension="minecraft:overworld"), state, cooldown_ready=True
    )
    assert nether and nether.kind is OpportunityKind.DIMENSION_ENTRY
    assert nether.location == (1, 64, 2)
    assert end and end.kind is OpportunityKind.DIMENSION_ENTRY
    assert end.location == (3, 25, 4)
    monkeypatch.setattr(role_opportunities, "enter_nether_portal", lambda *_args, **_kwargs: True)
    success, detail, before, after = role_opportunities.run_role_opportunity(
        SimpleNamespace(), state, nether
    )
    assert success is False
    assert "awaiting productive cycle" in detail
    assert (before, after) == (0, 0)


def test_nether_cycle_does_not_count_movement_without_rod_delta(monkeypatch):
    opportunity = LocalOpportunity(OpportunityKind.NETHER_SUPPLY, 1, "test", target_item="minecraft:blaze_rod")
    counts = iter((4, 4))
    monkeypatch.setattr(role_opportunities, "count_item", lambda *_args: next(counts))
    monkeypatch.setattr(role_opportunities, "hunt_blazes", lambda *_args, **_kwargs: 4)
    success, detail, before, after = role_opportunities.run_role_opportunity(SimpleNamespace(), SimpleNamespace(), opportunity)
    assert success is False
    assert "no blaze-rod delta" in detail
    assert (before, after) == (4, 4)


def test_failed_end_supply_requires_a_different_city_frontier(monkeypatch):
    state = SimpleNamespace(custom_data={})
    opportunity = LocalOpportunity(
        OpportunityKind.END_SUPPLY,
        1,
        "test",
        target_item="minecraft:shulker_box",
    )
    counts = iter((5, 5))
    monkeypatch.setattr(role_opportunities, "count_item", lambda *_args: next(counts))
    monkeypatch.setattr(
        role_opportunities,
        "acquire_shulker_boxes",
        lambda *_args, **_kwargs: False,
    )

    result = role_opportunities.run_role_opportunity(
        SimpleNamespace(), state, opportunity
    )

    assert result[0] is False
    assert state.custom_data["end_worker"]["frontier_required"] is True


def test_end_frontier_replaces_only_a_newly_verified_city(monkeypatch):
    state = SimpleNamespace(
        custom_data={
            "end_city": [1, 70, 1],
            "adaptive_scheduler": {"opportunities": {"end_supply": {"success": False}}},
        }
    )
    chosen = role_opportunities.select_role_opportunity(
        FleetRole.END_RUNNER, _signals(dimension="minecraft:the_end"), state, cooldown_ready=True
    )
    assert chosen and chosen.kind is OpportunityKind.END_FRONTIER
    waypoints = []
    monkeypatch.setattr(
        role_opportunities,
        "goto",
        lambda _client, *coords, **_kwargs: waypoints.append(coords) or True,
    )
    monkeypatch.setattr(role_opportunities, "find_end_city", lambda *_args, **_kwargs: (200, 70, 200))
    success, detail, before, after = role_opportunities.run_role_opportunity(SimpleNamespace(), state, chosen)
    assert success is False
    assert "new End city verified" in detail
    assert (before, after) == (0, 0)
    assert state.custom_data["end_city"]["location"] == [200, 70, 200]
    assert state.custom_data["end_worker"]["frontier_required"] is False
    assert waypoints == [(193, 70, 1)]

    next_cycle = role_opportunities.select_role_opportunity(
        FleetRole.END_RUNNER,
        _signals(dimension="minecraft:the_end", position=(200, 70, 200)),
        state,
        cooldown_ready=True,
    )
    assert next_cycle and next_cycle.kind is OpportunityKind.END_SUPPLY


def test_end_runner_on_central_island_routes_before_supply(monkeypatch):
    state = SimpleNamespace(custom_data={"end_city": [900, 70, 900]})
    chosen = role_opportunities.select_role_opportunity(
        FleetRole.END_RUNNER,
        _signals(dimension="minecraft:the_end", position=(0, 70, 0)),
        state,
        cooldown_ready=True,
    )
    assert chosen and chosen.kind is OpportunityKind.END_CITY_ROUTE
    assert chosen.target_item == "gateway_then_city"
    monkeypatch.setattr(role_opportunities, "traverse_end_gateway", lambda *_args, **_kwargs: (700, 70, 700))
    monkeypatch.setattr(role_opportunities, "goto", lambda *_args, **_kwargs: True)
    success, detail, before, after = role_opportunities.run_role_opportunity(SimpleNamespace(), state, chosen)
    assert success is False
    assert "awaiting productive cycle" in detail
    assert (before, after) == (0, 0)


def test_end_runner_already_on_outer_islands_does_not_take_return_gateway(monkeypatch):
    state = SimpleNamespace(custom_data={"end_city": [1200, 70, 1200]})
    chosen = role_opportunities.select_role_opportunity(
        FleetRole.END_RUNNER,
        _signals(dimension="minecraft:the_end", position=(900, 70, 900)),
        state,
        cooldown_ready=True,
    )
    assert chosen and chosen.kind is OpportunityKind.END_CITY_ROUTE
    assert chosen.target_item == "city"
    gateway = []
    monkeypatch.setattr(
        role_opportunities,
        "traverse_end_gateway",
        lambda *_args, **_kwargs: gateway.append(True),
    )
    monkeypatch.setattr(role_opportunities, "goto", lambda *_args, **_kwargs: True)

    result = role_opportunities.run_role_opportunity(
        SimpleNamespace(), state, chosen
    )

    assert result[0] is False
    assert gateway == []


def test_durable_role_counter_accumulates_only_verified_deltas():
    state = SimpleNamespace(custom_data={})
    scheduler = AdaptiveScheduler(SimpleNamespace(), SimpleNamespace(), state)
    opportunity = LocalOpportunity(
        OpportunityKind.NETHER_SUPPLY, 1, "test", target_item="minecraft:blaze_rod"
    )
    scheduler._record_opportunity_result(
        OpportunityResult(opportunity, False, "moved only", 4, 4)
    )
    scheduler._record_opportunity_result(
        OpportunityResult(opportunity, True, "rods increased", 4, 6)
    )
    stored = state.custom_data["adaptive_scheduler"]["opportunities"]["nether_supply"]
    assert stored["attempts"] == 2
    assert stored["successful_cycles"] == 1
    assert stored["verified_delta_total"] == 2
