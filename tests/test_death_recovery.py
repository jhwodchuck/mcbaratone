from types import SimpleNamespace

import pytest

from baritone_client.actions import death_recovery_action
from baritone_client.actions.death_recovery_action import DeathRecoveryAction
from baritone_client.automator import automator as automator_module
from baritone_client.automator.automator import EndGameAutomator
from baritone_client.automator.state_manager import Phase
from baritone_client.automator.phase_executor import PhaseExecutor, PhaseHandler
from baritone_client.common.tasks import (
    ActionTask,
    PlayerDeathDetected,
    SurvivalRecoveryRequired,
    SequentialTask,
    TaskResult,
)
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
        self.dead = True

    def dispatch(self, route, payload):
        self.calls.append((route, payload))
        if route == "get_state":
            return {
                "is_dead": self.dead,
                "health": 0 if self.dead else 20,
                "block_position": {"x": -66, "y": 63, "z": -125},
                "dimension": "minecraft:overworld",
            }
        if route == "respawn":
            self.dead = False
            return {}
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


def test_alive_operator_restart_abandons_exhausted_pending_grave(monkeypatch):
    context = _context({})
    context.client.transport.dead = False
    context.state.custom_data["death_recovery"] = {
        "pending_location": [-150, 11, 220],
        "pending_dimension": "minecraft:overworld",
        "unsafe_failures": 1,
    }
    monkeypatch.setattr(
        death_recovery_action,
        "get_inventory",
        lambda _client: {"minecraft:dirt": 2},
    )

    result = DeathRecoveryAction().execute(context)

    assert result.success
    assert result.data["grave_abandoned"]
    assert "death_recovery" not in context.state.custom_data
    assert context.state.custom_data["last_abandoned_death_recovery"] == {
        "location": [-150, 11, 220],
        "dimension": "minecraft:overworld",
        "unsafe_failures": 1,
        "reason": "operator_restart_after_terminal_safety_stop",
    }
    assert not any(
        route == "respawn" for route, _payload in context.client.transport.calls
    )


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
    assert "could not retreat or secure" in result.message
    assert result.data["unsafe_recovery_failures"] == 1


