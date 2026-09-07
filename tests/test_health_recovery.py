"""recover_health's wait loop must not drown the bot it is trying to save.

Live A1 2026-09-06/07: 5 drowning deaths, several during "Verify Nether
expedition loadout" (which calls recover_health directly) and
acquire_emergency_food (which calls recover_health first thing). The loop
held position and slept in 2-second increments for up to a minute with no
drowning reflex at all -- every other long-running wait loop in this
codebase (goto, nether_travel, wood_gathering) already calls survival_tick
each poll; this one, sitting right inside the recovery path itself, was the
gap.
"""

from types import SimpleNamespace

from baritone_client.common import health_recovery


def _health_dispatch(healths):
    """Advance the health sequence only on get_state; ignore cancel/chat."""
    remaining = iter(healths)
    last = {"value": healths[0] if healths else 20.0}

    def dispatch(route, _payload=None, **_kwargs):
        if route == "get_state":
            last["value"] = next(remaining, last["value"])
        return {"health": last["value"]}

    return dispatch


def test_recover_health_calls_survival_tick_each_iteration(monkeypatch):
    client = SimpleNamespace(
        transport=SimpleNamespace(
            dispatch=_health_dispatch([5.0, 5.0, 20.0])
        )
    )
    calls = []
    monkeypatch.setattr(
        "baritone_client.common.combat.survival_tick",
        lambda _client, state: calls.append(state) or False,
    )
    monkeypatch.setattr(
        "baritone_client.common.combat.heal_if_needed", lambda *_a, **_k: True
    )
    monkeypatch.setattr(health_recovery.time, "sleep", lambda _s: None)

    assert health_recovery.recover_health(client, minimum_health=18.0, timeout=10.0)
    assert len(calls) >= 2, "survival_tick must run on every wait iteration"


def test_recover_health_runs_survival_tick_while_drowning(monkeypatch):
    """The exact live bug: holding position in water while 'recovering'.

    Health dropping across iterations (as drowning damage would cause) must
    still route through survival_tick on every single poll, not just the
    ones where heal_if_needed happens to also run.
    """
    client = SimpleNamespace(
        transport=SimpleNamespace(
            dispatch=_health_dispatch([5.0, 4.0, 3.0, 20.0])
        )
    )
    observed = []
    monkeypatch.setattr(
        "baritone_client.common.combat.survival_tick",
        lambda _client, state: observed.append(state["health"]) or True,
    )
    monkeypatch.setattr(
        "baritone_client.common.combat.heal_if_needed", lambda *_a, **_k: True
    )
    monkeypatch.setattr(health_recovery.time, "sleep", lambda _s: None)

    assert health_recovery.recover_health(client, minimum_health=18.0, timeout=10.0)
    assert observed == [4.0, 3.0, 20.0]


def test_recover_health_returns_immediately_when_already_safe(monkeypatch):
    """No wait loop, no survival_tick call, when health is already fine."""
    client = SimpleNamespace(
        transport=SimpleNamespace(dispatch=lambda *_a, **_k: {"health": 20.0})
    )
    called = []
    monkeypatch.setattr(
        "baritone_client.common.combat.survival_tick",
        lambda *_a, **_k: called.append(True),
    )

    assert health_recovery.recover_health(client, minimum_health=18.0)
    assert not called
