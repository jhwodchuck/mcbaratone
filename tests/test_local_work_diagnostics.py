"""A scheduling hold must explain itself.

Bot18 -- the fleet's food worker -- reported only "fleet role village_food is
waiting for recurring food production" for hours while standing at 20 health
and 19 food with no hostiles nearby. The message named the role but never the
failing condition, so finding the cause meant reconstructing the gate by hand
against a live bot over RCON. Meanwhile Bot19 sat permanently at 15 health and
13 food, one point under each threshold, unable to regenerate because
Minecraft needs food 18 to heal, and its log said nothing about either number.

These lock in that the reason is always reported.
"""

from baritone_client.automator.adaptive_scheduler import GameSignals


def _healthy(**overrides) -> GameSignals:
    base = dict(
        observed=True,
        entities_observed=True,
        dimension="minecraft:overworld",
        health=20.0,
        food=20,
        nearby_hostiles=0,
        world_time=1000,
    )
    base.update(overrides)
    return GameSignals(**base)


def test_healthy_signals_report_no_blockers():
    signals = _healthy()
    assert signals.local_work_blockers() == ()
    assert signals.safe_for_local_work is True


def test_low_health_is_named_with_its_value():
    """Bot19's exact state: one point under the bar, and never told so."""
    blockers = _healthy(health=15.0, food=13).local_work_blockers()

    assert any("health 15.0<16" in b for b in blockers), blockers
    assert any("food 13<14" in b for b in blockers), blockers


def test_hostiles_and_night_are_named():
    blockers = _healthy(nearby_hostiles=2, world_time=18000).local_work_blockers()

    assert any("2 hostile" in b for b in blockers), blockers
    assert any("night" in b for b in blockers), blockers


def test_missing_snapshots_are_named_not_silent():
    """An unobserved snapshot must not look like a healthy refusal."""
    blockers = GameSignals(observed=False, entities_observed=False).local_work_blockers()

    assert any("no state snapshot" in b for b in blockers), blockers
    assert any("no entity snapshot" in b for b in blockers), blockers


def test_safe_for_local_work_still_agrees_with_the_blocker_list():
    """The boolean must stay a pure function of the reported blockers."""
    for signals in (
        _healthy(),
        _healthy(health=4.0),
        _healthy(food=0),
        _healthy(nearby_hostiles=1),
        _healthy(dimension="minecraft:the_nether"),
        GameSignals(),
    ):
        assert signals.safe_for_local_work == (not signals.local_work_blockers())