def test_death_recovery_never_resumes_phase_if_player_dies_while_securing(monkeypatch):
    context = _context(
        {"x": -70, "y": 62, "z": -120, "dimension": "minecraft:overworld"}
    )
    monkeypatch.setattr(death_recovery_action, "goto", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(death_recovery_action.time, "sleep", lambda _seconds: None)

    def die_while_securing(_client):
        context.client.transport.dead = True
        return True

    monkeypatch.setattr(
        death_recovery_action, "secure_recovery_area", die_while_securing
    )

    result = DeathRecoveryAction().execute(context)

    assert not result.success
    assert result.message == "Player died while securing the recovered grave"
    assert context.state.custom_data["death_recovery"]["pending_location"] == [
        -70,
        62,
        -120,
    ]


def test_death_recovery_retreats_to_checkpointed_storage(monkeypatch):
    context = _context(
        {"x": -70, "y": 62, "z": -120, "dimension": "minecraft:overworld"}
    )
    context.state.custom_data["structures"] = {
        "starter_house": {"supply_chest": [-10, 70, -60]}
    }
    destinations = []
    monkeypatch.setattr(
        death_recovery_action,
        "goto",
        lambda _client, x, y, z, **_kwargs: destinations.append((x, y, z)) or True,
    )
    monkeypatch.setattr(death_recovery_action.time, "sleep", lambda _seconds: None)

    result = DeathRecoveryAction().execute(context)

    assert result.success
    assert result.data["retreated"]
    assert destinations == [(-70, 62, -120), (-10, 70, -60)]


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


def test_sequential_task_yields_dead_player_without_respawning():
    calls = []

    class DeadTransport:
        def dispatch(self, route, payload):
            calls.append((route, payload))
            if route == "get_state":
                return {"is_dead": True, "health": 0}
            raise AssertionError(f"Unexpected route: {route}")

    client = SimpleNamespace(transport=DeadTransport())
    task = SequentialTask("phase", [ActionTask("never", lambda _client: True)])

    with pytest.raises(PlayerDeathDetected):
        task.run(client)

    assert calls == [("get_state", {})]


def test_phase_executor_does_not_retry_player_death():
    class DeadHandler(PhaseHandler):
        def __init__(self):
            self.calls = 0
            self.exited = False

        def execute(self, client, resources, state):
            self.calls += 1
            raise PlayerDeathDetected("dead")

        def get_name(self):
            return "Dead phase"

        def on_exit(self, client, resources, state):
            self.exited = True

    state = SimpleNamespace(
        update_progress=lambda *_args, **_kwargs: None,
        record_phase_payload=lambda *_args, **_kwargs: None,
    )
    resources = SimpleNamespace(refresh_inventory=lambda: None)
    client = SimpleNamespace()
    handler = DeadHandler()
    executor = PhaseExecutor(
        client,
        resources,
        state,
        max_retries=3,
        retry_delay=0,
        screenshot_enabled=False,
    )
    executor.register_handler(Phase.FOOD_AND_IRON, handler)

    assert not executor.execute_phase(Phase.FOOD_AND_IRON)
    assert executor.interruption_reason == "player_death"
    assert handler.calls == 1
    assert handler.exited


def test_phase_executor_does_not_retry_safe_survival_hold():
    class HoldingHandler(PhaseHandler):
        def __init__(self):
            self.calls = 0
            self.exited = False

        def execute(self, client, resources, state):
            self.calls += 1
            raise SurvivalRecoveryRequired("food remains below threshold")

        def get_name(self):
            return "Holding phase"

        def on_exit(self, client, resources, state):
            self.exited = True

    state = SimpleNamespace(
        update_progress=lambda *_args, **_kwargs: None,
        record_phase_payload=lambda *_args, **_kwargs: None,
    )
    resources = SimpleNamespace(refresh_inventory=lambda: None)
    handler = HoldingHandler()
    executor = PhaseExecutor(
        SimpleNamespace(),
        resources,
        state,
        max_retries=3,
        retry_delay=0,
        screenshot_enabled=False,
    )
    executor.register_handler(Phase.FOOD_AND_IRON, handler)

    assert not executor.execute_phase(Phase.FOOD_AND_IRON)
    assert executor.interruption_reason == "survival_recovery"
    assert handler.calls == 1
    assert handler.exited


def test_survival_recovery_actively_seeks_food_before_retrying(monkeypatch):
    """Regression: the yield handler used to just sleep(2.0) and return, so a
    phase that keeps hitting the same food gate (ensure_supplies raises this
    when eat_until_hunger fails because nothing is CARRIED -- it never hunts)
    replayed identically forever with food frozen. Confirmed live: Bot08
    looped 700+ attempts at food=8/20 sitting at full health, never once
    leaving base to find food. The handler must actively attempt recovery,
    not just pause."""
    from baritone_client.automator import phase_executor as phase_executor_module

    class HoldingHandler(PhaseHandler):
        def __init__(self):
            self.calls = 0
            self.exited = False

        def execute(self, client, resources, state):
            self.calls += 1
            raise SurvivalRecoveryRequired("food remains 8/20 while ensuring supplies")

        def get_name(self):
            return "Holding phase"

        def on_exit(self, client, resources, state):
            self.exited = True

    state = SimpleNamespace(
        update_progress=lambda *_args, **_kwargs: None,
        record_phase_payload=lambda *_args, **_kwargs: None,
    )
    resources = SimpleNamespace(refresh_inventory=lambda: None)
    handler = HoldingHandler()
    client = SimpleNamespace(transport=SimpleNamespace(dispatch=lambda *_a, **_k: {}))
    executor = PhaseExecutor(
        client, resources, state, max_retries=3, retry_delay=0, screenshot_enabled=False
    )
    executor.register_handler(Phase.FOOD_AND_IRON, handler)

    attempts = []
    monkeypatch.setattr(
        phase_executor_module.time, "sleep", lambda _s: None
    )
    monkeypatch.setattr(
        "baritone_client.common.combat.acquire_emergency_food",
        lambda _client, **kwargs: attempts.append(kwargs) or True,
    )

    assert not executor.execute_phase(Phase.FOOD_AND_IRON)
    assert executor.interruption_reason == "survival_recovery"
    assert attempts == [{"minimum_food": 14, "timeout": 120.0}]


def test_survival_recovery_food_attempt_failure_does_not_crash_the_executor(
    monkeypatch,
):
    """A failed food hunt must not turn the yield into an unhandled crash --
    the phase should still cleanly yield and let the caller's backoff/retry
    loop try again."""
    from baritone_client.automator import phase_executor as phase_executor_module

    class HoldingHandler(PhaseHandler):
        def execute(self, client, resources, state):
            raise SurvivalRecoveryRequired("food remains 8/20 while ensuring supplies")

        def get_name(self):
            return "Holding phase"

        def on_exit(self, client, resources, state):
            pass

    state = SimpleNamespace(
        update_progress=lambda *_args, **_kwargs: None,
        record_phase_payload=lambda *_args, **_kwargs: None,
    )
    resources = SimpleNamespace(refresh_inventory=lambda: None)
    client = SimpleNamespace(transport=SimpleNamespace(dispatch=lambda *_a, **_k: {}))
    executor = PhaseExecutor(
        client, resources, state, max_retries=3, retry_delay=0, screenshot_enabled=False
    )
    executor.register_handler(Phase.FOOD_AND_IRON, HoldingHandler())

    monkeypatch.setattr(phase_executor_module.time, "sleep", lambda _s: None)

    def _boom(_client, **_kwargs):
        raise RuntimeError("bridge unreachable")

    monkeypatch.setattr(
        "baritone_client.common.combat.acquire_emergency_food", _boom
    )

    assert not executor.execute_phase(Phase.FOOD_AND_IRON)
    assert executor.interruption_reason == "survival_recovery"


def test_bootstrap_starter_pickaxe_skips_when_already_carried(monkeypatch):
    from baritone_client.common import inventory as inv
    from baritone_client.common import resources

    monkeypatch.setattr(
        inv,
        "count_item",
        lambda _c, item_id: 1 if item_id == "minecraft:wooden_pickaxe" else 0,
    )
    crafted = []
    monkeypatch.setattr(
        resources,
        "ensure_supplies",
        lambda *_a, **_k: crafted.append(True) or SimpleNamespace(success=True),
    )
    client = SimpleNamespace(transport=SimpleNamespace())

    assert death_recovery_action._bootstrap_starter_pickaxe(client) is True
    assert crafted == []  # already had a pickaxe; must not attempt a craft


def test_bootstrap_starter_pickaxe_crafts_when_missing(monkeypatch):
    from baritone_client.common import inventory as inv
    from baritone_client.common import resources

    have = {"pick": False}
    monkeypatch.setattr(
        inv,
        "count_item",
        lambda _c, item_id: 1
        if (item_id == "minecraft:wooden_pickaxe" and have["pick"])
        else 0,
    )

    def ensure(*_a, **_k):
        have["pick"] = True
        return SimpleNamespace(success=True)

    monkeypatch.setattr(resources, "ensure_supplies", ensure)
    client = SimpleNamespace(transport=SimpleNamespace())

    assert death_recovery_action._bootstrap_starter_pickaxe(client) is True


def test_bootstrap_starter_pickaxe_fails_when_craft_yields_nothing(monkeypatch):
    from baritone_client.common import inventory as inv
    from baritone_client.common import resources

    monkeypatch.setattr(inv, "count_item", lambda _c, _item: 0)
    monkeypatch.setattr(
        resources,
        "ensure_supplies",
        lambda *_a, **_k: SimpleNamespace(success=True),  # claims ok but no pickaxe
    )
    client = SimpleNamespace(transport=SimpleNamespace())

    # Success must be gated on a pickaxe actually appearing, not just the claim.
    assert death_recovery_action._bootstrap_starter_pickaxe(client) is False


def test_lost_grave_bootstraps_starter_tools(monkeypatch):
    context = _context(
        {"x": -70, "y": 62, "z": -120, "dimension": "minecraft:overworld"}
    )
    # Grave unreachable -> _reach_overworld_grave fails on every approach.
    monkeypatch.setattr(death_recovery_action, "goto", lambda *_a, **_k: False)
    monkeypatch.setattr(death_recovery_action.time, "sleep", lambda _s: None)
    boot = []
    monkeypatch.setattr(
        death_recovery_action,
        "_bootstrap_starter_pickaxe",
        lambda _client: boot.append(True) or True,
    )

    result = DeathRecoveryAction().execute(context)

    assert result.success
    assert result.data["bootstrapped_tools"]
    assert not result.data["recovered"]
    assert boot == [True]


def test_lost_grave_still_fails_when_bootstrap_fails(monkeypatch):
    context = _context(
        {"x": -70, "y": 62, "z": -120, "dimension": "minecraft:overworld"}
    )
    monkeypatch.setattr(death_recovery_action, "goto", lambda *_a, **_k: False)
    monkeypatch.setattr(death_recovery_action.time, "sleep", lambda _s: None)
    monkeypatch.setattr(
        death_recovery_action, "_bootstrap_starter_pickaxe", lambda _client: False
    )

    result = DeathRecoveryAction().execute(context)

    assert not result.success


def test_repeated_incomplete_grave_is_abandoned_instead_of_looping(monkeypatch):
    """Regression for a live infinite relaunch loop.

    The incomplete-shortfall path used to `return fail` without recording an
    unsafe failure. abandon_exhausted_pending_recovery only fires at
    unsafe_failures >= 1, so the counter stayed 0, the grave was never written
    off, and every supervisor relaunch resumed the same unsatisfiable target.
    Bot10 died 12 times in two hours cycling one river grave whose iron kit had
    already despawned. Past the attempt bound the grave must be abandoned so
    the bot can continue.
    """
    context = _context(
        {"x": -704, "y": 61, "z": 151, "dimension": "minecraft:overworld"}
    )
    # Always short the expected iron pickaxe: the drop is gone for good.
    monkeypatch.setattr(
        death_recovery_action,
        "get_inventory",
        lambda _client: {"minecraft:iron_pickaxe": 1},
    )
    monkeypatch.setattr(death_recovery_action, "goto", lambda *_a, **_k: True)
    monkeypatch.setattr(death_recovery_action, "_sweep_death_drops", lambda *_a: True)
    monkeypatch.setattr(death_recovery_action.time, "sleep", lambda _s: None)
    monkeypatch.setattr(
        death_recovery_action, "_bootstrap_starter_pickaxe", lambda _c: True
    )
    # Pre-load the expectation the way a real prior death would have.
    context.state.custom_data["death_recovery"] = {
        "expected_critical": {"minecraft:iron_pickaxe": 2},
        "pending_location": [-704, 61, 151],
        "pending_dimension": "minecraft:overworld",
    }

    # Attempt 1: still worth retrying -> fail, but now it COUNTS the attempt.
    first = DeathRecoveryAction().execute(context)
    assert not first.success
    assert context.state.custom_data["death_recovery"]["unsafe_failures"] == 1

    # Attempt 2: the recorded failure now lets the existing repair boundary
    # fire, so the grave is written off instead of resumed a third time.
    # (Which of the two abandon paths runs depends on whether the player is
    # alive at entry; both must break the loop.)
    second = DeathRecoveryAction().execute(context)
    assert second.success
    assert second.data.get("grave_abandoned")
    # The pending grave is cleared so the next launch cannot resume the trap.
    assert "death_recovery" not in context.state.custom_data
    assert context.state.custom_data["last_abandoned_death_recovery"][
        "location"
    ] == [-704, 61, 151]


def test_abandon_unrecoverable_grave_clears_target_and_records_reason():
    state = SimpleNamespace(
        custom_data={
            "death_recovery": {
                "pending_location": [-704, 61, 151],
                "expected_critical": {"minecraft:iron_pickaxe": 2},
            }
        },
        save_checkpoint=lambda _inv: None,
    )

    death_recovery_action.abandon_unrecoverable_grave(
        state, (-704, 61, 151), {"minecraft:iron_pickaxe": 1}, {}, 2
    )

    # Both the location AND its expected-item list must go, or the next launch
    # resumes an unsatisfiable target.
    assert "death_recovery" not in state.custom_data
    abandoned = state.custom_data["last_abandoned_death_recovery"]
    assert abandoned["reason"] == "critical_items_unrecoverable"
    assert abandoned["missing"] == {"minecraft:iron_pickaxe": 1}
    assert abandoned["unsafe_failures"] == 2


def test_abandoned_grave_bootstraps_tools_so_the_bot_can_function(monkeypatch):
    context = _context(
        {"x": -704, "y": 61, "z": 151, "dimension": "minecraft:overworld"}
    )
    monkeypatch.setattr(
        death_recovery_action, "get_inventory", lambda _client: {}
    )
    monkeypatch.setattr(death_recovery_action, "goto", lambda *_a, **_k: True)
    monkeypatch.setattr(death_recovery_action, "_sweep_death_drops", lambda *_a: True)
    monkeypatch.setattr(death_recovery_action.time, "sleep", lambda _s: None)
    bootstrapped = []
    monkeypatch.setattr(
        death_recovery_action,
        "_bootstrap_starter_pickaxe",
        lambda _c: bootstrapped.append(True) or True,
    )
    context.state.custom_data["death_recovery"] = {
        "expected_critical": {"minecraft:stone_pickaxe": 1},
        "pending_location": [-704, 61, 151],
        "pending_dimension": "minecraft:overworld",
        # Already at the bound: this execute() should abandon immediately.
        "unsafe_failures": 1,
        "location": [-704, 61, 151],
    }

    result = DeathRecoveryAction().execute(context)

    assert result.success
    assert bootstrapped == [True]
