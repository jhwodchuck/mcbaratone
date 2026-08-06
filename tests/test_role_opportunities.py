from types import SimpleNamespace

from baritone_client.automator.adaptive_scheduler import (
    AdaptiveScheduler,
    GameSignals,
    OpportunityResult,
)
from baritone_client.automator.common import role_opportunities
from baritone_client.automator.end_readiness import FleetRole
from baritone_client.automator.local_opportunity import LocalOpportunity, OpportunityKind


def _signals(**overrides):
    values = dict(observed=True, entities_observed=True, dimension="minecraft:the_nether", health=20.0, food=20, nearby_hostiles=0, inventory={})
    values.update(overrides)
    return GameSignals(**values)


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
