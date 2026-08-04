from types import SimpleNamespace

import pytest

from baritone_client.actions import death_recovery_action
from baritone_client.actions.death_recovery_action import DeathRecoveryAction
from baritone_client.automator import automator as automator_module
from baritone_client.automator import objective_survival
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


def test_alive_player_clears_noncritical_stale_grave(monkeypatch):
    context = _context({})
    context.client.transport.dead = False
    context.state.custom_data["death_recovery"] = {
        "pending_location": [21, 53, 113],
        "pending_dimension": "minecraft:overworld",
        "expected_critical": {},
    }
    monkeypatch.setattr(death_recovery_action, "get_inventory", lambda _client: {})

    result = DeathRecoveryAction().execute(context)

    assert result.success
    assert result.data["grave_abandoned"]
    assert "death_recovery" not in context.state.custom_data
    assert context.state.custom_data["last_abandoned_death_recovery"][
        "reason"
    ] == "alive_with_no_critical_items_pending"


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


def test_automator_blocks_next_objective_until_survival_recovers(monkeypatch):
    class SurvivalTransport:
        def __init__(self):
            self.health = 5.0
            self.food = 6

        def dispatch(self, route, _payload):
            assert route == "get_state"
            return {
                "health": self.health,
                "food_level": self.food,
                "is_dead": False,
            }

    transport = SurvivalTransport()
    automator = object.__new__(EndGameAutomator)
    automator.client = SimpleNamespace(transport=transport)
    automator.state = SimpleNamespace()
    attempts = []

    monkeypatch.setattr(
        objective_survival,
        "_attempt_survival_recovery_food",
        lambda *_args: attempts.append("known") or False,
    )

    def recover(*_args):
        attempts.append("explore")
        transport.health = 12.0
        transport.food = 14
        return True

    monkeypatch.setattr(
        objective_survival,
        "_acquire_checkpointed_emergency_food",
        recover,
    )

    assert objective_survival.recover_survival_before_objective(
        automator.client, automator.state
    )
    assert attempts == ["known", "explore"]


def test_survival_gate_uses_carried_wheat_before_storage_navigation(monkeypatch):
    class SurvivalTransport:
        def __init__(self):
            self.health = 5.0
            self.food = 4

        def dispatch(self, route, _payload):
            assert route == "get_state"
            return {
                "health": self.health,
                "food_level": self.food,
                "is_dead": False,
            }

    transport = SurvivalTransport()
    client = SimpleNamespace(transport=transport)
    attempts = []
    monkeypatch.setattr(
        objective_survival,
        "_has_carried_emergency_bread_materials",
        lambda _client: True,
    )
    monkeypatch.setattr(
        objective_survival,
        "_attempt_survival_recovery_food",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("storage recovery must not precede carried wheat")
        ),
    )

    def recover(*_args):
        attempts.append("carried_wheat")
        transport.health = 12.0
        transport.food = 14
        return True

    monkeypatch.setattr(
        objective_survival,
        "_acquire_checkpointed_emergency_food",
        recover,
    )

    assert objective_survival.recover_survival_before_objective(
        client, SimpleNamespace()
    )
    assert attempts == ["carried_wheat"]


def test_automator_survival_gate_skips_recovery_at_safe_margin(monkeypatch):
    automator = object.__new__(EndGameAutomator)
    automator.client = SimpleNamespace(
        transport=SimpleNamespace(
            dispatch=lambda route, _payload: {
                "health": 12.0,
                "food_level": 10,
                "is_dead": False,
            }
        )
    )
    automator.state = SimpleNamespace()
    monkeypatch.setattr(
        objective_survival,
        "_attempt_survival_recovery_food",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("safe players must proceed directly to objective selection")
        ),
    )

    assert objective_survival.recover_survival_before_objective(
        automator.client, automator.state
    )


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
        custom_data={
            "phase_payloads": {
                "SPAWN_BOOTSTRAP": {
                    "return_home": {"origin": [10, 64, 20]}
                }
            }
        },
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
    assert attempts == [
        {
            "minimum_food": 14,
            "timeout": 120.0,
            "exploration_center": (10.0, 64.0, 20.0),
            "return_to_exploration_center": True,
        }
    ]


