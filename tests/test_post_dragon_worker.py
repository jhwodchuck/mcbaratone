import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest

from baritone_client.operations import post_dragon_worker as module
from baritone_client.operations import post_dragon_runtime as runtime_module


class Transport:
    def __init__(self, states):
        self.states = list(states)
        self.calls = []

    def dispatch(self, route, payload):
        self.calls.append((route, payload))
        if route == "get_state":
            if len(self.states) > 1:
                return self.states.pop(0)
            return self.states[0]
        return {}


def state(**overrides):
    value = {
        "health": 20,
        "food_level": 20,
        "dimension": "minecraft:the_end",
        "game_mode": "survival",
        "is_dead": False,
        "automation_profile": "non_op_client",
        "server_authority": False,
        "player_name": "Bot19",
    }
    value.update(overrides)
    return value


def checkpoint():
    return SimpleNamespace(
        custom_data={"end_city": {"location": [100, 70, 200], "verified": True}},
        save_checkpoint=lambda inventory: None,
    )


class Resources:
    cached_inventory = {}

    def refresh_inventory(self):
        return {}


def worker(states, *, scheduler=None, checkpoint_state=None):
    client = SimpleNamespace(transport=Transport(states))
    checkpoint_value = checkpoint_state or checkpoint()
    return (
        module.PostDragonWorker(
            client,
            Resources(),
            checkpoint_value,
            bot_name="Bot19",
            sleeper=lambda _seconds: None,
            scheduler=scheduler,
        ),
        client,
        checkpoint_value,
    )


def test_assignment_must_be_verified():
    value = checkpoint()
    value.custom_data["end_city"]["verified"] = False
    with pytest.raises(ValueError, match="verified end_city"):
        module.assigned_end_city(value)


def test_dead_bot_respawns_then_holds_without_restage(monkeypatch):
    subject, client, checkpoint_value = worker(
        [
            state(health=0, is_dead=True),
            state(health=0, is_dead=True),
            state(),
            state(),
        ],
    )
    monkeypatch.setattr(module, "reset_inventory_cache", lambda: None)

    turn = subject.run_turn()

    assert turn.state == "manual_recovery_hold"
    assert "respawned normally" in turn.detail
    assert ("respawn", {}) in client.transport.calls
    assert (
        checkpoint_value.custom_data["post_dragon_worker"]["manual_recovery_hold"][
            "reason"
        ]
        == turn.detail
    )
    assert {route for route, _payload in client.transport.calls} <= {
        "get_state",
        "cancel",
        "respawn",
    }


def test_sub_end_hunt_survival_margin_recovers_before_work(monkeypatch):
    subject, _client, _checkpoint = worker([state(health=17, food_level=17)] * 3)
    eaten = []
    monkeypatch.setattr(module, "equip_best_armor", lambda _client: 4)
    monkeypatch.setattr(module, "equip_best_weapon", lambda _client: True)
    monkeypatch.setattr(module, "defend_or_flee", lambda *_args, **_kwargs: False)

    def eat(_client, minimum_food):
        eaten.append(minimum_food)
        return True

    monkeypatch.setattr(module, "eat_until_hunger", eat)

    turn = subject.run_turn()

    assert turn.state == "recovering"
    assert eaten == [18]


def test_critical_health_cancels_and_persists_manual_hold(monkeypatch):
    subject, client, checkpoint_value = worker(
        [state(health=5, food_level=17)] * 3,
    )

    turn = subject.run_turn()

    assert turn.state == "manual_recovery_hold"
    assert "critical End health" in turn.detail
    assert ("cancel", {}) in client.transport.calls
    assert "manual_recovery_hold" in checkpoint_value.custom_data["post_dragon_worker"]


def test_critical_stabilizer_only_cancels_and_returns_hold():
    transport = Transport([state(health=5, food_level=16)])
    client = SimpleNamespace(transport=transport)

    result = module.stabilize_critical_bot(
        client,
        bot_name="Bot19",
    )

    assert result is not None and result.state == "manual_recovery_hold"
    assert ("cancel", {}) in transport.calls
    assert transport.calls == [("get_state", {}), ("cancel", {})]


