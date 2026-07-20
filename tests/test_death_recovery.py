from types import SimpleNamespace

from baritone_client.actions import death_recovery_action
from baritone_client.actions.death_recovery_action import DeathRecoveryAction
from baritone_client.automator import automator as automator_module
from baritone_client.automator.automator import EndGameAutomator
from baritone_client.automator.state_manager import Phase
from baritone_client.core.interfaces import ActionResult


class RecoveryState:
    def __init__(self):
        self.custom_data = {}
        self.phase = Phase.BASE_CONSTRUCTION

    def get_current_phase(self):
        return self.phase


class RecoveryTransport:
    def __init__(self, death_response):
        self.death_response = death_response
        self.calls = []

    def dispatch(self, route, payload):
        self.calls.append((route, payload))
        if route == "get_state":
            return {
                "is_dead": True,
                "health": 0,
                "block_position": {"x": -66, "y": 63, "z": -125},
                "dimension": "minecraft:overworld",
            }
        if route == "get_death_location":
            return self.death_response
        return {}


def _context(death_response):
    state = RecoveryState()
    transport = RecoveryTransport(death_response)
    return SimpleNamespace(
        client=SimpleNamespace(transport=transport),
        state=state,
        resources=SimpleNamespace(),
    )


def test_death_recovery_accepts_unwrapped_bridge_response(monkeypatch):
    context = _context(
        {"x": -70, "y": 62, "z": -120, "dimension": "minecraft:overworld"}
    )
    monkeypatch.setattr(death_recovery_action, "goto", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        death_recovery_action, "secure_recovery_area", lambda _client: True
    )
    monkeypatch.setattr(death_recovery_action.time, "sleep", lambda _seconds: None)

    result = DeathRecoveryAction().execute(context)

    assert result.success
    assert result.data["recovered"]
    assert not result.data["reset_phase"]
    assert context.state.get_current_phase() == Phase.BASE_CONSTRUCTION
    assert context.state.custom_data["last_death_location"]["x"] == -70


def test_death_recovery_falls_back_to_pre_respawn_position(monkeypatch):
    context = _context({"has_death_location": False})
    destinations = []
    monkeypatch.setattr(
        death_recovery_action,
        "goto",
        lambda _client, x, y, z, **_kwargs: destinations.append((x, y, z)) or True,
    )
    monkeypatch.setattr(death_recovery_action.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        death_recovery_action, "secure_recovery_area", lambda _client: True
    )

    result = DeathRecoveryAction().execute(context)

    assert result.success
    assert destinations == [(-66, 63, -125)]


def test_death_recovery_fails_if_pickup_area_stays_unsafe(monkeypatch):
    context = _context(
        {"x": -70, "y": 62, "z": -120, "dimension": "minecraft:overworld"}
    )
    monkeypatch.setattr(death_recovery_action, "goto", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        death_recovery_action, "secure_recovery_area", lambda _client: False
    )
    monkeypatch.setattr(death_recovery_action.time, "sleep", lambda _seconds: None)

    result = DeathRecoveryAction().execute(context)

    assert not result.success
    assert "could not secure" in result.message


def test_death_recovery_fails_when_critical_inventory_is_still_missing(monkeypatch):
    context = _context(
        {"x": -70, "y": 62, "z": -120, "dimension": "minecraft:overworld"}
    )
    inventories = iter(
        (
            {"minecraft:diamond": 5, "minecraft:iron_pickaxe": 2},
            {"minecraft:diamond": 4, "minecraft:iron_pickaxe": 2},
        )
    )
    monkeypatch.setattr(death_recovery_action, "get_inventory", lambda _client: next(inventories))
    monkeypatch.setattr(death_recovery_action, "goto", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(death_recovery_action, "_sweep_death_drops", lambda *_args: True)
    monkeypatch.setattr(death_recovery_action.time, "sleep", lambda _seconds: None)

    result = DeathRecoveryAction().execute(context)

    assert not result.success
    assert result.message == "Critical inventory recovery is incomplete"
    assert result.data["missing"] == {"minecraft:diamond": 1}


def test_automator_stops_instead_of_resuming_after_failed_recovery(monkeypatch):
    class FailedRecovery:
        def execute(self, _context):
            return ActionResult.fail("partial inventory")

    automator = object.__new__(EndGameAutomator)
    automator.client = SimpleNamespace()
    automator.state = SimpleNamespace()
    automator.resources = SimpleNamespace()
    automator._running = True
    monkeypatch.setattr(automator_module, "DeathRecoveryAction", FailedRecovery)

    assert automator._handle_death_recovery()
    assert not automator._running