def test_critical_remote_survival_recovery_uses_local_bounded_anchor(monkeypatch):
    from baritone_client.automator import phase_executor as phase_executor_module

    state = SimpleNamespace(
        custom_data={
            "phase_payloads": {
                "SPAWN_BOOTSTRAP": {
                    "return_home": {"origin": [-168, 107, -396]}
                }
            }
        }
    )
    client = SimpleNamespace(
        transport=SimpleNamespace(
            dispatch=lambda *_args: {
                "health": 2.4,
                "food_level": 4,
                "block_position": {"x": -10, "y": 69, "z": -259},
            }
        )
    )
    attempts = []
    monkeypatch.setattr(
        "baritone_client.common.combat.acquire_emergency_food",
        lambda _client, **kwargs: attempts.append(kwargs) or True,
    )

    assert phase_executor_module._acquire_checkpointed_emergency_food(
        client, state
    )
    assert attempts[0]["exploration_center"] == (-10.0, 69.0, -259.0)
    assert attempts[0]["return_to_exploration_center"] is True


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
        custom_data={},
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


def test_submerged_grave_approach_stops_after_first_survival_abort(monkeypatch):
    transport = RecoveryTransport({})
    transport.dead = False
    client = SimpleNamespace(transport=transport)
    approaches = []

    def survival_aborted_goto(active_client, *_args, **_kwargs):
        approaches.append(True)
        active_client._last_navigation_survival_abort = True
        return False

    monkeypatch.setattr(death_recovery_action, "goto", survival_aborted_goto)

    assert not death_recovery_action._reach_overworld_grave(
        client, (-70, 52, -120), attempts=2
    )
    assert approaches == [True]


def test_aquatic_death_abandons_grave_before_navigation(monkeypatch):
    context = _context(
        {"x": -507, "y": 52, "z": 117, "dimension": "minecraft:overworld"}
    )
    monkeypatch.setattr(
        death_recovery_action,
        "position_is_aquatic",
        lambda *_args: True,
        raising=False,
    )
    monkeypatch.setattr(
        death_recovery_action,
        "goto",
        lambda *_args, **_kwargs: pytest.fail(
            "known aquatic grave must not be approached"
        ),
    )
    monkeypatch.setattr(death_recovery_action.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        death_recovery_action,
        "_bootstrap_starter_pickaxe",
        lambda _client: True,
    )

    result = DeathRecoveryAction().execute(context)

    assert result.success
    assert result.data["bootstrapped_tools"]
    assert not result.data["recovered"]


def test_death_recovery_defers_respawn_while_killer_remains(monkeypatch):
    context = _context(
        {"x": -129, "y": 68, "z": -94, "dimension": "minecraft:overworld"}
    )
    monkeypatch.setattr(
        death_recovery_action,
        "scan_for_threats",
        lambda *_args, **_kwargs: [
            {"type": "minecraft:zombie", "distance": 0.8}
        ],
        raising=False,
    )
    monkeypatch.setattr(death_recovery_action.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        death_recovery_action,
        "goto",
        lambda *_args, **_kwargs: pytest.fail(
            "a hostile death area must not be re-entered"
        ),
    )

    result = DeathRecoveryAction().execute(context)

    assert not result.success
    assert result.data["respawn_deferred"]
    assert context.client.transport.dead
    assert not any(
        route == "respawn" for route, _payload in context.client.transport.calls
    )


def test_grave_approach_aborts_after_first_combat_intervention(monkeypatch):
    transport = RecoveryTransport({})
    transport.dead = False
    client = SimpleNamespace(transport=transport)
    approaches = []

    monkeypatch.setattr(
        death_recovery_action,
        "defend_or_flee",
        lambda _client: True,
        raising=False,
    )

    def guarded_goto(_client, *_args, on_tick=None, **_kwargs):
        approaches.append(True)
        assert on_tick is not None
        try:
            on_tick()
        except RuntimeError:
            return False
        return True

    monkeypatch.setattr(death_recovery_action, "goto", guarded_goto)

    assert not death_recovery_action._reach_overworld_grave(
        client, (94, 13, 353), attempts=2
    )
    assert approaches == [True]


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
    assert context.state.custom_data["death_recovery"]["unsafe_failures"] == 1
    assert context.state.custom_data["death_recovery"]["location"] == [
        -70,
        62,
        -120,
    ]


def test_relaunch_abandons_lethal_grave_before_replaying_route(monkeypatch):
    """A dead relaunch must not repeat a grave route that already killed it."""
    context = _context({})
    context.state.custom_data["death_recovery"] = {
        "pending_location": [65, 53, -242],
        "pending_dimension": "minecraft:overworld",
        "expected_critical": {},
        "unsafe_failures": 1,
        "location": [65, 53, -242],
    }
    monkeypatch.setattr(death_recovery_action.time, "sleep", lambda _s: None)
    monkeypatch.setattr(
        death_recovery_action,
        "goto",
        lambda *_a, **_k: pytest.fail("lethal grave route must not be replayed"),
    )
    rebuild_states = []

    def rebuild(client):
        rebuild_states.append(client.transport.dead)
        return True

    monkeypatch.setattr(
        death_recovery_action, "_bootstrap_starter_pickaxe", rebuild
    )

    result = DeathRecoveryAction().execute(context)

    assert result.success
    assert result.data["grave_abandoned"]
    assert result.data["bootstrapped_tools"]
    assert rebuild_states == [False]
    assert "death_recovery" not in context.state.custom_data
    assert context.state.custom_data["last_abandoned_death_recovery"] == {
        "location": [65, 53, -242],
        "dimension": "minecraft:overworld",
        "unsafe_failures": 1,
        "reason": "repeated_unsafe_grave_route",
    }


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


