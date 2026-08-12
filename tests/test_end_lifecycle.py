import copy
from types import SimpleNamespace

import pytest

from baritone_client.automator.phases.dragon_fight import DragonFightHandler
from baritone_client.automator.phases import end_game
from baritone_client.automator.state_manager import Phase, StateManager
from baritone_client.common import end, enderman_hunt, nether
from baritone_client.common import dragon_combat


def _frames():
    return [
        {"x": index, "y": 30, "z": 10, "block": "minecraft:end_portal_frame"}
        for index in range(12)
    ]


def _complete_dragon_kit():
    return {
        "inventory": [
            {
                "id": "minecraft:iron_sword",
                "count": 1,
                "slot": 0,
                "damage": 0,
                "max_damage": 250,
            },
            {
                "id": "minecraft:bow",
                "count": 1,
                "slot": 1,
                "damage": 0,
                "max_damage": 384,
            },
            {"id": "minecraft:arrow", "count": 32, "slot": 2},
            {"id": "minecraft:cooked_beef", "count": 6, "slot": 3},
        ],
        "armor": [
            {
                "id": f"minecraft:iron_{piece}",
                "count": 1,
                "damage": 0,
                "max_damage": 250,
            }
            for piece in ("helmet", "chestplate", "leggings", "boots")
        ],
        "offhand": [
            {
                "id": "minecraft:shield",
                "count": 1,
                "slot": 40,
                "damage": 0,
                "max_damage": 336,
            }
        ],
        "selected_slot": 0,
    }


def _ready_end_state(**updates):
    state = {
        "dimension": "minecraft:the_end",
        "health": 20,
        "food_level": 20,
        "attack_cooldown": 1.0,
        "block_position": {"x": 0, "y": 64, "z": 0},
    }
    state.update(updates)
    return state


def test_find_end_portal_requires_actual_frame_ring(monkeypatch):
    client = SimpleNamespace(
        transport=SimpleNamespace(
            dispatch=lambda route, payload: (
                {"found": _frames()} if route == "find_blocks" else {}
            )
        )
    )
    assert end.find_end_portal(client, timeout=1) == (6, 30, 10)


def test_activate_end_portal_verifies_eyes_and_active_blocks():
    calls = []

    def dispatch(route, payload):
        calls.append((route, payload))
        if route == "find_blocks" and payload["blocks"] == [
            "minecraft:end_portal_frame"
        ]:
            return {"found": _frames()}
        if route == "get_block":
            return {"id": "minecraft:end_portal_frame", "state": {"eye": "true"}}
        if route == "find_blocks":
            return {
                "found": [{"x": 6, "y": 30, "z": 10, "block": "minecraft:end_portal"}]
            }
        return {}

    client = SimpleNamespace(transport=SimpleNamespace(dispatch=dispatch))
    assert end.activate_end_portal(client, timeout=1) is True
    assert sum(route == "get_block" for route, _ in calls) == 12
    assert not any(route == "interact_block" for route, _ in calls)


def test_enter_end_portal_moves_into_active_block(monkeypatch):
    dimensions = iter(("minecraft:overworld", "minecraft:the_end"))

    def dispatch(route, payload):
        if route == "find_blocks":
            return {
                "found": [{"x": 4, "y": 31, "z": 8, "block": "minecraft:end_portal"}]
            }
        if route == "get_state":
            return {"dimension": next(dimensions)}
        return {}

    client = SimpleNamespace(transport=SimpleNamespace(dispatch=dispatch))
    moved = []
    monkeypatch.setattr(
        "baritone_client.common.navigation.goto",
        lambda _c, x, y, z, **_k: moved.append((x, y, z)) or True,
    )
    monkeypatch.setattr(end.time, "sleep", lambda _seconds: None)
    assert end.enter_end_portal(client, timeout=1) is True
    assert moved == [(4, 31, 8)]


