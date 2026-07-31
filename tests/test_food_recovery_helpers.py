from types import SimpleNamespace

from baritone_client.automator.food_recovery_state import (
    get_food_search_anchor,
    get_food_search_center,
    record_failed_food_source,
    recover_food_from_known_sources,
    remember_renewable_food_source,
)
from baritone_client.common.food_recovery import (
    bounded_exploration_origin,
    must_hold_for_critical_food,
)
from baritone_client.common import emergency_food


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
                "food_search_anchor": [-669.0, 64.0, 165.0],
            }
        }
    )

    def read_state(*_args):
        return {"block_position": {"x": -42, "z": 28}}

    assert get_food_search_center(None, state, read_state) == (-42.0, 28.0)
    assert state.custom_data["survival_recovery"]["food_search_anchor"] == [
        -42.0,
        64.0,
        28.0,
    ]


def test_food_search_anchor_prefers_reanchored_spawn_home():
    state = SimpleNamespace(
        custom_data={
            "phase_payloads": {
                "SPAWN_BOOTSTRAP": {
                    "return_home": {"origin": [-160, 63, -320]}
                }
            }
        }
    )

    anchor = get_food_search_anchor(
        None,
        state,
        lambda *_args: {
            "block_position": {"x": -119, "y": 70, "z": -179}
        },
    )

    assert anchor == (-160.0, 63.0, -320.0)
    assert state.custom_data["survival_recovery"]["food_search_anchor"] == [
        -160.0,
        63.0,
        -320.0,
    ]


def test_phase_food_search_returns_home_instead_of_rebasing(monkeypatch):
    from baritone_client.common import combat

    far_state = {
        "health": 20.0,
        "food_level": 6,
        "world_time": 1000,
        "dimension": "minecraft:overworld",
        "block_position": {"x": 150, "y": 64, "z": 20},
    }
    client = SimpleNamespace(
        transport=SimpleNamespace(
            dispatch=lambda route, _payload: (
                dict(far_state) if route == "get_state" else {}
            )
        )
    )
    returns = []

    monkeypatch.setattr(combat, "recover_health", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        combat,
        "prepare_food_search_state",
        lambda _client, _state: dict(far_state),
    )
    monkeypatch.setattr(
        combat,
        "return_to_food_search_anchor",
        lambda _client, anchor: returns.append(anchor) or False,
    )

    assert not combat.acquire_emergency_food(
        client,
        minimum_food=14,
        timeout=0,
        exploration_center=(10.0, 64.0, 20.0),
        return_to_exploration_center=True,
    )
    assert returns == [(10.0, 64.0, 20.0)]


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


def test_known_food_recovery_uses_nearest_persisted_food_landmark():
    calls = []
    state = SimpleNamespace(
        checkpoint_dir=None,
        custom_data={
            "homestead_anchor": [200, 64, -100],
            "locations": {
                "farm": [
                    {"x": 148, "y": 65, "z": -143,
                     "tags": ["food", "pig_herd"]},
                    {"x": 900, "y": 65, "z": 900,
                     "tags": ["food", "cow_herd"]},
                ]
            },
        },
    )

    recovered = recover_food_from_known_sources(
        SimpleNamespace(
            transport=SimpleNamespace(
                dispatch=lambda *_args: {
                    "block_position": {"x": 200, "y": 64, "z": -100}
                }
            )
        ),
        state,
        {"pig": ("minecraft:porkchop", "minecraft:cooked_porkchop"),
         "cow": ("minecraft:beef", "minecraft:cooked_beef")},
        withdraw_fn=lambda *_args, **_kwargs: -1,
        eat_fn=lambda *_args, **_kwargs: calls.append(("eat",)) or len(calls) > 1,
        visit_herd_fn=lambda _client, _requirements, animal, **kwargs:
            calls.append((animal, kwargs["location"], kwargs["preserve_breeding_pair"])) or True,
    )

    assert recovered
    assert calls == [("pig", [148, 65, -143], False), ("eat",)]