def test_pacing_hold_for_hunger_actively_acquires_food(monkeypatch):
    """The pacing gates demand food>=16 (and carried reserves), but emergency
    food acquisition only triggers at food<=10 or health<12. A bot between
    those bands is too hungry to work and not hungry enough to go eat, so it
    holds forever. Measured live 2026-07-31 across 19 bots: zero
    BOOT_SEQUENCE completions ever, bots parked at food 11-17 with no carried
    food, yielding to pacing_hold 83+ times each."""
    from baritone_client.automator import phase_executor as phase_executor_module
    from baritone_client.common.tasks import PacingHoldRequired

    class HungryPacingHandler(PhaseHandler):
        def execute(self, client, resources, state):
            raise PacingHoldRequired("food below 16 before construction")

        def get_name(self):
            return "Pacing phase"

        def on_exit(self, client, resources, state):
            return None

    acquired = []
    monkeypatch.setattr(
        "baritone_client.common.combat.acquire_emergency_food",
        lambda _c, **kw: acquired.append(kw) or True,
    )

    state = SimpleNamespace(
        custom_data={},
        update_progress=lambda *_a, **_k: None,
        record_phase_payload=lambda *_a, **_k: None,
    )
    executor = PhaseExecutor(
        SimpleNamespace(transport=SimpleNamespace(dispatch=lambda *_a, **_k: {})),
        SimpleNamespace(refresh_inventory=lambda: None),
        state,
        max_retries=0,
        retry_delay=0,
        screenshot_enabled=False,
    )
    executor.register_handler(Phase.BOOT_SEQUENCE, HungryPacingHandler())

    assert executor.execute_phase(Phase.BOOT_SEQUENCE) is False
    assert executor.interruption_reason == "pacing_hold"
    assert acquired, "a hunger pacing hold must actively acquire food"
    assert acquired[0].get("minimum_food") == 18


def test_pacing_hold_unrelated_to_food_does_not_hunt(monkeypatch):
    """A daylight hold must just wait, not wander off hunting."""
    from baritone_client.common.tasks import PacingHoldRequired

    class NightHandler(PhaseHandler):
        def execute(self, client, resources, state):
            raise PacingHoldRequired("wait for daylight before construction")

        def get_name(self):
            return "Night phase"

        def on_exit(self, client, resources, state):
            return None

    acquired = []
    monkeypatch.setattr(
        "baritone_client.common.combat.acquire_emergency_food",
        lambda _c, **kw: acquired.append(kw) or True,
    )
    state = SimpleNamespace(
        custom_data={},
        update_progress=lambda *_a, **_k: None,
        record_phase_payload=lambda *_a, **_k: None,
    )
    executor = PhaseExecutor(
        SimpleNamespace(transport=SimpleNamespace(dispatch=lambda *_a, **_k: {})),
        SimpleNamespace(refresh_inventory=lambda: None),
        state,
        max_retries=0,
        retry_delay=0,
        screenshot_enabled=False,
    )
    executor.register_handler(Phase.BOOT_SEQUENCE, NightHandler())
    executor.execute_phase(Phase.BOOT_SEQUENCE)

    assert not acquired


def test_a_newer_death_replaces_a_stale_pending_grave(monkeypatch):
    """A pending grave is only worth resuming while it is still this death's.

    pending_location survives across deaths, and it used to override the
    bridge's own report of where the player just died. Live 2026-08-03: Bot07
    died in lava at (708, 27, 600) in the Nether carrying a diamond pickaxe
    and full iron, and recovery set off for (-12, 84, -40) -- an earlier death
    it had never finished walking to. The gear it actually dropped despawned.
    """
    context = _context(
        {"x": 708, "y": 27, "z": 600, "dimension": "minecraft:overworld"}
    )
    monkeypatch.setattr(
        death_recovery_action, "_wait_for_clear_death_area", lambda *_a: True
    )
    monkeypatch.setattr(
        death_recovery_action, "_bootstrap_starter_pickaxe", lambda *_a: True
    )
    context.state.custom_data["death_recovery"] = {
        "pending_location": [-12, 84, -40],
        "pending_dimension": "minecraft:overworld",
        "expected_critical": {},
        "unsafe_failures": 0,
    }
    monkeypatch.setattr(death_recovery_action, "goto", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        death_recovery_action, "secure_recovery_area", lambda _client: True
    )
    monkeypatch.setattr(death_recovery_action.time, "sleep", lambda _seconds: None)

    DeathRecoveryAction().execute(context)

    assert context.state.custom_data["last_abandoned_death_recovery"][
        "location"
    ] == [708, 27, 600], "the circuit must record the grave actually just made"
    # The stale target must not survive as something to walk to later.
    pending = (context.state.custom_data.get("death_recovery") or {}).get(
        "pending_location"
    )
    assert pending in (None, [708, 27, 600]), pending