def test_dragon_fight_targets_entity_ids_and_requires_exit_portal(monkeypatch):
    crystal = {
        "id": 91,
        "type": "minecraft:end_crystal",
        "distance": 3,
        "position": {"x": 1, "y": 65, "z": 2},
    }
    entity_reads = iter(
        (
            {"entities": [crystal], "skipped_count": 0},
            {"entities": [crystal], "skipped_count": 0},
            {"entities": [], "skipped_count": 0},
            {"entities": [], "skipped_count": 0},
        )
    )
    calls = []
    selected_slot = {"value": 0}

    def dispatch(route, payload):
        calls.append((route, payload))
        if route == "get_state":
            return _ready_end_state()
        if route == "get_entities":
            return next(entity_reads)
        if route == "get_inventory":
            kit = _complete_dragon_kit()
            kit["selected_slot"] = selected_slot["value"]
            return kit
        if route == "select_slot":
            selected_slot["value"] = payload["slot"]
            return {"selected": payload["slot"]}
        if route == "get_block":
            return {"id": "minecraft:air"}
        if route == "find_blocks":
            return {
                "found": [{"x": 0, "y": 64, "z": 0, "block": "minecraft:end_portal"}]
            }
        return {}

    client = SimpleNamespace(transport=SimpleNamespace(dispatch=dispatch))
    monkeypatch.setattr(
        "baritone_client.common.navigation.goto", lambda *_a, **_k: True
    )
    monkeypatch.setattr(
        dragon_combat, "fire_best_ranged_attack", lambda *_a, **_k: False
    )
    monkeypatch.setattr(end.time, "sleep", lambda _seconds: None)
    assert end.fight_ender_dragon(client, timeout=1) is True
    assert (
        "attack_entity",
        {"entity_id": 91, "min_cooldown": 0.9},
    ) in calls


def test_dragon_fight_charges_bow_through_shared_ranged_policy(monkeypatch):
    entity_reads = iter(
        (
            {
                "entities": [
                    {
                        "id": 99,
                        "type": "minecraft:ender_dragon",
                        "distance": 30,
                        "position": {"x": 30, "y": 90, "z": 0},
                        "velocity": {"x": 0, "y": 0, "z": 0},
                        "health": 200,
                    }
                ],
                "skipped_count": 0,
            },
            {"entities": [], "skipped_count": 0},
        )
    )
    calls = []
    selected_slot = {"value": 0}

    def dispatch(route, payload):
        calls.append((route, payload))
        if route == "get_state":
            return _ready_end_state()
        if route == "get_entities":
            return next(entity_reads)
        if route == "get_inventory":
            kit = _complete_dragon_kit()
            kit["selected_slot"] = selected_slot["value"]
            return kit
        if route == "select_slot":
            selected_slot["value"] = payload["slot"]
            return {"selected": payload["slot"]}
        if route == "find_blocks":
            return {
                "found": [{"x": 0, "y": 64, "z": 0, "block": "minecraft:end_portal"}]
            }
        if route == "use_item":
            return {
                "holding": True,
                "duration_ms": 1100,
                "held_item": "minecraft:bow",
                "hit": {"type": "miss"},
            }
        return {}

    client = SimpleNamespace(transport=SimpleNamespace(dispatch=dispatch))
    monkeypatch.setattr(
        "baritone_client.common.inventory.select_item", lambda *_a, **_k: True
    )
    monkeypatch.setattr(end.time, "sleep", lambda _seconds: None)

    assert end.fight_ender_dragon(client, timeout=1)
    assert ("use_item", {"duration_ms": 1100}) in calls
    assert not any(route == "attack_entity" for route, _payload in calls)


def test_dragon_fight_stops_below_survival_margin(monkeypatch):
    calls = []
    recovery_attempts = []
    attack_attempts = []

    def dispatch(route, payload):
        calls.append((route, payload))
        if route == "get_state":
            return {
                "dimension": "minecraft:the_end",
                "health": 5,
                "food_level": 20,
                "block_position": {"x": 0, "y": 64, "z": 0},
            }
        if route == "get_entities":
            return {
                "entities": [
                    {
                        "id": 99,
                        "type": "minecraft:ender_dragon",
                        "distance": 20,
                        "position": {"x": 20, "y": 90, "z": 0},
                    }
                ],
                "skipped_count": 0,
            }
        if route == "get_inventory":
            return _complete_dragon_kit()
        return {}

    client = SimpleNamespace(transport=SimpleNamespace(dispatch=dispatch))
    monkeypatch.setattr(
        "baritone_client.common.dragon_combat.heal_if_needed",
        lambda *_a, **_k: recovery_attempts.append(True) or False,
    )
    monkeypatch.setattr(
        dragon_combat,
        "_attack_dragon",
        lambda *_a, **_k: attack_attempts.append(True) or True,
    )

    assert not end.fight_ender_dragon(client, timeout=1)
    assert ("cancel", {}) in calls
    assert recovery_attempts == [True]
    assert attack_attempts == []
    assert not any(route in {"attack_entity", "use_item"} for route, _ in calls)


