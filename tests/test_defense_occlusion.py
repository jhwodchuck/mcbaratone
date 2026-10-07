from types import SimpleNamespace

import pytest

from baritone_client.common import combat
from baritone_client.common.defense import assess_threats


PLAYER = {
    "entity_id": 99,
    "block_position": {"x": 0, "y": 80, "z": 0},
    "health": 20,
    "food_level": 20,
    "armor_count": 4,
}
GROUNDED_PLAYER = {
    **PLAYER,
    "is_on_ground": True,
    "velocity": {"x": 0, "y": -0.0784000015258789, "z": 0},
}


def sheltered_mob(kind="skeleton", distance=18):
    return {
        "id": 42,
        "type": f"minecraft:{kind}",
        "distance": distance,
        "position": {"x": distance, "y": 80, "z": 0},
        "velocity": {"x": 0, "y": 0, "z": 0},
        "is_aggressive": False,
        "can_see_player": False,
    }


@pytest.mark.parametrize("kind,distance", [("skeleton", 18), ("zombie", 8), ("husk", 12)])
def test_occluded_calm_nonclosing_mobs_do_not_displace_sheltered_work(kind, distance):
    assert assess_threats([sheltered_mob(kind, distance)], PLAYER) == []


def vertical_cave_mob(*, mob_y=14):
    return {
        **sheltered_mob("skeleton", 10),
        "position": {"x": 8, "y": mob_y, "z": 0},
        "velocity": {"x": -0.2, "y": 0, "z": 0},
    }


def grounded_surface_player(*, on_ground=True):
    return {
        **PLAYER,
        "block_position": {"x": 0, "y": 20, "z": 0},
        "velocity": {"x": 0, "y": -0.0784, "z": 0},
        "is_on_ground": on_ground,
    }


def test_distant_calm_occluded_cave_mob_does_not_preempt_grounded_surface_work():
    # The mob is moving toward the player, but remains six blocks below.
    assert assess_threats(
        [vertical_cave_mob()], grounded_surface_player()
    ) == []


def test_vertical_suppression_requires_six_blocks_of_separation():
    mob = vertical_cave_mob(mob_y=14.01)

    assert assess_threats([mob], grounded_surface_player())


@pytest.mark.parametrize("on_ground", [False, None])
def test_vertical_suppression_requires_explicit_grounded_player(on_ground):
    assert assess_threats(
        [vertical_cave_mob()], grounded_surface_player(on_ground=on_ground)
    )


@pytest.mark.parametrize("velocity", [None, {}, {"x": 0, "y": 0, "z": float("nan")}])
def test_vertical_suppression_requires_complete_finite_player_velocity(velocity):
    player = {**grounded_surface_player(), "velocity": velocity}

    assert assess_threats([vertical_cave_mob()], player)


def test_vertical_suppression_rechecks_fresh_visibility_and_aggression():
    mob = vertical_cave_mob()
    player = grounded_surface_player()
    assert assess_threats([mob], player) == []

    mob["can_see_player"] = True
    assert assess_threats([mob], player)

    mob["can_see_player"] = False
    mob["is_aggressive"] = True
    assert assess_threats([mob], player)


def test_same_level_calm_occluded_mob_approaching_player_stays_actionable():
    mob = sheltered_mob("skeleton", 10)
    mob["velocity"]["x"] = -0.2

    assert assess_threats([mob], PLAYER)


@pytest.mark.parametrize(
    "change",
    [
        {"can_see_player": True},
        {"can_see_player": None},
        {"can_see_player": 0},
        {"is_aggressive": True},
        {"is_aggressive": None},
        {"target_id": 99},
        {"distance": 5},
        {"distance": "invalid"},
        {"distance": float("inf")},
        {"velocity": {"x": -0.2, "y": 0, "z": 0}},
        {"velocity": {}},
        {"velocity": {"x": "invalid", "y": 0, "z": 0}},
        {"position": {}},
        {"type": "minecraft:creeper", "distance": 5},
        {"type": "minecraft:creeper", "can_see_player": None},
        {"type": "minecraft:creeper", "is_aggressive": None},
        {"type": "minecraft:creeper", "angry_at_player": True},
        {"type": "minecraft:creeper", "is_attacking": True},
        {"type": "minecraft:creeper", "target_id": 101},
        {"type": "minecraft:warden"},
        {"type": "minecraft:vex"},
        {"type": "minecraft:arrow", "distance": 1},
    ],
)
def test_occlusion_exception_does_not_hide_actionable_or_uncertain_threats(change):
    mob = dict(sheltered_mob(), **change)
    assert assess_threats([mob], PLAYER)


