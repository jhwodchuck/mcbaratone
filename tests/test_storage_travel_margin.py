"""Banking cargo must not require the bot to be in perfect condition.

The storage travel gate demanded health >= 18 and food >= 18 -- full health and
full natural-regen capability. Any bot that had recently been hit by anything
failed it. On 2026-08-06 Bot19 (wood_supply) stood 14 blocks from home holding
547 logs it had already cut, and completed 5 deposits all day; Bot07, working
beside its storage, completed 4,249.

Depositing is the risk-reducing move for cargo. A wounded bot that dies on the
way loses what it was going to lose anyway; one that arrives banks it for good.
"""

from baritone_client.common import storage_safety as ss


def _snapshot(health=20.0, food=20, dead=False):
    return {"health": health, "food_level": food, "is_dead": dead}


def test_a_scuffed_bot_may_still_bank_its_cargo():
    """Bot19's condition: hurt and hungry, 14 blocks from home, 547 logs held."""
    assert ss.storage_travel_safe(_snapshot(health=14.0, food=12)) is True


def test_the_old_thresholds_would_have_refused_that_trip():
    """Pins the actual regression, not just the new numbers."""
    assert ss.MIN_STORAGE_TRAVEL_HEALTH < 18.0
    assert ss.MIN_STORAGE_TRAVEL_FOOD < 18


def test_a_bot_that_must_eat_or_flee_first_still_defers():
    """The floor has to exclude someone with a more urgent problem."""
    assert ss.storage_travel_safe(_snapshot(health=4.0, food=20)) is False
    assert ss.storage_travel_safe(_snapshot(health=20.0, food=2)) is False


def test_a_dead_bot_never_travels():
    assert ss.storage_travel_safe(_snapshot(dead=True)) is False
    assert ss.storage_travel_must_abort(_snapshot(dead=True)) is True


def test_starting_and_aborting_use_different_floors():
    """Hysteresis: one shared threshold made trips flap.

    A bot that set out at exactly the limit aborted on its first point of
    damage, healed back, set out again, and never arrived.
    """
    assert ss.ABORT_STORAGE_TRAVEL_HEALTH < ss.MIN_STORAGE_TRAVEL_HEALTH
    assert ss.ABORT_STORAGE_TRAVEL_FOOD < ss.MIN_STORAGE_TRAVEL_FOOD


def test_a_trip_survives_losing_a_little_health_en_route():
    """Below the start floor but above the abort floor: keep going."""
    scuffed = _snapshot(health=ss.MIN_STORAGE_TRAVEL_HEALTH - 1.0, food=20)

    assert ss.storage_travel_safe(scuffed) is False, "would not start here"
    assert ss.storage_travel_must_abort(scuffed) is False, "but must not abort"


def test_a_trip_is_abandoned_when_the_bot_is_actually_in_danger():
    assert ss.storage_travel_must_abort(_snapshot(health=3.0, food=20)) is True
    assert ss.storage_travel_must_abort(_snapshot(health=20.0, food=1)) is True


def test_in_flight_cancel_uses_the_abort_floor(monkeypatch):
    """The on-tick cancel must not use the stricter start floor."""
    cancelled = []

    class _Client:
        def __init__(self, snapshot):
            self.snapshot = snapshot
            self.transport = self

        def dispatch(self, route, _payload=None):
            if route == "cancel":
                cancelled.append(True)
            return self.snapshot

    scuffed = _Client(_snapshot(health=ss.MIN_STORAGE_TRAVEL_HEALTH - 1.0))
    ss.cancel_unsafe_storage_travel(scuffed)
    assert not cancelled, "cancelled a trip over a scratch"

    dying = _Client(_snapshot(health=1.0))
    ss.cancel_unsafe_storage_travel(dying)
    assert cancelled, "failed to cancel while dying"
    assert dying._storage_survival_abort is True


def test_restoring_the_margin_eats_past_the_bare_floor():
    """Eat to comfort so the bot is not back at the floor immediately."""
    assert ss.COMFORTABLE_STORAGE_TRAVEL_FOOD > ss.MIN_STORAGE_TRAVEL_FOOD
