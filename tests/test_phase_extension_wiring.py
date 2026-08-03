from types import SimpleNamespace
from unittest.mock import MagicMock

from baritone_client.automator import EndGameAutomator
from baritone_client.automator.objective import ObjectivePlanner, default_objectives
from baritone_client.automator.state_manager import Phase, StateManager
from baritone_client.automator.phases import city_building, terraforming
from baritone_client.actions.suites import (
    CompleteRunAction,
    IronAgeAction,
    NetherJourneyAction,
    PlaceholderAction,
    StrongholdAction,
)
from baritone_client.actions.boot_sequence import FoodCookingAction, IronSmeltingAction
from baritone_client.common.tasks import TaskResult
from baritone_client.core.interfaces import ActionContext
import baritone_client.actions.boot_sequence as boot_sequence


def make_context():
    return ActionContext(client=SimpleNamespace(transport=MagicMock()), state=SimpleNamespace())


def test_default_handlers_register_terraform_and_city():
    automator = EndGameAutomator(MagicMock())
    automator.register_default_handlers()

    missing = [
        phase.name
        for phase in Phase
        if phase is not Phase.COMPLETE and not automator.executor.has_handler(phase)
    ]
    assert missing == []


def test_objective_graph_wires_terraform_city_after_megabase():
    planner = ObjectivePlanner(default_objectives())
    order = [obj.phase for obj in planner.objectives]
    assert Phase.TERRAFORM in order
    assert Phase.CITY_BUILD in order
    assert order.index(Phase.TERRAFORM) > order.index(Phase.MEGABASE_INIT)
    assert order.index(Phase.CITY_BUILD) > order.index(Phase.TERRAFORM)
    assert planner._by_phase[Phase.CITY_BUILD].terminal is True
    assert not planner._by_phase[Phase.MEGABASE_INIT].terminal


def test_placeholder_actions_fail_explicitly():
    result = PlaceholderAction("NotYetImplemented").execute(make_context())
    assert result.success is False
    assert "not implemented" in result.message


def test_food_cooking_action_is_concrete_and_no_raw_food_is_a_no_op():
    result = FoodCookingAction().execute(make_context())
    assert result.success is True
    assert "nothing to cook" in result.message.lower()


def test_production_backed_suites_are_concrete_and_t904_is_explicit():
    for suite in (IronAgeAction(), NetherJourneyAction(), StrongholdAction()):
        assert suite.action.actions
    result = CompleteRunAction().execute(make_context())
    assert result.success is False
    assert "EndGameAutomator" in result.message


def test_terraform_handler_persists_verified_payload(monkeypatch, tmp_path):
    state = StateManager(tmp_path)
    state.custom_data["terraform_plan"] = {
        "center": [16, 32],
        "target_y": 70,
        "radius_chunks": 1,
        "preserve": ["beacon"],
    }
    state.custom_data["structures"] = {
        "beacon": {"location": [20, 71, 36], "verified": True}
    }

    def complete(_client, center_x, center_z, target_y, **kwargs):
        assert (center_x, center_z, target_y) == (16, 32, 70)
        kwargs["progress"].update({"next_index": 9, "total": 9})
        assert kwargs["skip_chunks"] == {(16, 32)}
        return TaskResult.ok(
            "done",
            chunks_completed=8,
            chunks_total=8,
            progress_entries_total=9,
            skipped_chunks=[[16, 32]],
            progress_complete=True,
        )

    monkeypatch.setattr(terraforming, "terraform_area", complete)
    result = terraforming.TerraformingHandler().execute(
        SimpleNamespace(), MagicMock(), state
    )
    assert result.success
    payload = state.get_phase_payload(Phase.TERRAFORM)
    assert payload["verified_operations"] is True
    assert payload["chunks_completed"] == payload["chunks_total"] == 8


