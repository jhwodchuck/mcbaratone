"""A blocked bot must eventually leave rather than camp forever.

Bot18, the fleet's food worker, stood healthy and well fed inside a nest of 26
hostiles for hours. It could not satisfy ``nearby_hostiles == 0`` and had no
way to conclude the spot itself was the problem, so it held -- and the fleet
starved down to ten edible items while its food worker waited.

Camping was not merely tolerated, it was unmeasured: ``escalation_level`` was
computed and read by nothing, and the progress ledger only records attempts
that actually ran, so a bot that holds never counted as making no progress.
"""

from types import SimpleNamespace

import pytest

from baritone_client.automator import camp_breaker


def _state():
    return SimpleNamespace(custom_data={})


def _client(x=100, y=64, z=-200):
    return SimpleNamespace(transport=SimpleNamespace(
        dispatch=lambda *_a, **_k: {"block_position": {"x": x, "y": y, "z": z}}
    ))


def test_repeated_identical_holds_accumulate():
    state = _state()
    for expected in range(1, 5):
        assert camp_breaker.record_hold(state, "8 hostile(s) near") == expected


def test_a_changed_blocker_resets_the_streak():
    """A moving situation is not a camp; only an unchanging one is."""
    state = _state()
    camp_breaker.record_hold(state, "8 hostile(s) near")
    camp_breaker.record_hold(state, "8 hostile(s) near")
    assert camp_breaker.record_hold(state, "night (t=13000)") == 1


def test_real_work_clears_the_streak():
    state = _state()
    for _ in range(5):
        camp_breaker.record_hold(state, "food 0<14")
    camp_breaker.clear_holds(state)
    assert camp_breaker.hold_streak(state) == 0
    assert not camp_breaker.should_break_camp(state)


def test_camp_breaks_only_after_the_limit():
    state = _state()
    for _ in range(camp_breaker.CAMP_HOLD_LIMIT - 1):
        camp_breaker.record_hold(state, "8 hostile(s) near")
    assert not camp_breaker.should_break_camp(state)
    camp_breaker.record_hold(state, "8 hostile(s) near")
    assert camp_breaker.should_break_camp(state)


def test_break_camp_relocates_a_bounded_distance():
    state = _state()
    for _ in range(camp_breaker.CAMP_HOLD_LIMIT):
        camp_breaker.record_hold(state, "8 hostile(s) near")

    targets = []
    moved = camp_breaker.break_camp(
        _client(), state,
        goto=lambda _c, x, z, **_k: targets.append((x, z)) or True,
    )

    assert moved is True
    (tx, tz), = targets
    distance = max(abs(tx - 100), abs(tz - (-200)))
    assert distance == camp_breaker.CAMP_BREAK_DISTANCE


def test_successive_breaks_use_different_headings():
    """Fan out instead of retreading one corridor into the same trouble."""
    state = _state()
    headings = {camp_breaker.next_heading(state) for _ in range(4)}
    assert len(headings) == 4


def test_a_successful_break_clears_the_streak():
    state = _state()
    for _ in range(camp_breaker.CAMP_HOLD_LIMIT):
        camp_breaker.record_hold(state, "food 0<14")
    camp_breaker.break_camp(_client(), state, goto=lambda *_a, **_k: True)
    assert camp_breaker.hold_streak(state) == 0


def test_a_failed_break_keeps_the_streak_so_it_retries():
    """Failure must not look like success, or the bot silently re-camps."""
    state = _state()
    for _ in range(camp_breaker.CAMP_HOLD_LIMIT):
        camp_breaker.record_hold(state, "food 0<14")
    camp_breaker.break_camp(_client(), state, goto=lambda *_a, **_k: False)
    assert camp_breaker.should_break_camp(state)


def _attempt(escalation_level):
    return SimpleNamespace(escalation_level=escalation_level)


def test_low_escalation_never_breaks_camp():
    """Below the fleet-aid threshold, this must not touch movement at all."""
    state = _state()
    targets = []

    assert not camp_breaker.self_rescue_if_escalated(
        _client(), state, _attempt(camp_breaker.ESCALATION_SELF_RESCUE_THRESHOLD - 1),
        goto=lambda _c, x, z, **_k: targets.append((x, z)) or True,
    )
    assert targets == []


def test_high_escalation_self_rescues_with_no_fleet_to_ask():
    """mutual_aid.py's own threshold: a solo bot has nobody else to wait for.

    Repeated defensive work must not leave a solo worker waiting forever
    for assistance from a fleet that is not available.
    """
    state = _state()
    targets = []

    moved = camp_breaker.self_rescue_if_escalated(
        _client(), state, _attempt(camp_breaker.ESCALATION_SELF_RESCUE_THRESHOLD),
        goto=lambda _c, x, z, **_k: targets.append((x, z)) or True,
    )

    assert moved is True
    (tx, tz), = targets
    distance = max(abs(tx - 100), abs(tz - (-200)))
    assert distance == camp_breaker.CAMP_BREAK_DISTANCE


