from types import SimpleNamespace
from unittest.mock import MagicMock

from baritone_client.automator import EndGameAutomator
from baritone_client.automator.objective import ObjectivePlanner, default_objectives
from baritone_client.automator.state_manager import Phase
from baritone_client.actions.suites import PlaceholderAction
from baritone_client.actions.boot_sequence import FoodCookingAction, IronSmeltingAction
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


def test_food_cooking_action_verifies_inventory_delta(monkeypatch):
    counts = {
        "minecraft:raw_beef": 3,
        "minecraft:cooked_beef": 0,
        "minecraft:raw_porkchop": 1,
        "minecraft:cooked_porkchop": 0,
        "minecraft:coal": 8,
    }

    def fake_count_item(_client, item_id):
        return int(counts.get(item_id, 0))

    def fake_smelt(_client, _furnace_pos, input_id, _fuel_id, output_id, output_count, **_kwargs):
        available = counts.get(input_id, 0)
        cooked = min(output_count, available)
        counts[input_id] = max(0, available - cooked)
        counts[output_id] = counts.get(output_id, 0) + cooked
        return True

    monkeypatch.setattr(boot_sequence, "count_item", fake_count_item)
    monkeypatch.setattr(boot_sequence, "find_nearby_block", lambda *_args, **_kwargs: [1, 2, 3])
    monkeypatch.setattr(boot_sequence, "ensure_supplies", lambda *_args, **_kwargs: type("T", (), {"success": True})())
    monkeypatch.setattr(boot_sequence, "open_furnace", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(boot_sequence, "smelt_in_furnace", fake_smelt)

    result = FoodCookingAction().execute(make_context())
    assert result.success is True
    assert result.data["raw_delta"] == 4
    assert result.data["cooked_delta"] == 4


def test_food_cooking_action_fails_without_raw_food(monkeypatch):
    monkeypatch.setattr(boot_sequence, "count_item", lambda *_args, **_kwargs: 0)
    assert not FoodCookingAction().execute(make_context()).success


def test_iron_smelt_action_verifies_delta(monkeypatch):
    counts = {"minecraft:raw_iron": 6, "minecraft:iron_ingot": 0}

    def fake_count_item(_client, item_id):
        return int(counts.get(item_id, 0))

    def fake_smelt_requirement(_client, item_id, shortfall):
        if item_id != "minecraft:iron_ingot":
            return False
        smelted = min(shortfall, counts["minecraft:raw_iron"])
        counts["minecraft:raw_iron"] -= smelted
        counts["minecraft:iron_ingot"] += smelted
        return True

    monkeypatch.setattr(boot_sequence, "count_item", fake_count_item)
    monkeypatch.setattr(
        boot_sequence,
        "_smelt_requirement_shortfall",
        fake_smelt_requirement,
    )

    result = IronSmeltingAction().execute(make_context())
    assert result.success is True
    assert result.data["raw_delta"] == 6
    assert result.data["ingot_delta"] == 6


def test_iron_smelting_action_fails_when_smelt_helper_fails(monkeypatch):
    counts = {"minecraft:raw_iron": 3, "minecraft:iron_ingot": 0}
    monkeypatch.setattr(boot_sequence, "count_item", lambda *_args, **_kwargs: counts.get(_args[1], 0))
    monkeypatch.setattr(boot_sequence, "_smelt_requirement_shortfall", lambda *_args, **_kwargs: False)
    result = IronSmeltingAction().execute(make_context())
    assert result.success is False
    assert result.message == "Iron smelting helper failed"
