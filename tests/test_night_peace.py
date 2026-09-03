"""Do not hide from a night that has nothing in it.

Waiting out a night costs roughly seven minutes. dragon-a's sealed campaign
recorded 205 daylight holds against 3 completed objectives -- the single
largest category of lost runtime in the progression lane, on a world whose
hostile spawning is deliberately disabled.

A non-op client cannot read the gamerule, so the world is judged by what it
contains. The risk runs the other way too: skipping shelter on a hostile world
sends an unarmed bot out to die, so one empty scan is never enough.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from baritone_client.common import night_peace


@pytest.fixture
def client():
    """A bot that has already stood through the grace period."""
    import time

    return SimpleNamespace(
        transport=SimpleNamespace(),
        _peaceful_night_since=time.monotonic() - night_peace.PEACEFUL_GRACE_SECONDS - 1,
    )


def _threats(monkeypatch, sequence):
    calls = iter(sequence)
    monkeypatch.setattr(
        "baritone_client.common.combat.scan_for_threats",
        lambda *_a, **_k: next(calls),
    )


def test_daytime_returns_immediately(client, monkeypatch):
    monkeypatch.setattr(
        "baritone_client.common.combat.scan_for_threats",
        lambda *_a, **_k: pytest.fail("must not scan in daylight"),
    )
    assert night_peace.night_is_over_or_harmless(client, 500)


def test_one_empty_sweep_is_not_enough(client, monkeypatch):
    """A momentary gap on a hostile world must not send an unarmed bot out."""
    _threats(monkeypatch, [[]])
    assert not night_peace.night_is_over_or_harmless(client, 15000)


def test_consecutive_empty_sweeps_release_the_night(client, monkeypatch):
    _threats(monkeypatch, [[], []])
    assert not night_peace.night_is_over_or_harmless(client, 15000)
    assert night_peace.night_is_over_or_harmless(client, 15000)


def test_a_sighting_resets_the_evidence(client, monkeypatch):
    """Hostile, empty, hostile, empty must never accumulate to a release."""
    _threats(monkeypatch, [[], [{"distance": 8}], [], [{"distance": 5}]])
    for _ in range(4):
        assert not night_peace.night_is_over_or_harmless(client, 15000)


def test_a_failed_scan_is_treated_as_dangerous(client, monkeypatch):
    """Unknown is not the same as empty; that is how a bot dies."""
    def boom(*_a, **_k):
        raise RuntimeError("bridge down")

    monkeypatch.setattr("baritone_client.common.combat.scan_for_threats", boom)
    assert not night_peace.night_is_over_or_harmless(client, 15000)


def test_the_sweep_radius_is_wide_enough_to_mean_something():
    """A short radius would call an ordinary hostile night peaceful."""
    assert night_peace.PEACEFUL_SCAN_RADIUS >= 48
    assert night_peace.PEACEFUL_CONFIRMATIONS >= 2


def test_the_waiter_asks_before_settling_in_for_the_night():
    import inspect

    from baritone_client.common import base

    source = inspect.getsource(base.wait_for_safe_daylight)
    assert "night_is_over_or_harmless(client, day_time)" in source
    # The old unconditional day-only gate must be gone, not merely bypassed.
    assert "if day_time < 12000:" not in source


def test_the_opening_of_a_night_is_never_second_guessed(monkeypatch):
    """Shelter and defence get the first minute uncontested.

    The existing daylight-wait tests hand `scan_for_threats` a fixed sequence
    and assert the bot defends against what the first scan returns. An extra
    sweep here would consume that entry and silently disarm the defence path,
    so nothing is scanned at all until the grace period has elapsed.
    """
    monkeypatch.setattr(
        "baritone_client.common.combat.scan_for_threats",
        lambda *_a, **_k: pytest.fail("must not scan during the grace period"),
    )
    fresh = SimpleNamespace(transport=SimpleNamespace())
    assert not night_peace.night_is_over_or_harmless(fresh, 15000)
    assert not night_peace.night_is_over_or_harmless(fresh, 15000)


def test_daylight_clears_the_evidence_for_the_next_night(client, monkeypatch):
    """A night released yesterday must be re-earned tonight."""
    _threats(monkeypatch, [[], []])
    assert not night_peace.night_is_over_or_harmless(client, 15000)
    assert night_peace.night_is_over_or_harmless(client, 15000)
    assert night_peace.night_is_over_or_harmless(client, 500)
    assert getattr(client, "_peaceful_night_sweeps") == 0
    assert getattr(client, "_peaceful_night_since") == 0.0