def test_self_rescue_failure_is_swallowed_not_raised():
    """A broken relocation must never take the whole scheduler down with it."""
    state = _state()

    def _boom(*_args, **_kwargs):
        raise RuntimeError("bridge unavailable")

    assert not camp_breaker.self_rescue_if_escalated(
        _client(), state, _attempt(camp_breaker.ESCALATION_SELF_RESCUE_THRESHOLD),
        goto=_boom,
    )


def test_measured_relocation_clears_streak_when_route_reports_failure():
    state = _state()
    for _ in range(camp_breaker.CAMP_HOLD_LIMIT):
        camp_breaker.record_hold(state, "food 0<14")
    positions = iter((100, 196))
    client = SimpleNamespace(
        transport=SimpleNamespace(
            dispatch=lambda *_a, **_k: {
                "block_position": {"x": next(positions), "y": 64, "z": -200}
            }
        )
    )

    moved = camp_breaker.break_camp(
        client, state, goto=lambda *_a, **_k: False
    )

    assert moved is True
    assert camp_breaker.hold_streak(state) == 0


def test_note_hold_breaks_camp_at_the_limit():
    state = _state()
    client = _client()
    calls = []
    original = camp_breaker.break_camp
    try:
        camp_breaker.break_camp = lambda *_a, **_k: calls.append(True) or True
        for _ in range(camp_breaker.CAMP_HOLD_LIMIT - 1):
            assert camp_breaker.note_hold(client, state, "8 hostile(s) near") is False
        assert camp_breaker.note_hold(client, state, "8 hostile(s) near") is True
    finally:
        camp_breaker.break_camp = original
    assert len(calls) == 1


def test_unreadable_position_does_not_crash_the_scheduler():
    state = _state()
    blind = SimpleNamespace(transport=SimpleNamespace(dispatch=lambda *_a, **_k: {}))
    assert camp_breaker.break_camp(blind, state, goto=lambda *_a, **_k: True) is False


# -- a relocation must not invent a point in the air (A1 2026-11-08) -----------
def test_break_camp_lets_baritone_choose_the_ground_height(monkeypatch):
    """A1, on a y=107 hilltop, was sent to exact y=107 above y=93 trees."""
    from baritone_client.common import navigation

    columns = []
    monkeypatch.setattr(
        navigation, "goto_xz",
        lambda _c, x, z, **_k: columns.append((x, z)) or True,
    )
    monkeypatch.setattr(
        navigation, "goto",
        lambda *_a, **_k: pytest.fail("an exact-height goal can be mid-air"),
    )

    assert camp_breaker.break_camp(_client(x=-461, y=107, z=85), _state())
    assert columns == [(-365, 85)]


# -- escalation must not relocate after every attempt (A1 2026-11-03..08) ------
COOLDOWN = camp_breaker.ESCALATION_SELF_RESCUE_COOLDOWN


def _escalated():
    return _attempt(camp_breaker.ESCALATION_SELF_RESCUE_THRESHOLD)


def test_self_rescue_does_not_relocate_after_every_failed_attempt():
    """Escalation persists, so A1 broke camp 1,870 times in under six days."""
    state, moves = _state(), []

    def travel(_client, x, z, **_kwargs):
        moves.append((x, z))
        return True

    assert camp_breaker.self_rescue_if_escalated(
        _client(), state, _escalated(), goto=travel, now=1000.0)
    for later in (1001.0, 1000.0 + COOLDOWN - 1):
        assert not camp_breaker.self_rescue_if_escalated(
            _client(), state, _escalated(), goto=travel, now=later)
    assert len(moves) == 1

    assert camp_breaker.self_rescue_if_escalated(
        _client(), state, _escalated(), goto=travel, now=1000.0 + COOLDOWN)
    assert len(moves) == 2


def test_a_failed_self_rescue_also_waits_out_the_cooldown():
    state, tries = _state(), []

    def travel(*_args, **_kwargs):
        tries.append(1)
        return False

    for now in (1000.0, 1010.0):
        assert not camp_breaker.self_rescue_if_escalated(
            _client(), state, _escalated(), goto=travel, now=now)
    assert len(tries) == 1


@pytest.mark.parametrize("stamp", ["soon", 5000.0], ids=["corrupt", "future"])
def test_a_corrupt_or_future_stamp_does_not_block_rescue(stamp):
    state, moves = _state(), []
    state.custom_data["camp_holds"] = {"last_self_rescue_at": stamp}

    def travel(_client, x, z, **_kwargs):
        moves.append((x, z))
        return True

    assert camp_breaker.self_rescue_if_escalated(
        _client(), state, _escalated(), goto=travel, now=1000.0)
    assert len(moves) == 1