def test_dragon_fight_rejects_overworld_portal_as_victory():
    calls = []

    def dispatch(route, payload):
        calls.append((route, payload))
        if route == "get_state":
            return {"dimension": "minecraft:overworld", "health": 20}
        if route == "find_blocks":
            return {"found": [{"block": "minecraft:end_portal"}]}
        return {}

    client = SimpleNamespace(transport=SimpleNamespace(dispatch=dispatch))
    assert not end.fight_ender_dragon(client, timeout=1)
    assert not any(route == "find_blocks" for route, _ in calls)


def test_failed_dragon_breath_escape_never_attacks(monkeypatch):
    clock = {"now": 0.0}
    calls = []
    escape_attempts = []
    attack_attempts = []
    entities = [
        {
            "id": 99,
            "type": "minecraft:ender_dragon",
            "distance": 20,
            "position": {"x": 20, "y": 90, "z": 0},
        },
        {
            "id": 7,
            "type": "minecraft:area_effect_cloud",
            "distance": 2,
            "position": {"x": 2, "y": 64, "z": 0},
        },
    ]

    def dispatch(route, payload):
        calls.append((route, payload))
        if route == "get_state":
            return {
                "dimension": "minecraft:the_end",
                "health": 20,
                "food_level": 20,
                "block_position": {"x": 0, "y": 64, "z": 0},
            }
        if route == "get_entities":
            return {"entities": entities, "skipped_count": 0}
        if route == "get_inventory":
            return _complete_dragon_kit()
        return {}

    client = SimpleNamespace(transport=SimpleNamespace(dispatch=dispatch))
    monkeypatch.setattr(
        "baritone_client.common.navigation.goto",
        lambda *_a, **_k: escape_attempts.append(True) or False,
    )
    monkeypatch.setattr(
        dragon_combat,
        "_attack_dragon",
        lambda *_a, **_k: attack_attempts.append(True) or True,
    )
    monkeypatch.setattr(dragon_combat.time, "time", lambda: clock["now"])
    monkeypatch.setattr(
        dragon_combat.time, "sleep", lambda seconds: clock.__setitem__("now", clock["now"] + seconds)
    )
    assert not end.fight_ender_dragon(client, timeout=0.2)
    assert escape_attempts
    assert attack_attempts == []
    assert not any(route in {"attack_entity", "use_item"} for route, _ in calls)


def test_crystal_melee_rechecks_survival_margin_after_approach(monkeypatch):
    calls = []

    def dispatch(route, payload):
        calls.append((route, payload))
        if route == "get_state":
            return {
                "health": 5,
                "food_level": 20,
                "attack_cooldown": 1.0,
                "block_position": {"x": 1, "y": 65, "z": 2},
            }
        if route == "get_inventory":
            return {"inventory": [], "selected_slot": 0}
        if route == "get_block":
            return {"id": "minecraft:air"}
        return {}

    client = SimpleNamespace(transport=SimpleNamespace(dispatch=dispatch))
    crystal = {
        "id": 91,
        "type": "minecraft:end_crystal",
        "distance": 3,
        "position": {"x": 1, "y": 65, "z": 2},
    }
    healthy_before_path = {
        "health": 20,
        "food_level": 20,
        "attack_cooldown": 1.0,
        "block_position": {"x": 0, "y": 64, "z": 0},
    }
    monkeypatch.setattr(dragon_combat, "fire_best_ranged_attack", lambda *_a, **_k: False)
    monkeypatch.setattr("baritone_client.common.navigation.goto", lambda *_a, **_k: True)

    consumed, progressed = dragon_combat._attack_crystal(
        client, crystal, healthy_before_path
    )
    assert consumed is True
    assert progressed is False
    assert not any(route == "attack_entity" for route, _ in calls)


