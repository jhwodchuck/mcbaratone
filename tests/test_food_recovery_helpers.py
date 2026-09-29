from types import SimpleNamespace

from baritone_client.automator.food_recovery_state import (
    checkpointed_wheat_farm_origin,
    get_food_search_anchor,
    get_food_search_center,
    record_failed_food_source,
    recover_food_from_known_sources,
    remember_renewable_food_source,
    verified_food_herd_source,
)
from baritone_client.common.food_recovery import (
    bounded_exploration_origin,
    must_hold_for_critical_food,
)
from baritone_client.common import emergency_food


def test_emergency_exploration_rotates_direction_across_recovery_attempts():
    calls = []

    class Transport:
        @staticmethod
        def dispatch(route, payload):
            calls.append((route, payload))
            return {}

    client = SimpleNamespace(transport=Transport())
    state = {"block_position": {"x": 0, "y": 64, "z": 0}}

    first = emergency_food.EmergencyExploration(0, 0, 32)
    first.resume_waypoint_rotation(client)
    first.start(client, state)

    second = emergency_food.EmergencyExploration(0, 0, 32)
    second.resume_waypoint_rotation(client)
    second.start(client, state)

    assert [
        payload for route, payload in calls if route == "explore"
    ] == [
        {"x": 24, "z": 0},
        {"x": 0, "z": 24},
    ]


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


def test_food_search_anchor_prefers_current_base_over_stale_bootstrap_origin():
    state = SimpleNamespace(
        custom_data={
            "base_location": [-413, 78, -15],
            "homestead_anchor": [-433, 78, 0],
            "phase_payloads": {
                "SPAWN_BOOTSTRAP": {"return_home": {"origin": [8, 162, 10]}}
            },
            "survival_recovery": {"food_search_anchor": [8, 162, 10]},
        }
    )

    anchor = get_food_search_anchor(
        None,
        state,
        lambda *_args: {"block_position": {"x": -430, "y": 70, "z": -200}},
    )

    assert anchor == (-413.0, 78.0, -15.0)
    assert state.custom_data["survival_recovery"]["food_search_anchor"] == [
        -413.0,
        78.0,
        -15.0,
    ]


def test_phase_food_search_returns_home_instead_of_rebasing(
    monkeypatch, advancing_clock
):
    from baritone_client.common import combat, inventory

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
                dict(far_state)
                if route == "get_state"
                else {"inventory": [], "armor": [], "offhand": []}
                if route == "get_inventory"
                else {}
            )
        )
    )
    returns = []

    monkeypatch.setattr(inventory, "time", advancing_clock())

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


def test_legacy_crop_checkpoint_is_reused_without_becoming_an_animal_herd():
    crop_source = {
        "type": "starter_crop_farm",
        "location": [-426, 79, -20],
        "verified": True,
        "plots": [[-429, 80, -23, "minecraft:wheat"]],
    }
    state = SimpleNamespace(
        checkpoint_dir=None,
        custom_data={
            "farm_location": [-426, 79, -20],
            "structures": {"food_source": crop_source},
        },
    )
    calls = []

    recovered = recover_food_from_known_sources(
        SimpleNamespace(transport=SimpleNamespace(dispatch=lambda *_a: {})),
        state,
        {"cow": ("minecraft:beef", "minecraft:cooked_beef")},
        harvest_fn=lambda _client, x, y, z: calls.append(
            ("harvest", x, y, z)
        )
        or True,
        eat_fn=lambda *_args, **_kwargs: calls.append(("eat",)) or True,
        visit_herd_fn=lambda *_args, **_kwargs: calls.append(("herd",)) or True,
    )

    assert checkpointed_wheat_farm_origin(state) == (-426, 79, -20)
    assert verified_food_herd_source(
        state, {"cow": ("minecraft:beef", "minecraft:cooked_beef")}
    ) == {}
    assert recovered
    assert calls == [("harvest", -426, 79, -20), ("eat",)]


def test_retired_legacy_crop_checkpoint_is_not_retried_for_food_recovery():
    state = SimpleNamespace(
        custom_data={
            "farm_location": [-426, 79, -20],
            "structures": {
                "food_source": {
                    "type": "starter_crop_farm",
                    "location": [-426, 79, -20],
                    "verified": False,
                    "failed_visits": 2,
                }
            },
        }
    )

    assert checkpointed_wheat_farm_origin(state) is None