def test_distant_stationary_occluded_calm_creeper_does_not_force_evasion():
    creeper = sheltered_mob("creeper", 9.2)

    assert assess_threats([creeper], GROUNDED_PLAYER) == []


def test_grounded_gravity_does_not_make_distant_creeper_look_closing():
    player = {
        "entity_id": 99,
        "block_position": {"x": 7, "y": 12, "z": 7},
        "velocity": {"x": 0, "y": -0.0784000015258789, "z": 0},
        "is_on_ground": True,
        "health": 20,
    }
    creeper = {
        "id": 42,
        "type": "minecraft:creeper",
        "distance": 15.556,
        "position": {"x": 0, "y": 0, "z": 0},
        "velocity": {"x": 0, "y": 0, "z": 0},
        "is_aggressive": False,
        "can_see_player": False,
    }

    assert assess_threats([creeper], player) == []


def test_occluded_creeper_still_threatens_when_moving_toward_player():
    creeper = sheltered_mob("creeper", 9.2)
    creeper["velocity"]["x"] = -0.2

    assert assess_threats([creeper], GROUNDED_PLAYER)


def test_occluded_creeper_still_threatens_when_moving_sideways():
    creeper = sheltered_mob("creeper", 9.2)
    creeper["velocity"]["z"] = 0.1

    assert assess_threats([creeper], GROUNDED_PLAYER)


def test_airborne_player_descent_keeps_creeper_actionable():
    player = {
        **GROUNDED_PLAYER,
        "is_on_ground": False,
    }
    creeper = {
        **sheltered_mob("creeper", 15.448),
        "position": {"x": 0, "y": 64.552, "z": 0},
    }

    assert assess_threats([creeper], player)


def test_grounded_player_horizontal_approach_keeps_creeper_actionable():
    player = {
        **GROUNDED_PLAYER,
        "velocity": {"x": 0.2, "y": -0.0784000015258789, "z": 0},
    }
    creeper = sheltered_mob("creeper", 9.2)

    assert assess_threats([creeper], player)


@pytest.mark.parametrize(
    "velocity",
    [
        None,
        {},
        {"x": 0, "y": float("nan"), "z": 0},
        {"x": 0, "y": -0.2, "z": 0},
        {"x": 0, "y": 5, "z": 0},
    ],
)
def test_unknown_or_nonfinite_grounded_player_velocity_keeps_creeper_actionable(velocity):
    player = {**GROUNDED_PLAYER, "velocity": velocity}
    creeper = sheltered_mob("creeper", 9.2)

    assert assess_threats([creeper], player)


@pytest.mark.parametrize("field", ["can_see_player", "is_aggressive", "velocity", "position"])
def test_missing_shelter_evidence_keeps_the_existing_defense_policy(field):
    mob = sheltered_mob()
    del mob[field]
    assert assess_threats([mob], PLAYER)


def test_missing_player_geometry_keeps_the_existing_defense_policy():
    assert assess_threats([sheltered_mob()], {})


def test_defense_keeps_working_inside_shelter_without_mutating_or_equipping():
    class Transport:
        def dispatch(self, route, payload):
            if route == "get_combat_snapshot":
                return {"player": PLAYER, "entities": [sheltered_mob()], "skipped_count": 0}
            if route == "get_block":
                return {"id": "minecraft:air"}
            raise AssertionError(f"Sheltered defense must not mutate: {route}")

    client = SimpleNamespace(transport=Transport())
    assert combat.defend_or_flee(client) is False
    assert client._mcbaratone_defense_runtime.mode.value == "clear"
    client._mcbaratone_defense_runtime.entered_at -= 20
    assert combat.defend_or_flee(client) is False


def test_fresh_visibility_observation_reactivates_defense():
    mob = sheltered_mob()
    assert assess_threats([mob], PLAYER) == []
    mob["can_see_player"] = True
    assert assess_threats([mob], PLAYER)[0].entity["id"] == 42