@pytest.mark.parametrize(
    "missing",
    ("armor", "shield", "melee", "bow", "arrows", "reserve_food"),
)
def test_shared_dragon_controller_refuses_incomplete_live_kit(monkeypatch, missing):
    dragon = {
        "id": 99,
        "type": "minecraft:ender_dragon",
        "distance": 20,
        "position": {"x": 20, "y": 90, "z": 0},
    }
    kit = copy.deepcopy(_complete_dragon_kit())
    if missing == "armor":
        kit["armor"].pop()
    elif missing == "shield":
        kit["offhand"] = []
    elif missing == "melee":
        kit["inventory"] = [
            item
            for item in kit["inventory"]
            if item["id"] != "minecraft:iron_sword"
        ]
    elif missing == "bow":
        kit["inventory"] = [
            item for item in kit["inventory"] if item["id"] != "minecraft:bow"
        ]
    elif missing == "arrows":
        next(
            item
            for item in kit["inventory"]
            if item["id"] == "minecraft:arrow"
        )["count"] = 31
    else:
        next(
            item
            for item in kit["inventory"]
            if item["id"] == "minecraft:cooked_beef"
        )["count"] = 5
    calls = []
    attack_attempts = []

    def dispatch(route, payload):
        calls.append((route, payload))
        if route == "get_state":
            return _ready_end_state()
        if route == "get_entities":
            return {"entities": [dragon], "skipped_count": 0}
        if route == "get_inventory":
            return kit
        return {}

    client = SimpleNamespace(transport=SimpleNamespace(dispatch=dispatch))
    monkeypatch.setattr(
        dragon_combat,
        "_attack_dragon",
        lambda *_a, **_k: attack_attempts.append(True) or True,
    )

    assert not end.fight_ender_dragon(client, timeout=1)
    assert [route for route, _payload in calls].count("get_inventory") == 1
    assert attack_attempts == []


@pytest.mark.parametrize(
    "malformation",
    (
        "missing_health",
        "missing_food",
        "text_health",
        "float_food",
        "nan_health",
        "food_out_of_range",
    ),
)
def test_shared_dragon_controller_refuses_invalid_survival_telemetry(
    monkeypatch, malformation
):
    state = _ready_end_state()
    if malformation == "missing_health":
        state.pop("health")
    elif malformation == "missing_food":
        state.pop("food_level")
    elif malformation == "text_health":
        state["health"] = "twenty"
    elif malformation == "float_food":
        state["food_level"] = 20.0
    elif malformation == "nan_health":
        state["health"] = float("nan")
    else:
        state["food_level"] = 21
    calls = []
    attack_attempts = []

    def dispatch(route, payload):
        calls.append((route, payload))
        if route == "get_state":
            return dict(state)
        if route == "get_entities":
            return {
                "entities": [
                    {
                        "id": 99,
                        "type": "minecraft:ender_dragon",
                        "distance": 20,
                        "position": {"x": 20, "y": 90, "z": 0},
                    }
                ],
                "skipped_count": 0,
            }
        if route == "get_inventory":
            return _complete_dragon_kit()
        return {}

    client = SimpleNamespace(transport=SimpleNamespace(dispatch=dispatch))
    monkeypatch.setattr(
        dragon_combat,
        "_attack_dragon",
        lambda *_a, **_k: attack_attempts.append(True) or True,
    )

    assert not end.fight_ender_dragon(client, timeout=1)
    assert ("cancel", {}) in calls
    assert not any(route == "get_inventory" for route, _payload in calls)
    assert attack_attempts == []


