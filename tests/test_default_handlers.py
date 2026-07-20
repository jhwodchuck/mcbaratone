import inspect
from types import SimpleNamespace
from unittest.mock import MagicMock
from unittest.mock import patch

from baritone_client.automator import EndGameAutomator
from baritone_client.actions import CraftingAction
from baritone_client.automator.phases import InitialGatheringHandler
from baritone_client.core.interfaces import ActionContext
from baritone_client.automator.state_manager import Phase
from baritone_client.common.tasks import TaskResult
from baritone_client.common.resources import gather_stone
from baritone_client.automator.phases import initial_gathering
from baritone_client.common import inventory as common_inventory


def test_default_handlers_cover_every_runnable_phase():
    automator = EndGameAutomator(MagicMock())

    automator.register_default_handlers()

    missing = [
        phase.name
        for phase in Phase
        if phase is not Phase.COMPLETE and not automator.executor.has_handler(phase)
    ]
    assert missing == []


def test_initial_gathering_can_initialize_its_actions():
    client = MagicMock()
    resources = MagicMock()
    resources.phase_ready_result.return_value = TaskResult.ok("already ready")

    result = InitialGatheringHandler().execute(client, resources, MagicMock())

    assert result.success
    client.mission.macro.assert_not_called()


def test_crafting_table_open_is_verified():
    action = CraftingAction()
    action.run_command = MagicMock(side_effect=[
        {},
        {"block_position": {"x": 2, "y": 64, "z": 3}},
        {},
        {},
        {"has_gui": True},
    ])

    assert action._open_crafting_table(
        ActionContext(client=MagicMock(), state=MagicMock()), 1, 64, 3
    )


def test_crafting_table_placement_avoids_baritone_builder():
    source = inspect.getsource(CraftingAction.ensure_crafting_table)

    assert "break_block" not in source
    assert "#place" not in source


def test_gather_stone_does_not_restart_mining_when_target_is_met():
    client = MagicMock()

    with patch(
        "baritone_client.common.resources.count_item",
        side_effect=lambda _client, item_id: 72 if item_id == "minecraft:cobblestone" else 0,
    ):
        assert gather_stone(client, count=64)

    client.transport.dispatch.assert_not_called()


def test_missing_bed_materials_do_not_block_progression():
    handler = InitialGatheringHandler()
    handler.inventory = MagicMock()
    handler.inventory.count_item.return_value = 0
    handler.context = ActionContext(client=MagicMock(), state=MagicMock())

    assert handler._craft_bed_optional(MagicMock())


def test_initial_core_goals_require_verified_storage_deposit(monkeypatch):
    counts = {
        "minecraft:oak_log": 8,
        "minecraft:cobblestone": 64,
        "minecraft:stone_pickaxe": 1,
        "minecraft:stone_axe": 1,
    }
    monkeypatch.setattr(
        initial_gathering,
        "count_item",
        lambda _client, item_id: counts.get(item_id, 0),
    )
    monkeypatch.setattr(
        common_inventory,
        "resolve_storage_location",
        lambda *_args, **_kwargs: (10, 64, 20),
    )

    handler = InitialGatheringHandler()
    handler.state = SimpleNamespace()
    handler._storage_deposit_verified = False

    assert not handler._core_goals_met(MagicMock())

    handler._storage_deposit_verified = True
    assert handler._core_goals_met(MagicMock())


def test_storage_setup_does_not_report_missing_table_as_success(monkeypatch):
    monkeypatch.setattr(
        common_inventory,
        "resolve_storage_location",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        common_inventory,
        "count_item",
        lambda _client, item_id: (
            8 if item_id == "minecraft:oak_planks" else 0
        ),
    )

    handler = InitialGatheringHandler()
    handler.state = SimpleNamespace()
    handler._ensure_crafting_table = lambda _client: False

    assert not handler._setup_storage(MagicMock())


def test_storage_setup_requests_eight_planks_for_chest(monkeypatch):
    counts = {"minecraft:oak_log": 8, "minecraft:oak_planks": 4}
    craft_calls = []

    monkeypatch.setattr(
        common_inventory,
        "resolve_storage_location",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        common_inventory,
        "count_item",
        lambda _client, item_id: counts.get(item_id, 0),
    )

    def fake_craft(_client, item_id, count):
        craft_calls.append((item_id, count))
        if item_id == "minecraft:oak_planks":
            counts[item_id] = 8
            return True
        return False

    monkeypatch.setattr(common_inventory, "craft", fake_craft)

    handler = InitialGatheringHandler()
    handler.state = SimpleNamespace()
    handler._ensure_crafting_table = lambda _client: False
    client = MagicMock()

    assert not handler._setup_storage(client)
    assert ("minecraft:oak_planks", 8) in craft_calls


def test_initial_gathering_refuses_work_while_critically_wounded(monkeypatch):
    client = MagicMock()
    resources = MagicMock()
    resources.phase_ready_result.return_value = None
    monkeypatch.setattr(initial_gathering, "recover_health", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        initial_gathering,
        "acquire_emergency_food",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        initial_gathering,
        "sleep_through_night",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("unsafe player must not start the phase")
        ),
    )

    result = InitialGatheringHandler().execute(
        client, resources, SimpleNamespace()
    )

    assert not result.success
    assert "health recovery" in result.reason
    client.transport.dispatch.assert_any_call("close_screen", {})
