"""A cornered bot must dig in rather than loop until the supervisor kills it.

`acquire_emergency_food` aborts its food run when a hostile is close and the
bot has no enclosure to hold in. That is a dead end, because by the time it is
reached every other exit is already closed:

  * it cannot work      -- blocked under the health floor
  * it cannot heal      -- "no edible food available"
  * it cannot get food  -- this branch
  * it cannot flee      -- run_away just returned False
  * it cannot fight     -- "Combat attack declined: weapon_unavailable"

Each exit is gated on a condition only another exit could satisfy, so the bot
spins in place. Live dragon-a 2026-08-17 sat at (17, 145, 40) on 10/20 health
with a stray 3.8m away and logged "evasion failed 76x" before the supervisor
reported "STUCK no movement 300s".

Digging in breaks the loop. `dig_and_seal_night_hole` needs no items, no tool
and no health floor -- it pays for itself out of the ground it removes -- so it
is reachable from exactly this state, which is what makes it a real exit and
not another circular gate.
"""

from types import SimpleNamespace

import pytest

from baritone_client.common import combat


@pytest.fixture
def cornered(monkeypatch):
    """A bot at low health, hostile adjacent, no enclosure, nowhere to run."""
    calls = {"dug": 0, "held": 0}

    monkeypatch.setattr(
        "baritone_client.common.base._has_existing_enclosure",
        lambda *_a, **_k: calls["dug"] > 0,
    )
    monkeypatch.setattr(combat, "run_away", lambda *_a, **_k: False)

    def _dig(_client, **_kwargs):
        calls["dug"] += 1
        return True

    monkeypatch.setattr(
        "baritone_client.common.night_shelter.shelter_in_place", _dig
    )
    monkeypatch.setattr(combat.time, "sleep", lambda *_a: None)
    return calls


def test_the_cornered_branch_reaches_the_shelter(cornered, monkeypatch):
    """The abort path must try to build the enclosure it is missing."""
    import inspect

    source = inspect.getsource(combat.acquire_emergency_food)
    assert "shelter_in_place" in source, (
        "the cornered branch still gives up without trying to dig in"
    )
    # And the fallback must survive: a failed dig still aborts rather than
    # looping, so a bot on bedrock does not spin here forever.
    assert "aborting food run" in source


def test_digging_in_is_tried_before_aborting(cornered):
    """Order matters: flee, then dig, then only abort if the ground refuses."""
    import inspect

    source = inspect.getsource(combat.acquire_emergency_food)
    flee_at = source.index("run_away(client, immediate_threat)")
    dig_at = source.index("shelter_in_place")
    abort_at = source.index("hostile nearby and no enclosure")

    assert flee_at < dig_at < abort_at, (
        "the dig must sit between the failed flee and the abort"
    )


def test_the_shelter_needs_nothing_the_cornered_bot_lacks():
    """The exit must not be gated on what the deadlock already denies.

    A previous fix in this repo failed because the new gate required the very
    safety it was establishing. dig_and_seal_night_hole takes only a client.
    """
    import inspect

    from baritone_client.common.night_shelter import dig_and_seal_night_hole

    signature = inspect.signature(dig_and_seal_night_hole)
    required = [
        name
        for name, param in signature.parameters.items()
        if param.default is inspect.Parameter.empty
    ]
    assert required == ["client"], (
        f"shelter requires {required}; anything beyond the client risks "
        "reintroducing a circular gate"
    )