def test_shared_dragon_controller_rejects_three_incomplete_entity_frames(
    monkeypatch,
):
    calls = []
    attack_attempts = []

    def dispatch(route, payload):
        calls.append((route, payload))
        if route == "get_state":
            return _ready_end_state()
        if route == "get_entities":
            return {
                "entities": [
                    {
                        "id": 99,
                        "type": "minecraft:ender_dragon",
                        "distance": 20,
                        "position": {"x": 20, "y": 90, "z": 0},
                    }
                ],
                "skipped_count": 1,
            }
        return {}

    client = SimpleNamespace(transport=SimpleNamespace(dispatch=dispatch))
    monkeypatch.setattr(dragon_combat.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        dragon_combat,
        "_attack_dragon",
        lambda *_a, **_k: attack_attempts.append(True) or True,
    )

    assert not end.fight_ender_dragon(client, timeout=1)
    assert [route for route, _payload in calls].count("get_entities") == 3
    assert [route for route, _payload in calls].count("cancel") == 3
    assert not any(route == "get_inventory" for route, _payload in calls)
    assert attack_attempts == []


def test_crystal_cage_opening_requires_verified_bar_removal(monkeypatch):
    clock = {"now": 0.0}
    target = (20, 65, 2)
    crystal = {"position": {"x": 20, "y": 65, "z": 2}}
    position = {"value": {"x": 0, "y": 64, "z": 0}}
    calls = []

    def dispatch(route, payload):
        calls.append((route, payload))
        if route == "get_state":
            return _ready_end_state(block_position=dict(position["value"]))
        if route == "get_block":
            return {"id": "minecraft:iron_bars"}
        return {}

    client = SimpleNamespace(transport=SimpleNamespace(dispatch=dispatch))

    def navigate(_client, x, y, z, **_kwargs):
        position["value"] = {"x": x, "y": y, "z": z}
        return True

    monkeypatch.setattr(
        "baritone_client.common.navigation.goto", navigate
    )
    monkeypatch.setattr(dragon_combat.time, "time", lambda: clock["now"])
    monkeypatch.setattr(
        dragon_combat.time,
        "sleep",
        lambda seconds: clock.__setitem__("now", clock["now"] + seconds),
    )

    assert not dragon_combat._open_crystal_cage(
        client, [target], crystal, _ready_end_state()
    )
    assert clock["now"] >= 10.0
    assert calls.count(
        (
            "dig_block",
            {"x": target[0], "y": target[1], "z": target[2], "max_ticks": 200},
        )
    ) == 1
    assert calls[-1] == ("cancel", {})


def test_crystal_cage_opening_accepts_observed_bar_removal(monkeypatch):
    target = (20, 65, 2)
    crystal = {"position": {"x": 20, "y": 65, "z": 2}}
    position = {"value": {"x": 0, "y": 64, "z": 0}}
    calls = []

    def dispatch(route, payload):
        calls.append((route, payload))
        if route == "get_state":
            return _ready_end_state(block_position=dict(position["value"]))
        if route == "get_block":
            return {"id": "minecraft:air"}
        return {}

    client = SimpleNamespace(transport=SimpleNamespace(dispatch=dispatch))

    def navigate(_client, x, y, z, **_kwargs):
        position["value"] = {"x": x, "y": y, "z": z}
        return True

    monkeypatch.setattr(
        "baritone_client.common.navigation.goto", navigate
    )

    assert dragon_combat._open_crystal_cage(
        client, [target], crystal, _ready_end_state()
    )
    assert (
        "dig_block",
        {"x": 20, "y": 65, "z": 2, "max_ticks": 200},
    ) in calls
    assert ("cancel", {}) in calls
    assert position["value"] == {"x": 0, "y": 64, "z": 0}


def test_legacy_dragon_fight_handler_executes_shared_controller(monkeypatch):
    controller_calls = []
    refreshes = []
    client = SimpleNamespace(
        transport=SimpleNamespace(
            dispatch=lambda route, payload: (
                {"dimension": "minecraft:the_end"} if route == "get_state" else {}
            )
        )
    )
    resources = SimpleNamespace(
        refresh_inventory=lambda: refreshes.append(True),
        get_summary=lambda: {"inventory": {"minecraft:dragon_egg": 1}},
    )
    monkeypatch.setattr(
        "baritone_client.automator.phases.dragon_fight.fight_ender_dragon",
        lambda _client, timeout=1200: controller_calls.append(timeout) or True,
    )

    result = DragonFightHandler().execute(client, resources, SimpleNamespace())

    assert result.success
    assert result.reason == "Ender Dragon defeated"
    assert controller_calls == [1200]
    assert refreshes == [True]


