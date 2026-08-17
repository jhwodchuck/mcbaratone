"""A long return must not throw away the distance it already covered.

`staged_goto` walks a distant exact goal in bounded horizontal legs. It used to
`return False` the moment any single leg failed, which did two damaging things:
it discarded every leg already walked, and it skipped the whole-route fallback
at the bottom of the function -- the attempt most likely to succeed, because it
loads the target column before retrying the exact height.

Measured on A1Bot 2026-08-17, returning from a mine at (-343, 79, 120) to its
starter-house doorway at (-410, 79, -17), 152 blocks away. Both halves of the
defect showed up within four minutes of each other:

    09:56:39  Staging home approach via (-353, 82, 89)...
    09:56:43  Exact staging height was rejected; retrying the column ...
    09:56:43  Sequential task failed at Return to base for protected smelting
              -> died on the first leg, four seconds, never moved

    09:56:56  Staging home approach via (-355, 78, 84)...
    09:57:25  Staging home approach via (-371, 77, 56)...
    09:57:35  Staging home approach via (-386, 82, 28)...
    09:57:49  Staging home approach via (-401, 75, -1)...
    09:58:09  Sequential task failed at Return to base for protected smelting
              -> walked 135 of 152 blocks, then discarded all of it

The phase then restarted the whole return from scratch, every time, and
FOOD_AND_IRON never got to smelt its iron.
"""

import math
from types import SimpleNamespace

import pytest

from baritone_client.common import navigation


MINE = (-343, 79, 120)
DOORWAY = (-410, 79, -17)


def _client(position=MINE):
    """A client that simply reports standing wherever `position` says."""
    holder = {"position": position}

    def dispatch(route, _payload=None):
        if route == "get_state":
            x, y, z = holder["position"]
            return {"block_position": {"x": x, "y": y, "z": z}}
        return {}

    client = SimpleNamespace(transport=SimpleNamespace(dispatch=dispatch))
    client._holder = holder
    return client


def test_a_blocked_first_leg_still_attempts_the_final_approach(monkeypatch):
    """THE four-second failure: it gave up without trying the real fallback."""
    attempts = []

    def navigate(_client, x, y, z, **_kwargs):
        attempts.append(("exact", (x, y, z)))
        return False

    monkeypatch.setattr(
        navigation, "goto_xz",
        lambda _c, x, z, **_k: attempts.append(("column", (x, z))) or False,
    )
    monkeypatch.setattr(navigation, "_loaded_stage_y", lambda _c, _x, y, _z: y)

    navigation.staged_goto(_client(), DOORWAY, MINE, navigate=navigate)

    assert ("exact", DOORWAY) in attempts, (
        "never tried the final exact goal after the first leg was blocked; "
        f"only attempted {attempts}"
    )
    assert ("column", (DOORWAY[0], DOORWAY[2])) in attempts, (
        "never tried loading the target column, which is the fallback most "
        "likely to work"
    )


def test_arriving_next_to_the_door_counts_as_arriving(monkeypatch):
    """THE 135-block discard: standing 6 blocks away was reported as failure."""
    beside_the_door = (DOORWAY[0] + 4, DOORWAY[1], DOORWAY[2] + 4)

    monkeypatch.setattr(navigation, "goto_xz", lambda *_a, **_k: False)
    monkeypatch.setattr(navigation, "_loaded_stage_y", lambda _c, _x, y, _z: y)

    arrived = navigation.staged_goto(
        _client(beside_the_door),
        DOORWAY,
        MINE,
        navigate=lambda *_a, **_k: False,
    )

    gap = math.hypot(
        beside_the_door[0] - DOORWAY[0], beside_the_door[2] - DOORWAY[2]
    )
    assert gap <= navigation.ARRIVAL_RADIUS  # fixture sanity
    assert arrived is True, (
        f"reported failure while standing {gap:.1f} blocks from the goal, "
        "discarding the whole journey"
    )


def test_stopping_far_from_the_goal_is_still_a_failure(monkeypatch):
    """The near-arrival allowance must not become "anywhere is fine".

    A return that never left the mine has to fail, or the caller proceeds to
    open a chest and smelt iron 150 blocks from the furnace.
    """
    monkeypatch.setattr(navigation, "goto_xz", lambda *_a, **_k: False)
    monkeypatch.setattr(navigation, "_loaded_stage_y", lambda _c, _x, y, _z: y)

    arrived = navigation.staged_goto(
        _client(MINE), DOORWAY, MINE, navigate=lambda *_a, **_k: False
    )

    assert arrived is False, "claimed arrival from 152 blocks away"


def test_a_clean_run_still_reports_success(monkeypatch):
    """No regression for the ordinary case where every leg works."""
    monkeypatch.setattr(navigation, "goto_xz", lambda *_a, **_k: True)
    monkeypatch.setattr(navigation, "_loaded_stage_y", lambda _c, _x, y, _z: y)

    assert navigation.staged_goto(
        _client(), DOORWAY, MINE, navigate=lambda *_a, **_k: True
    ) is True


def test_a_short_hop_is_left_to_the_caller(monkeypatch):
    """Under 48 blocks staging is not used at all; that contract is unchanged."""
    near = (MINE[0] + 10, MINE[1], MINE[2] + 10)

    assert navigation.staged_goto(
        _client(), near, MINE, navigate=lambda *_a, **_k: True
    ) is False


def test_the_remaining_gap_is_reported_when_a_leg_is_blocked(monkeypatch, capsys):
    """The log must show whether a return is closing or stuck.

    A route that keeps failing 17 blocks out is a different problem from one
    that never leaves the mine, and the old message could not tell them apart.
    """
    monkeypatch.setattr(navigation, "goto_xz", lambda *_a, **_k: False)
    monkeypatch.setattr(navigation, "_loaded_stage_y", lambda _c, _x, y, _z: y)

    navigation.staged_goto(
        _client(), DOORWAY, MINE, navigate=lambda *_a, **_k: False
    )

    printed = capsys.readouterr().out
    assert "blocks left" in printed, (
        f"no closing-distance reported on a blocked leg; got: {printed!r}"
    )