def test_crop_landmark_is_not_reinterpreted_as_a_cow_herd():
    source = {
        "type": "starter_crop_farm",
        "location": [5, 64, 5],
        "verified": False,
        "failed_visits": 2,
    }
    state = SimpleNamespace(
        checkpoint_dir=None,
        custom_data={
            "structures": {"food_source": source},
            "locations": {
                "farm": [
                    {"x": 5, "y": 64, "z": 5, "tags": ["food", "crops"]}
                ]
            },
        },
    )
    visits = []

    recovered = recover_food_from_known_sources(
        SimpleNamespace(
            transport=SimpleNamespace(
                dispatch=lambda *_a: {"block_position": {"x": 0, "y": 64, "z": 0}}
            )
        ),
        state,
        {"cow": ("minecraft:beef", "minecraft:cooked_beef")},
        harvest_fn=lambda *_args: False,
        eat_fn=lambda *_args, **_kwargs: False,
        visit_herd_fn=lambda *_args, **_kwargs: visits.append(True) or False,
    )

    assert not recovered
    assert visits == []
    assert source["failed_visits"] == 2


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

    client = SimpleNamespace(
        transport=SimpleNamespace(
            dispatch=lambda *_args: {
                "block_position": {"x": 200, "y": 64, "z": -100}
            }
        )
    )

    def visit(active_client, _requirements, animal, **kwargs):
        assert active_client._safe_recovery_navigation_depth == 1
        calls.append(
            (animal, kwargs["location"], kwargs["preserve_breeding_pair"])
        )
        return True

    recovered = recover_food_from_known_sources(
        client,
        state,
        {"pig": ("minecraft:porkchop", "minecraft:cooked_porkchop"),
         "cow": ("minecraft:beef", "minecraft:cooked_beef")},
        withdraw_fn=lambda *_args, **_kwargs: -1,
        eat_fn=lambda *_args, **_kwargs: calls.append(("eat",)) or len(calls) > 1,
        visit_herd_fn=visit,
    )

    assert recovered
    # Not starving: the last pair stays (live A1 lost every herd to this path).
    assert calls == [("pig", [148, 65, -143], True), ("eat",)]
    assert client._safe_recovery_navigation_depth == 0

    calls.clear()
    client.transport.dispatch = lambda *_args: {
        "block_position": {"x": 200, "y": 64, "z": -100},
        "food_level": 4,
    }
    assert recover_food_from_known_sources(
        client,
        state,
        {"pig": ("minecraft:porkchop", "minecraft:cooked_porkchop"),
         "cow": ("minecraft:beef", "minecraft:cooked_beef")},
        withdraw_fn=lambda *_args, **_kwargs: -1,
        eat_fn=lambda *_args, **_kwargs: calls.append(("eat",)) or len(calls) > 1,
        visit_herd_fn=visit,
    )
    assert calls[0] == ("pig", [148, 65, -143], False)  # starving: eat the pair


def test_known_food_recovery_marks_storage_open_as_survival_recovery():
    calls = []
    state = SimpleNamespace(checkpoint_dir="checkpoint", custom_data={})
    client = SimpleNamespace(transport=SimpleNamespace(dispatch=lambda *_args: {}))

    def withdraw(active_client, requirements, **kwargs):
        calls.append((active_client, requirements, kwargs))
        return 1

    assert recover_food_from_known_sources(
        client,
        state,
        {"cow": ("minecraft:beef", "minecraft:cooked_beef")},
        withdraw_fn=withdraw,
        eat_fn=lambda *_args, **_kwargs: True,
    )
    assert calls[0][0] is client
    assert calls[0][2]["allow_recovery_access"] is True
    assert client._safe_recovery_navigation_depth == 0


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
    # Isolate the pre-existing reach_dry_surface path from the fast
    # surface-for-air call now tried ahead of it (covered by its own tests);
    # this transport mock returns {} for every dispatch, so an unmocked
    # emergency-surface attempt would run for real against it.
    monkeypatch.setattr(
        "baritone_client.common.combat._surface_after_aquatic_hunt",
        lambda *_args, **_kwargs: False,
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


def test_recovery_hunts_a_visible_herd_when_none_was_persisted():
    """Live A1 2026-09-28: 9 cows in view, no persisted herd, blind search death."""
    cows = [
        {"type": "minecraft:cow", "distance": d, "position": {"x": -449.3, "y": 93.0, "z": -13.2}}
        for d in (31, 39, 40, 45)
    ]

    def dispatch(route, *_args, **_kwargs):
        if route == "get_entities":
            return {"entities": cows}
        return {"block_position": {"x": -423, "y": 80, "z": -3}, "food_level": 8}

    client = SimpleNamespace(transport=SimpleNamespace(dispatch=dispatch))
    state = SimpleNamespace(checkpoint_dir=None, custom_data={})
    calls = []

    def visit(_client, requirements, animal, **kwargs):
        calls.append((animal, kwargs["location"], kwargs["preserve_breeding_pair"], requirements))
        return True

    assert recover_food_from_known_sources(
        client,
        state,
        {"cow": ("minecraft:beef", "minecraft:cooked_beef")},
        withdraw_fn=lambda *_a, **_k: -1,
        eat_fn=lambda *_a, **_k: True,
        visit_herd_fn=visit,
    )
    assert calls == [("cow", [-449, 93, -13], True, {"minecraft:beef": 3})]


def test_a_visible_pair_alone_is_left_unless_starving():
    pair = [
        {"type": "minecraft:cow", "distance": 20, "position": {"x": 1, "y": 70, "z": 1}}
    ] * 2
    food = {"level": 8}

    def dispatch(route, *_args, **_kwargs):
        if route == "get_entities":
            return {"entities": pair}
        return {"block_position": {"x": 0, "y": 70, "z": 0}, "food_level": food["level"]}

    client = SimpleNamespace(transport=SimpleNamespace(dispatch=dispatch))
    state = SimpleNamespace(checkpoint_dir=None, custom_data={})
    kwargs = dict(
        withdraw_fn=lambda *_a, **_k: -1,
        eat_fn=lambda *_a, **_k: True,
        visit_herd_fn=lambda *_a, **_k: True,
    )
    animals = {"cow": ("minecraft:beef", "minecraft:cooked_beef")}
    assert not recover_food_from_known_sources(client, state, animals, **kwargs)
    food["level"] = 4
    assert recover_food_from_known_sources(client, state, animals, **kwargs)