def test_world_unlock_persists_real_milestones(monkeypatch, tmp_path):
    state = StateManager(tmp_path)
    handler = end_game.WorldUnlockHandler()
    client = SimpleNamespace(
        transport=SimpleNamespace(
            dispatch=lambda route, payload: {
                "block_position": {"x": 0, "y": 64, "z": 0},
                "dimension": "minecraft:overworld",
            }
        )
    )
    monkeypatch.setattr(end_game, "triangulate_stronghold", lambda _client: (100, 200))
    monkeypatch.setattr(end_game, "enter_end_portal", lambda *_a, **_k: True)
    monkeypatch.setattr(end_game, "fight_ender_dragon", lambda *_a, **_k: True)
    monkeypatch.setattr(handler, "_ensure_end_fight_readiness", lambda *_a: True)

    resources = SimpleNamespace(
        refresh_inventory=lambda: {},
        get_summary=lambda: {"inventory": {}},
    )
    assert handler._locate_stronghold(client, state, resources)
    state.custom_data["end_portal"] = [101, 30, 201]
    assert handler._enter_end(client, state, resources)
    assert handler._kill_dragon(client, state, resources)
    assert state.custom_data["stronghold_coords"] == [100, 200]
    assert state.custom_data["milestones"] == {
        "end_entered": True,
        "dragon_defeated": True,
    }
    payload = state.get_phase_payload(Phase.WORLD_UNLOCK)
    assert payload["stronghold_coords"] == [100, 200]
    assert payload["dragon_defeated"] is True


def test_world_unlock_refuses_end_entry_without_dragon_kit(monkeypatch, tmp_path):
    state = StateManager(tmp_path)
    state.custom_data["end_portal"] = [1, 30, 1]
    handler = end_game.WorldUnlockHandler()
    entered = []
    monkeypatch.setattr(handler, "_ensure_end_fight_readiness", lambda *_a: False)
    monkeypatch.setattr(
        end_game, "enter_end_portal", lambda *_a, **_k: entered.append(True) or True
    )

    assert not handler._enter_end(SimpleNamespace(), state, SimpleNamespace())
    assert entered == []


def test_end_entry_readiness_requires_exact_shared_dragon_kit(monkeypatch):
    handler = end_game.WorldUnlockHandler()
    client = SimpleNamespace()
    monkeypatch.setattr(
        end_game.combat_readiness,
        "ensure_combat_readiness",
        lambda *_args: True,
    )
    monkeypatch.setattr(
        end_game,
        "withdraw_required_from_catalog",
        lambda *_args, **_kwargs: 0,
    )
    monkeypatch.setattr(
        end_game,
        "ensure_supplies",
        lambda *_args, **_kwargs: SimpleNamespace(success=True),
    )
    monkeypatch.setattr(
        "baritone_client.common.combat_ranged.ranged_loadout_ready",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        "baritone_client.common.dragon_combat.dragon_kit_provision_requirements",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        "baritone_client.common.dragon_combat.dragon_kit_ready",
        lambda *_args, **_kwargs: False,
    )

    assert not handler._ensure_end_fight_readiness(client, SimpleNamespace())


def test_dragon_kit_provisioning_replaces_depleted_gear_and_raw_food():
    kit = _complete_dragon_kit()
    sword = next(item for item in kit["inventory"] if item["id"].endswith("sword"))
    bow = next(item for item in kit["inventory"] if item["id"] == "minecraft:bow")
    shield = kit["offhand"][0]
    sword["damage"] = sword["max_damage"] - 10
    bow["damage"] = bow["max_damage"] - 10
    shield["damage"] = shield["max_damage"] - 10
    kit["inventory"] = [
        item
        for item in kit["inventory"]
        if item["id"] not in {"minecraft:arrow", "minecraft:cooked_beef"}
    ]
    kit["inventory"].extend(
        (
            {"id": "minecraft:spectral_arrow", "count": 30, "slot": 2},
            {"id": "minecraft:beef", "count": 20, "slot": 3},
        )
    )
    client = SimpleNamespace(
        transport=SimpleNamespace(
            dispatch=lambda route, _payload: kit if route == "get_inventory" else {}
        )
    )

    assert dragon_combat.dragon_kit_provision_requirements(client) == {
        "minecraft:bow": 2,
        "minecraft:shield": 2,
        "minecraft:iron_sword": 2,
        "minecraft:arrow": 2,
        "minecraft:bread": 6,
    }


