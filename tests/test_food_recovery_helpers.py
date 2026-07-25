from types import SimpleNamespace

from baritone_client.automator.food_recovery_state import (
    get_food_search_center,
    record_failed_food_source,
    remember_renewable_food_source,
)
from baritone_client.common.food_recovery import (
    bounded_exploration_origin,
    must_hold_for_critical_food,
)


def test_critical_food_search_holds_only_near_death_or_starving():
    # Near-death (below the fixed critical floor) with unstable food -> hold.
    assert must_hold_for_critical_food(
        {"health": 5.0},
        minimum_health=12.0,
        current_food=10,
    )
    # Starving (food<=2) holds regardless of health.
    assert must_hold_for_critical_food(
        {"health": 20.0},
        minimum_health=12.0,
        current_food=0,
    )
    assert not must_hold_for_critical_food(
        {"health": 20.0},
        minimum_health=12.0,
        current_food=6,
    )


def test_recovering_health_below_minimum_but_above_critical_floor_may_explore():
    """Regression: the gate used to fire for the whole recovery attempt.

    Every production call site passes minimum_health=12.0. Health below that
    (but not near death) must NOT hold -- otherwise a bot at, say, 10/20
    health with food 10 can never explore to find food, never eats, and
    never recovers: a permanent soft-lock. This exact state (health=6.3-10,
    food=6-10) was observed live wedging Bot07 and Bot09 indefinitely.
    """
    assert not must_hold_for_critical_food(
        {"health": 10.0},
        minimum_health=12.0,
        current_food=10,
    )
    assert not must_hold_for_critical_food(
        {"health": 6.3},
        minimum_health=12.0,
        current_food=6,
    )


def test_near_death_with_stable_food_does_not_hold():
    # Health is critical but food is comfortably >= 18: natural regen is
    # already in progress, so blind exploration is not needed either.
    assert not must_hold_for_critical_food(
        {"health": 3.0},
        minimum_health=12.0,
        current_food=19,
    )


def test_food_search_center_is_stable_across_retries():
    state = SimpleNamespace(custom_data={})
    positions = iter(
        (
            {"block_position": {"x": 10, "z": 20}},
            {"block_position": {"x": 200, "z": 300}},
        )
    )

    def read_state(*_args):
        return next(positions)

    assert get_food_search_center(None, state, read_state) == (10.0, 20.0)
    assert get_food_search_center(None, state, read_state) == (10.0, 20.0)


def test_food_search_center_rebases_after_distant_respawn():
    state = SimpleNamespace(
        custom_data={
            "survival_recovery": {
                "food_search_center": [-669.0, 165.0],
            }
        }
    )

    def read_state(*_args):
        return {"block_position": {"x": -42, "z": 28}}

    assert get_food_search_center(None, state, read_state) == (-42.0, 28.0)
    assert state.custom_data["survival_recovery"]["food_search_center"] == [
        -42.0,
        28.0,
    ]


def test_bounded_search_rebases_a_center_outside_its_safety_radius():
    assert bounded_exploration_origin(
        {"x": -17, "z": 195},
        (-12, 56),
        96,
    ) == (-17.0, 195.0)


def test_bounded_search_keeps_a_nearby_requested_center():
    assert bounded_exploration_origin(
        {"x": 10, "z": 20},
        (25, 30),
        96,
    ) == (25.0, 30.0)


def test_repeatedly_unharvestable_food_source_is_retired():
    source = {"verified": True}

    assert record_failed_food_source(source) == 1
    assert source["verified"]
    assert record_failed_food_source(source) == 2
    assert not source["verified"]


def test_observed_herd_persistence_is_centralized():
    locations = []
    state = SimpleNamespace(
        custom_data={},
        add_location=lambda *args, **kwargs: locations.append((args, kwargs)),
    )

    source = remember_renewable_food_source(
        state,
        {"pig": ("minecraft:porkchop", "minecraft:cooked_porkchop")},
        "pig",
        (4, 65, -8),
    )

    assert source["verified"]
    assert source["location"] == [4, 65, -8]
    assert state.custom_data["structures"]["food_source"] is source
    assert locations[0][1]["tags"] == [
        "food",
        "pig_herd",
        "renewable",
        "observed",
    ]