def test_terraform_handler_propagates_failure_without_payload(monkeypatch, tmp_path):
    state = StateManager(tmp_path)
    state.custom_data["terraform_plan"] = {
        "center": [0, 0],
        "target_y": 64,
        "radius_chunks": 1,
    }
    monkeypatch.setattr(
        terraforming,
        "terraform_area",
        lambda *_args, **_kwargs: TaskResult.fail("chunk failed"),
    )
    result = terraforming.TerraformingHandler().execute(
        SimpleNamespace(), MagicMock(), state
    )
    assert not result.success
    assert state.get_phase_payload(Phase.TERRAFORM) == {}


def test_city_handler_propagates_ring_failure(monkeypatch, tmp_path):
    state = StateManager(tmp_path)
    state.record_phase_payload(
        Phase.TERRAFORM,
        {"center": [0, 0], "target_y": 64, "verified_operations": True},
    )
    monkeypatch.setattr(
        city_building,
        "build_ring",
        lambda *_args, **_kwargs: TaskResult.fail("district failed"),
    )
    result = city_building.CityBuildingHandler().execute(
        SimpleNamespace(), MagicMock(), state
    )
    assert not result.success
    assert state.custom_data["city_progress"].get("ring", 0) == 0
    assert state.get_phase_payload(Phase.CITY_BUILD) == {}


def test_city_handler_persists_only_completed_ring_counts(monkeypatch, tmp_path):
    state = StateManager(tmp_path)
    state.record_phase_payload(
        Phase.TERRAFORM,
        {"center": [4, 8], "target_y": 66, "verified_operations": True},
    )

    def complete_ring(_client, center_x, center_z, target_y, ring, **kwargs):
        assert (center_x, center_z, target_y) == (4, 8, 66)
        count = 1 if ring == 0 else ring * 8
        kwargs["progress"].update({"next_index": count, "total": count})
        return TaskResult.ok(
            "done",
            districts_completed=count,
            districts_total=count,
            progress_complete=True,
        )

    monkeypatch.setattr(city_building, "build_ring", complete_ring)
    result = city_building.CityBuildingHandler(rings=2).execute(
        SimpleNamespace(), MagicMock(), state
    )
    assert result.success
    payload = state.get_phase_payload(Phase.CITY_BUILD)
    assert payload["districts_completed"] == 24
    assert payload["rings_completed"] == 3
    assert state.custom_data["city_progress"]["ring"] == 3


def test_iron_smelt_action_verifies_delta(monkeypatch):
    from baritone_client.common import resources

    counts = {"minecraft:raw_iron": 6, "minecraft:iron_ingot": 0}

    def fake_count_item(_client, item_id):
        return int(counts.get(item_id, 0))

    def fake_smelt(_client, item_id, target, _furnace_pos=None):
        if item_id != "minecraft:iron_ingot":
            return False
        shortfall = max(0, target - counts["minecraft:iron_ingot"])
        smelted = min(shortfall, counts["minecraft:raw_iron"])
        counts["minecraft:raw_iron"] -= smelted
        counts["minecraft:iron_ingot"] += smelted
        return True

    monkeypatch.setattr(boot_sequence, "count_item", fake_count_item)
    monkeypatch.setattr(
        resources,
        "_smelt_with_furnace",
        fake_smelt,
    )

    result = IronSmeltingAction().execute(make_context())
    assert result.success is True
    assert result.data["raw_delta"] == 6
    assert result.data["ingot_delta"] == 6


def test_iron_smelting_action_fails_when_smelt_helper_fails(monkeypatch):
    from baritone_client.common import resources

    counts = {"minecraft:raw_iron": 3, "minecraft:iron_ingot": 0}
    monkeypatch.setattr(boot_sequence, "count_item", lambda *_args, **_kwargs: counts.get(_args[1], 0))
    monkeypatch.setattr(resources, "_smelt_with_furnace", lambda *_args, **_kwargs: False)
    result = IronSmeltingAction().execute(make_context())
    assert result.success is False
    assert result.message == "Iron smelting helper failed"
