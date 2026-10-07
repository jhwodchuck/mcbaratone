"""A clear defense verdict stops a distant idle mob from starving all work."""

import threading
from types import SimpleNamespace

import pytest

from baritone_client.automator import safety_recovery
from baritone_client.common.combat_action import exclusive_combat_action
from baritone_client.common.defense import DefenseMode


@pytest.fixture(autouse=True)
def reset_quiet():
    safety_recovery._quiet.update(until=0.0, hostiles=0)
    yield
    safety_recovery._quiet.update(until=0.0, hostiles=0)


def _signals(hostiles):
    return SimpleNamespace(
        observed=True, entities_observed=True, dimension="minecraft:overworld",
        nearby_hostiles=hostiles,
    )


def _run(monkeypatch, *, acted, mode, hostiles=1):
    monkeypatch.setattr(safety_recovery, "defend_or_flee", lambda *_a, **_k: acted)
    monkeypatch.setattr(safety_recovery, "_resolved_mode", lambda _c: mode)
    client = SimpleNamespace(transport=SimpleNamespace())
    return safety_recovery.run_self_defense(client, lambda: _signals(hostiles))


def test_clear_verdict_quiets_reselection_for_the_same_hostiles(monkeypatch):
    clock = {"t": 100.0}
    monkeypatch.setattr(safety_recovery.time, "monotonic", lambda: clock["t"])
    assert safety_recovery.select_self_defense(_signals(1)) is not None

    _run(monkeypatch, acted=False, mode=DefenseMode.CLEAR)

    assert safety_recovery.select_self_defense(_signals(1)) is None
    # More hostiles than the quiet verdict covered: defend again at once.
    assert safety_recovery.select_self_defense(_signals(2)) is not None
    # The verdict expires.
    clock["t"] += safety_recovery.QUIET_SECONDS + 1
    assert safety_recovery.select_self_defense(_signals(1)) is not None


def test_a_pass_that_acted_does_not_quiet_defense(monkeypatch):
    _run(monkeypatch, acted=True, mode=DefenseMode.CLEAR)

    assert safety_recovery.select_self_defense(_signals(1)) is not None


def test_unknown_mode_does_not_quiet_defense(monkeypatch):
    _run(monkeypatch, acted=False, mode=None)

    assert safety_recovery.select_self_defense(_signals(1)) is not None


def test_scheduled_defense_defers_while_background_action_owns_client(monkeypatch):
    client = SimpleNamespace(transport=SimpleNamespace())
    defense_calls = []
    holding_lock = threading.Event()
    release_lock = threading.Event()

    def hold_client_action():
        with exclusive_combat_action(client):
            holding_lock.set()
            release_lock.wait(timeout=2)

    holder = threading.Thread(target=hold_client_action)
    holder.start()
    assert holding_lock.wait(timeout=1)
    monkeypatch.setattr(
        safety_recovery,
        "defend_or_flee",
        lambda *_a, **_k: defense_calls.append(True) or True,
    )
    try:
        outcome = safety_recovery.run_self_defense(
            client, lambda: _signals(1)
        )
    finally:
        release_lock.set()
        holder.join(timeout=2)

    assert not holder.is_alive()
    assert defense_calls == []
    assert outcome == (
        False,
        "defense deferred while another client action owns controls",
        1,
        1,
    )
