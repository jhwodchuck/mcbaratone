"""Persistent campaign strategy and home-commitment regressions."""

from types import SimpleNamespace

from baritone_client.automator.state_manager import Phase
from baritone_client.automator.strategic_state import StrategicPhase, StrategicState


def _state(custom=None, phase=Phase.BRIDGE_CHECK):
    state = SimpleNamespace(custom_data=custom or {})
    state.get_current_phase = lambda: phase
    return state


def test_legacy_checkpoint_is_normalized_into_inspectable_strategy():
    state = _state({
        "homestead_anchor": [-433, 78, 0],
        "stronghold_coords": [1200, -900],
        "locations": {
            "spawn": [{"x": 0, "y": 70, "z": 0, "dimension": "minecraft:overworld"}],
            "village": [{"x": 100, "y": 64, "z": 30, "dimension": "minecraft:overworld"}],
            "nether_portal": [{"x": -420, "y": 79, "z": -10, "dimension": "minecraft:overworld"}],
        },
        "structures": {"wheat_farm": {"location": [-430, 78, 5], "verified": True}},
        "milestones": {"nether_entered": True},
        "end_readiness": {"missing": ["6 blaze rods", "12 pearl/Eye components"]},
    }, phase=Phase.NETHER_AND_BLAZE)

    data = StrategicState(state).refresh(current=Phase.NETHER_AND_BLAZE)

    assert data["phase"] == StrategicPhase.PROGRESS_TECHNOLOGY.name
    assert data["spawn_location"]["position"] == [0, 70, 0]
    assert data["permanent_home"]["position"] == [-433, 78, 0]
    assert data["permanent_home"]["status"] == "legacy_unscored"
    assert data["known_villages"][0]["position"] == [100, 64, 30]
    assert data["portals"][0]["kind"] == "nether_portal"
    assert data["stronghold"]["position"] == [1200, None, -900]
    assert data["major_infrastructure"][0]["kind"] == "wheat_farm"
    assert data["blocking_condition"] == "6 blaze rods, 12 pearl/Eye components"


def test_strategic_phase_does_not_regress_when_survival_recovery_runs():
    state = _state({}, phase=Phase.WORLD_UNLOCK)
    strategy = StrategicState(state)
    strategy.refresh(current=Phase.WORLD_UNLOCK, completed={Phase.NETHER_AND_BLAZE})
    strategy.refresh(current=Phase.BOOT_SEQUENCE)

    assert strategy.data["phase"] == StrategicPhase.PREPARE_FOR_END.name
    assert [entry["to"] for entry in strategy.data["phase_history"]] == [
        "ESTABLISH_HOME", "BUILD_INFRASTRUCTURE", "PROGRESS_TECHNOLOGY",
        "PREPARE_FOR_END",
    ]


def test_survival_override_preserves_long_term_progress_and_resumes():
    state = _state({}, phase=Phase.WORLD_UNLOCK)
    strategy = StrategicState(state)
    strategy.refresh(current=Phase.WORLD_UNLOCK, completed={Phase.NETHER_AND_BLAZE})

    assert strategy.suspend_for_survival("health=5.2, food=7")
    assert strategy.data["phase"] == StrategicPhase.PREPARE_FOR_END.name
    assert strategy.data["active_phase"] == StrategicPhase.SURVIVE.name
    assert strategy.data["survival_override"]["resume_phase"] == "PREPARE_FOR_END"
    assert "Restore health" in strategy.data["current_major_objective"]
    assert not strategy.suspend_for_survival("health=5.0, food=6")

    assert strategy.resume_from_survival()
    assert strategy.data["phase"] == StrategicPhase.PREPARE_FOR_END.name
    assert strategy.data["active_phase"] == StrategicPhase.PREPARE_FOR_END.name
    assert strategy.data["survival_override"] is None



def test_survival_resume_restores_the_long_term_blocker():
    state = _state({}, phase=Phase.NETHER_AND_BLAZE)
    strategy = StrategicState(state)
    strategy.data["blocking_condition"] = "6 blaze rods, prepared_food_32"

    assert strategy.suspend_for_survival("player death requires recovery")
    assert strategy.data["blocking_condition"] == "player death requires recovery"
    assert not strategy.suspend_for_survival("health=5.0, food=6")

    assert strategy.resume_from_survival()
    assert strategy.data["blocking_condition"] == "6 blaze rods, prepared_food_32"


def test_survival_resume_of_a_legacy_override_clears_the_blocker():
    state = _state({}, phase=Phase.NETHER_AND_BLAZE)
    strategy = StrategicState(state)
    strategy.data["survival_override"] = {"resume_phase": "PROGRESS_TECHNOLOGY"}
    strategy.data["blocking_condition"] = "player death requires recovery"

    assert strategy.resume_from_survival()
    assert strategy.data["blocking_condition"] is None

def test_refresh_rebinds_after_checkpoint_load_replaces_custom_data():
    state = _state({})
    strategy = StrategicState(state)
    state.custom_data = {"homestead_anchor": [9, 70, 12]}

    strategy.refresh(current=Phase.BOOT_SEQUENCE)

    assert state.custom_data["strategic_state"] is strategy.data
    assert strategy.data["permanent_home"]["position"] == [9, 70, 12]


def test_home_commitment_requires_margin_and_preserves_previous_base():
    strategy = StrategicState(_state())

    first = strategy.consider_home(
        {"position": [0, 64, 0], "dimension": "minecraft:overworld", "score": 80,
         "eligible": True, "factors": {"water": True}},
        trigger="initial selection",
    )
    marginal = strategy.consider_home(
        {"position": [100, 64, 0], "dimension": "minecraft:overworld", "score": 90,
         "eligible": True, "factors": {"village": True}},
        trigger="exploration",
    )
    better = strategy.consider_home(
        {"position": [200, 64, 0], "dimension": "minecraft:overworld", "score": 96,
         "eligible": True, "factors": {"village": True, "river": True}},
        trigger="exploration",
    )

    assert first["decision"] == "relocate"
    assert marginal["decision"] == "remain"
    assert better["decision"] == "relocate"
    assert strategy.data["permanent_home"]["position"] == [200, 64, 0]
    assert strategy.data["previous_bases"][0]["position"] == [0, 64, 0]
    assert len(strategy.data["candidate_homes"]) == 3


def test_compelling_trigger_cannot_commit_a_low_quality_candidate():
    strategy = StrategicState(_state({
        "strategic_state": {
            "permanent_home": {"position": [0, 64, 0], "score": 75},
        }
    }))

    decision = strategy.consider_home(
        {"position": [50, 120, 50], "score": 54, "eligible": True, "factors": {}},
        trigger="anchor unreachable",
        compelling=True,
    )

    assert decision["decision"] == "remain"
    assert strategy.data["permanent_home"]["position"] == [0, 64, 0]