def test_new_death_during_pending_recovery_opens_safety_circuit(monkeypatch):
    context = _context(
        {"x": -103, "y": 82, "z": 52, "dimension": "minecraft:the_nether"}
    )
    context.state.custom_data["death_recovery"] = {
        "pending_location": [-285, 76, 80],
        "pending_dimension": "minecraft:the_nether",
        "expected_critical": {"minecraft:iron_sword": 1},
        "unsafe_failures": 0,
    }
    monkeypatch.setattr(
        death_recovery_action,
        "_wait_for_clear_death_area",
        lambda *_a, **_k: True,
    )
    monkeypatch.setattr(
        death_recovery_action,
        "_bootstrap_starter_pickaxe",
        lambda *_a, **_k: True,
    )
    monkeypatch.setattr(death_recovery_action.time, "sleep", lambda _seconds: None)

    result = DeathRecoveryAction().execute(context)

    assert result.success
    assert result.data["grave_abandoned"] is True
    abandoned = context.state.custom_data["last_abandoned_death_recovery"]
    assert abandoned["location"] == [-103, 82, 52]
    assert abandoned["unsafe_failures"] == 1


def test_an_interrupted_recovery_still_resumes_the_same_grave(monkeypatch):
    """Resuming matters when a restart interrupts a walk to an unchanged grave."""
    context = _context(
        {"x": -12, "y": 84, "z": -40, "dimension": "minecraft:overworld"}
    )
    context.state.custom_data["death_recovery"] = {
        "pending_location": [-12, 84, -40],
        "pending_dimension": "minecraft:overworld",
        "expected_critical": {},
        "unsafe_failures": 0,
    }
    monkeypatch.setattr(death_recovery_action, "goto", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        death_recovery_action, "secure_recovery_area", lambda _client: True
    )
    monkeypatch.setattr(death_recovery_action.time, "sleep", lambda _seconds: None)

    DeathRecoveryAction().execute(context)

    assert context.state.custom_data["last_death_location"]["x"] == -12


def test_corpse_position_wins_when_the_bridge_has_no_death_location(monkeypatch):
    """has_death_location: False left the stale pending target as the only clue.

    The bridge frequently reports no death location at all. The corpse's own
    coordinate, read before respawning, is exactly where the items dropped.
    Live 2026-08-03: Bot17 died at (-315, 44, 187) in the Nether, the bridge
    answered has_death_location: False, and recovery announced
    (-12, 84, -47) -- a grave from a previous death.
    """
    context = _context({"has_death_location": False})
    context.client.transport.death_position = {"x": -315, "y": 44, "z": 187}
    context.state.custom_data["death_recovery"] = {
        "pending_location": [-12, 84, -47],
        "pending_dimension": "minecraft:overworld",
        "expected_critical": {},
        "unsafe_failures": 0,
    }

    # The corpse lies where it died until it respawns.
    original = context.client.transport.dispatch

    def dispatch(route, payload):
        if route == "get_state" and context.client.transport.dead:
            return {
                "is_dead": True,
                "health": 0,
                "block_position": {"x": -315, "y": 44, "z": 187},
                "dimension": "minecraft:overworld",
            }
        return original(route, payload)

    context.client.transport.dispatch = dispatch
    monkeypatch.setattr(death_recovery_action, "goto", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        death_recovery_action, "secure_recovery_area", lambda _client: True
    )
    monkeypatch.setattr(
        death_recovery_action, "_wait_for_clear_death_area", lambda *_a: True
    )
    monkeypatch.setattr(
        death_recovery_action, "_bootstrap_starter_pickaxe", lambda *_a: True
    )
    monkeypatch.setattr(death_recovery_action.time, "sleep", lambda _seconds: None)

    DeathRecoveryAction().execute(context)

    assert context.state.custom_data["last_abandoned_death_recovery"][
        "location"
    ] == [-315, 44, 187], "must circuit-break on the current corpse location"