def test_dragon_kit_uses_durable_backup_instead_of_depleted_upgrade():
    kit = _complete_dragon_kit()
    kit["inventory"].append(
        {
            "id": "minecraft:netherite_sword",
            "count": 1,
            "slot": 4,
            "damage": 2025,
            "max_damage": 2031,
        }
    )
    client = SimpleNamespace(
        transport=SimpleNamespace(dispatch=lambda _route, _payload: kit)
    )

    assert dragon_combat.dragon_kit_ready(client)
    assert dragon_combat.dragon_kit_provision_requirements(client) == {}


def test_world_unlock_acquires_missing_pearls_before_crafting_eyes(
    monkeypatch, tmp_path
):
    inventory = {
        "minecraft:ender_eye": 0,
        "minecraft:ender_pearl": 0,
        "minecraft:blaze_rod": 6,
        "minecraft:blaze_powder": 0,
    }
    state = StateManager(tmp_path)
    resources = SimpleNamespace(
        refresh_inventory=lambda: None,
        get_summary=lambda: {"inventory": dict(inventory)},
    )
    calls = []
    monkeypatch.setattr(
        end_game,
        "count_item",
        lambda _client, item: inventory.get(item, 0),
    )
    monkeypatch.setattr(
        end_game,
        "withdraw_required_from_catalog",
        lambda *_args, **_kwargs: 0,
    )
    # This test predates the naked-rearm gate and asserts pearl acquisition,
    # not equipment. Declare the bot already combat-ready.
    monkeypatch.setattr(
        end_game.WorldUnlockHandler,
        "_ensure_combat_readiness",
        staticmethod(lambda *_a, **_k: True),
    )

    def hunt(_client, target_count, timeout):
        calls.append(("hunt", target_count, timeout))
        inventory["minecraft:ender_pearl"] = target_count
        return target_count

    def ensure(_client, requirements, timeout):
        calls.append(("ensure", requirements, timeout))
        inventory["minecraft:ender_eye"] = 12
        return SimpleNamespace(success=True)

    monkeypatch.setattr(end_game, "hunt_endermen", hunt)
    monkeypatch.setattr(end_game, "ensure_supplies", ensure)

    assert end_game.WorldUnlockHandler()._craft_eyes(
        SimpleNamespace(), state, resources
    )
    assert calls == [
        ("hunt", 12, 1200),
        ("ensure", {"minecraft:blaze_powder": 12}, 120),
        ("ensure", {"minecraft:ender_eye": 12}, 120),
    ]
    assert state.get_phase_payload(Phase.WORLD_UNLOCK)["eyes_ready"] == 12


def test_world_unlock_fails_fast_when_blaze_supply_was_lost(monkeypatch, tmp_path):
    inventory = {
        "minecraft:ender_eye": 0,
        "minecraft:ender_pearl": 12,
        "minecraft:blaze_rod": 0,
        "minecraft:blaze_powder": 0,
    }
    monkeypatch.setattr(
        end_game,
        "count_item",
        lambda _client, item: inventory.get(item, 0),
    )
    monkeypatch.setattr(
        end_game,
        "withdraw_required_from_catalog",
        lambda *_args, **_kwargs: 0,
    )
    monkeypatch.setattr(
        end_game,
        "ensure_supplies",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("missing blaze supply must not enter recipe retries")
        ),
    )
    state = StateManager(tmp_path)

    assert not end_game.WorldUnlockHandler()._craft_eyes(
        SimpleNamespace(), state, SimpleNamespace()
    )