def test_submerged_food_search_reaches_dry_surface_even_above_y_floor(
    monkeypatch,
):
    client = SimpleNamespace(transport=SimpleNamespace(dispatch=lambda *_args: {}))
    state = {
        "dimension": "minecraft:overworld",
        "block_position": {"x": 10, "y": 58, "z": 20},
    }
    recovered = []
    monkeypatch.setattr(
        emergency_food,
        "player_is_in_water",
        lambda *_args: True,
    )
    monkeypatch.setattr(
        emergency_food,
        "head_block_is_water",
        lambda *_args: False,
    )
    monkeypatch.setattr(
        "baritone_client.common.surface_recovery.reach_dry_surface",
        lambda _client, **kwargs: recovered.append(kwargs) or (12, 64, 21),
    )

    assert emergency_food.reach_food_search_surface(client, state)
    assert recovered[0]["origin"] == (10, 58, 20)


def test_dry_surface_food_search_does_not_run_surface_recovery(monkeypatch):
    client = SimpleNamespace(transport=SimpleNamespace(dispatch=lambda *_args: {}))
    state = {
        "dimension": "minecraft:overworld",
        "block_position": {"x": 10, "y": 64, "z": 20},
    }
    monkeypatch.setattr(
        emergency_food,
        "player_is_in_water",
        lambda *_args: False,
    )
    monkeypatch.setattr(
        emergency_food,
        "head_block_is_water",
        lambda *_args: False,
    )
    monkeypatch.setattr(
        "baritone_client.common.surface_recovery.reach_dry_surface",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("dry surface must not trigger recovery")
        ),
    )

    assert emergency_food.reach_food_search_surface(client, state)


def test_dry_cave_above_floor_still_ascends_to_real_surface(monkeypatch):
    client = SimpleNamespace(transport=SimpleNamespace(dispatch=lambda *_args: {}))
    state = {
        "dimension": "minecraft:overworld",
        "block_position": {"x": 10, "y": 62, "z": 20},
    }
    recoveries = []
    monkeypatch.setattr(
        emergency_food,
        "surface_y_at",
        lambda *_args, **_kwargs: 84,
        raising=False,
    )
    monkeypatch.setattr(
        emergency_food,
        "player_is_in_water",
        lambda *_args: False,
    )
    monkeypatch.setattr(
        emergency_food,
        "head_block_is_water",
        lambda *_args: False,
    )
    monkeypatch.setattr(
        "baritone_client.common.surface_recovery.reach_dry_surface",
        lambda _client, **kwargs: recoveries.append(kwargs) or (10, 84, 20),
    )

    assert emergency_food.reach_food_search_surface(client, state)
    assert recoveries[0]["expected_y"] == 84


def test_low_dry_ledge_does_not_satisfy_food_surface_recovery(monkeypatch):
    client = SimpleNamespace(transport=SimpleNamespace(dispatch=lambda *_args: {}))
    state = {
        "dimension": "minecraft:overworld",
        "block_position": {"x": 10, "y": 58, "z": 20},
    }
    excavations = []
    monkeypatch.setattr(
        emergency_food,
        "surface_y_at",
        lambda *_args, **_kwargs: 84,
        raising=False,
    )
    monkeypatch.setattr(
        emergency_food,
        "player_is_in_water",
        lambda *_args: True,
    )
    monkeypatch.setattr(
        emergency_food,
        "head_block_is_water",
        lambda *_args: False,
    )
    monkeypatch.setattr(
        "baritone_client.common.surface_recovery.reach_dry_surface",
        lambda *_args, **_kwargs: (12, 64, 21),
    )
    monkeypatch.setattr(
        "baritone_client.common.build_site_recovery.excavate_surface_egress",
        lambda _client, **kwargs: (
            excavations.append(kwargs) or (12, 84, 21)
        ),
    )

    assert emergency_food.reach_food_search_surface(client, state)
    assert excavations[0]["expected_y"] == 84


def test_submerged_food_selection_rejects_distant_fish(monkeypatch):
    """Regression: Bot14 repeatedly drowned following fish 20-28 blocks."""
    from baritone_client.common import combat

    radii = []

    def nearby(_client, radius):
        radii.append(radius)
        return [
            {
                "id": 14,
                "type": "minecraft:cod",
                "distance": 20.9,
            }
        ]

    monkeypatch.setattr(combat, "get_nearby_entities", nearby)

    target = emergency_food.select_target(
        SimpleNamespace(transport=SimpleNamespace(dispatch=lambda *_args: {})),
        [],
        current_food=0,
        elapsed=120.0,
        timeout=240.0,
        renewable_source_callback=None,
        in_water=True,
    )

    assert target is None
    assert radii == [5]