def test_safe_turn_runs_only_selected_post_dragon_opportunity(monkeypatch):
    opportunity = SimpleNamespace(kind=SimpleNamespace(value="end_supply"))
    result = SimpleNamespace(success=True, detail="elytra increased", before=0, after=1)
    scheduler = SimpleNamespace(
        observe=lambda: SimpleNamespace(),
        run_local_opportunity=lambda selected: (
            result if selected is opportunity else None
        ),
    )
    subject, _client, _checkpoint = worker([state()] * 4, scheduler=scheduler)
    monkeypatch.setattr(module, "equip_best_armor", lambda _client: 4)
    monkeypatch.setattr(module, "equip_best_weapon", lambda _client: True)
    monkeypatch.setattr(module, "defend_or_flee", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        module, "select_specialty_opportunity", lambda **_kwargs: opportunity
    )

    turn = subject.run_turn()

    assert turn.progressed is True
    assert (turn.before, turn.after) == (0, 1)
    assert "elytra increased" in turn.detail


def test_wrong_dimension_holds_without_admin_movement():
    subject, client, checkpoint_value = worker([state(dimension="minecraft:overworld")])

    turn = subject.run_turn()

    assert turn.state == "manual_recovery_hold"
    assert "outside the End" in turn.detail
    assert client.transport.calls == [("get_state", {}), ("cancel", {})]
    assert "manual_recovery_hold" in checkpoint_value.custom_data["post_dragon_worker"]


def test_identity_mismatch_holds_without_sending_mutations():
    subject, client, _checkpoint = worker([state(player_name="Bot07")])

    turn = subject.run_turn()

    assert turn.state == "manual_recovery_hold"
    assert "identity mismatch" in turn.detail
    assert client.transport.calls == [("get_state", {})]


def test_missing_bridge_state_persists_hold_without_mutation():
    subject, client, checkpoint_value = worker([{}])

    turn = subject.run_turn()

    assert turn.state == "manual_recovery_hold"
    assert turn.detail == "bridge state is unavailable"
    assert client.transport.calls == [("get_state", {})]
    assert "manual_recovery_hold" in checkpoint_value.custom_data["post_dragon_worker"]


def test_runtime_close_does_not_cancel_an_identity_mismatch(tmp_path):
    transport = Transport([state(player_name="Bot07")])
    shutdown = []
    runtime = object.__new__(runtime_module.PostDragonBotRuntime)
    runtime.bot_name = "Bot19"
    runtime.pid_path = tmp_path / "Bot19.pid"
    runtime.client = SimpleNamespace(
        transport=transport,
        shutdown=lambda: shutdown.append(True),
    )

    runtime.close()

    assert transport.calls == [("get_state", {})]
    assert shutdown == [True]


def test_verified_manual_recovery_clears_checkpoint_hold(monkeypatch):
    checkpoint_value = checkpoint()
    checkpoint_value.custom_data["post_dragon_worker"] = {
        "manual_recovery_hold": {
            "reason": "return to the End manually",
            "timestamp": 1.0,
        }
    }
    subject, _client, _checkpoint = worker(
        [state(health=20, food_level=20)] * 4,
        checkpoint_state=checkpoint_value,
    )
    monkeypatch.setattr(module, "equip_best_armor", lambda _client: 4)
    monkeypatch.setattr(module, "equip_best_weapon", lambda _client: True)
    monkeypatch.setattr(module, "defend_or_flee", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(module, "select_specialty_opportunity", lambda **_kwargs: None)

    turn = subject.run_turn()

    assert turn.state == "waiting"
    assert (
        "manual_recovery_hold" not in checkpoint_value.custom_data["post_dragon_worker"]
    )


def test_restage_callback_surface_and_rcon_runtime_are_absent():
    assert "restage" not in inspect.signature(module.PostDragonWorker).parameters
    assert "restage" not in inspect.signature(module.recover_dead_bot).parameters
    assert "restage" not in inspect.signature(module.stabilize_critical_bot).parameters
    source = Path(runtime_module.__file__).read_text(encoding="utf-8")
    assert "RconClient" not in source
    assert "spreadplayers" not in source
    assert 'command(f"tp ' not in source
    assert "_restager" not in source