def test_enderman_hunt_target_is_total_inventory_not_additional(monkeypatch):
    client = SimpleNamespace(
        transport=SimpleNamespace(
            dispatch=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("an already satisfied target must not start a hunt")
            )
        )
    )
    monkeypatch.setattr(enderman_hunt, "count_item", lambda *_args: 12)

    assert enderman_hunt.hunt_endermen(client, target_count=12, timeout=1) == 12


def test_gateway_traversal_requires_verified_displacement(monkeypatch):
    states = iter(
        (
            {
                "dimension": "minecraft:the_end",
                "block_position": {"x": 0, "y": 64, "z": 0},
            },
            {
                "dimension": "minecraft:the_end",
                "block_position": {"x": 1000, "y": 70, "z": 1000},
            },
        )
    )

    def dispatch(route, payload):
        if route == "get_state":
            return next(states)
        if route == "find_blocks":
            return {
                "found": [{"x": 80, "y": 75, "z": 0, "block": "minecraft:end_gateway"}]
            }
        return {}

    client = SimpleNamespace(transport=SimpleNamespace(dispatch=dispatch))
    monkeypatch.setattr(
        "baritone_client.common.inventory.select_item", lambda *_a: True
    )
    monkeypatch.setattr(
        "baritone_client.common.navigation.goto", lambda *_a, **_k: True
    )
    assert end.traverse_end_gateway(client, timeout=1) == (80, 75, 0)


def test_end_city_discovery_requires_purpur_cluster():
    purpur = [
        {"x": i, "y": 70, "z": 30, "block": "minecraft:purpur_block"} for i in range(20)
    ]

    def dispatch(route, payload):
        if route == "get_state":
            return {"block_position": {"x": 0, "y": 64, "z": 0}}
        if route == "find_blocks":
            return {"found": purpur}
        return {}

    client = SimpleNamespace(transport=SimpleNamespace(dispatch=dispatch))
    assert end.find_end_city(client, timeout=1) == (10, 70, 30)


def test_elytra_acquisition_attacks_exact_item_frame(monkeypatch):
    inventory = {"minecraft:elytra": 0}
    calls = []

    def dispatch(route, payload):
        calls.append((route, payload))
        if route == "get_entities":
            return {
                "entities": [
                    {
                        "id": 44,
                        "type": "minecraft:item_frame",
                        "distance": 2,
                        "position": {"x": 4, "y": 80, "z": 5},
                    }
                ]
            }
        if route == "attack_entity":
            inventory["minecraft:elytra"] = 1
        return {}

    client = SimpleNamespace(transport=SimpleNamespace(dispatch=dispatch))
    monkeypatch.setattr(
        "baritone_client.common.inventory.count_item",
        lambda _client, item: inventory.get(item, 0),
    )
    monkeypatch.setattr(
        "baritone_client.common.navigation.goto", lambda *_a, **_k: True
    )
    monkeypatch.setattr(end.time, "sleep", lambda _seconds: None)
    assert end.acquire_elytra(client, timeout=1)
    assert ("attack_entity", {"entity_id": 44}) in calls


def test_naked_bot_rearms_before_hunting_instead_of_stalling(monkeypatch, tmp_path):
    """The armor gate must trigger a rearm, not silently stall the phase.

    Blocking a naked bot from hunting is only half the repair: without an
    explicit rearm the phase would refuse forever and sit at 0/12 pearls,
    which is the livelock that historically follows every death fix here.
    """
    state = StateManager(tmp_path)
    handler = end_game.WorldUnlockHandler()

    monkeypatch.setattr(end_game, "count_item", lambda *_a, **_k: 0)
    monkeypatch.setattr(
        end_game, "withdraw_required_from_catalog", lambda *_a, **_k: None
    )

    hunted = []
    monkeypatch.setattr(
        end_game, "hunt_endermen", lambda *_a, **_k: hunted.append(True) or 0
    )

    rearmed = []

    def fake_readiness(_client, _state):
        rearmed.append(True)
        return False

    monkeypatch.setattr(handler, "_ensure_combat_readiness", fake_readiness)

    assert handler._craft_eyes(SimpleNamespace(), state, SimpleNamespace()) is False
    assert rearmed, "phase never attempted a rearm for a naked bot"
    assert not hunted, "phase hunted anyway despite failing the readiness gate"
