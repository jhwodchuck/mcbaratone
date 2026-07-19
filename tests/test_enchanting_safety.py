from types import SimpleNamespace

from baritone_client.automator.phases import enchanting
from baritone_client.common.tasks import TaskResult


def test_leather_objective_withdraws_bank_then_hunts_only_cattle_and_returns(monkeypatch):
    client = SimpleNamespace(
        transport=SimpleNamespace(dispatch=lambda *_args, **_kwargs: {})
    )
    state = SimpleNamespace(
        custom_data={
            "structures": {
                "starter_house": {"supply_chest": [-8, 79, -120]}
            }
        }
    )
    leather = {"count": 2}
    calls = []
    handler = enchanting.EnchantingPipelineHandler()

    monkeypatch.setattr(
        enchanting,
        "count_item",
        lambda _client, item_id: leather["count"]
        if item_id == "minecraft:leather"
        else 0,
    )
    monkeypatch.setattr(
        enchanting,
        "withdraw_required_from_chest",
        lambda _client, chest, requirements: calls.append(
            ("withdraw", chest, requirements)
        )
        or 1,
    )
    monkeypatch.setattr(handler, "_wait_for_daylight", lambda *_args: True)
    monkeypatch.setattr(handler, "_leave_starter_house", lambda *_args: True)
    monkeypatch.setattr(
        handler,
        "_return_home",
        lambda *_args: calls.append(("return",)) or True,
    )

    def hunt(_client, **kwargs):
        calls.append(("hunt", kwargs))
        leather["count"] = 46
        return TaskResult.ok("complete")

    monkeypatch.setattr(enchanting, "hunt_mobs", hunt)

    assert handler._gather_leather(client, state)
    assert calls[0] == (
        "withdraw",
        (-8, 79, -120),
        {"minecraft:leather": 46},
    )
    hunt_call = next(call for call in calls if call[0] == "hunt")
    assert hunt_call[1]["mob_types"] == ["cow", "mooshroom"]
    assert hunt_call[1]["required_loot"] == {"minecraft:leather": 44}
    assert hunt_call[1]["abort_on_other_hostiles"] is True
    assert hunt_call[1]["latest_world_time"] == 9000
    assert hunt_call[1]["max_distance_from_origin"] == 160.0
    assert hunt_call[1]["exploration_center"] is None
    assert calls[-1] == ("return",)


def test_existing_bed_is_verified_without_hunting(monkeypatch):
    client = SimpleNamespace()
    house = {"origin": [-9, 78, -122], "bed": [-7, 79, -120]}
    state = SimpleNamespace(
        custom_data={"structures": {"starter_house": house}}
    )
    handler = enchanting.EnchantingPipelineHandler()
    monkeypatch.setattr(
        enchanting,
        "find_nearby_block",
        lambda *_args, **_kwargs: (-5, 79, -119),
    )
    monkeypatch.setattr(
        handler,
        "_leave_starter_house",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("verified bed should not start a sheep hunt")
        ),
    )

    assert handler._ensure_sleeping_bed(client, state)
    assert house["bed"] == [-5, 79, -119]


def test_verified_base_skips_repair_and_resource_gathering(monkeypatch):
    origin = (-9, 78, -122)
    blocks = {
        (-8, 79, -121): "minecraft:crafting_table",
        (-7, 79, -121): "minecraft:furnace",
        (-8, 79, -120): "minecraft:chest",
    }

    class Transport:
        def dispatch(self, route, payload):
            if route == "get_block":
                return {
                    "id": blocks.get(
                        (payload["x"], payload["y"], payload["z"]),
                        "minecraft:air",
                    )
                }
            return {}

    client = SimpleNamespace(transport=Transport())
    state = SimpleNamespace(
        custom_data={
            "structures": {"starter_house": {"origin": list(origin)}}
        }
    )
    handler = enchanting.EnchantingPipelineHandler()
    monkeypatch.setattr(handler, "_starter_house_integrity", lambda *_args: True)
    monkeypatch.setattr(
        handler,
        "_ensure_bookshelf_planks",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("verified workstations need no wood")
        ),
    )

    assert handler._ensure_starter_base(client, state)
    house = state.custom_data["structures"]["starter_house"]
    assert house["supply_chest"] == [-8, 79, -120]


def test_base_restore_fails_before_workstations_when_shell_repair_fails(monkeypatch):
    client = SimpleNamespace()
    state = SimpleNamespace(
        custom_data={
            "structures": {
                "starter_house": {"origin": [-9, 78, -122]},
            }
        }
    )
    handler = enchanting.EnchantingPipelineHandler()
    monkeypatch.setattr(handler, "_starter_house_integrity", lambda *_args: False)
    monkeypatch.setattr(enchanting, "build_good_house", lambda *_args: False)
    monkeypatch.setattr(
        enchanting,
        "craft",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("failed shell must block workstation crafting")
        ),
    )

    assert not handler._ensure_starter_base(client, state)


def test_failed_leather_hunt_still_returns_home(monkeypatch):
    client = SimpleNamespace(
        transport=SimpleNamespace(dispatch=lambda *_args, **_kwargs: {})
    )
    state = SimpleNamespace(custom_data={})
    handler = enchanting.EnchantingPipelineHandler()
    returned = []
    monkeypatch.setattr(enchanting, "count_item", lambda *_args: 0)
    monkeypatch.setattr(handler, "_withdraw_at_home", lambda *_args: 0)
    monkeypatch.setattr(handler, "_wait_for_daylight", lambda *_args: True)
    monkeypatch.setattr(handler, "_leave_starter_house", lambda *_args: True)
    monkeypatch.setattr(
        enchanting,
        "hunt_mobs",
        lambda *_args, **_kwargs: TaskResult.fail("night"),
    )
    monkeypatch.setattr(
        handler,
        "_return_home",
        lambda *_args: returned.append(True) or True,
    )

    assert not handler._gather_leather(client, state)
    assert returned == [True]


def test_leave_house_stages_clear_of_closed_door_before_exploring(monkeypatch):
    calls = []

    class Transport:
        def dispatch(self, route, payload):
            calls.append((route, payload))
            if route == "get_block":
                return {
                    "id": "minecraft:birch_door",
                    "state": {"open": "false"},
                }
            return {}

    client = SimpleNamespace(transport=Transport())
    state = SimpleNamespace(
        custom_data={
            "structures": {
                "starter_house": {
                    "origin": [-9, 78, -122],
                    "door": [-6, 79, -122],
                }
            }
        }
    )
    destinations = []
    monkeypatch.setattr(
        enchanting,
        "goto",
        lambda _client, x, y, z, **_kwargs: destinations.append((x, y, z))
        or True,
    )
    monkeypatch.setattr(enchanting.time, "sleep", lambda _seconds: None)

    handler = enchanting.EnchantingPipelineHandler()
    monkeypatch.setattr(handler, "_surface_staging_y", lambda *_args: 79)
    assert handler._leave_starter_house(client, state)
    assert destinations == [(-6, 79, -124), (-6, 79, -128)]
    settings = [
        payload["message"]
        for route, payload in calls
        if route == "chat"
    ]
    assert settings == ["#set allowBreak false", "#set allowBreak true"]


def test_surface_staging_adjusts_to_lower_ground():
    class Transport:
        def dispatch(self, route, payload):
            if route != "get_block":
                return {}
            blocks = {
                77: "minecraft:grass_block",
                78: "minecraft:air",
                79: "minecraft:air",
            }
            return {"id": blocks.get(payload["y"], "minecraft:air")}

    client = SimpleNamespace(transport=Transport())
    assert (
        enchanting.EnchantingPipelineHandler()._surface_staging_y(
            client, -6, 79, -128
        )
        == 78
    )


def test_paper_objective_crafts_banked_cane_without_leaving_home(monkeypatch):
    client = SimpleNamespace(
        transport=SimpleNamespace(dispatch=lambda *_args, **_kwargs: {})
    )
    state = SimpleNamespace(custom_data={})
    inventory = {"minecraft:paper": 0, "minecraft:sugar_cane": 138}
    crafted = []
    handler = enchanting.EnchantingPipelineHandler()
    monkeypatch.setattr(
        enchanting,
        "count_item",
        lambda _client, item_id: inventory.get(item_id, 0),
    )
    monkeypatch.setattr(handler, "_withdraw_at_home", lambda *_args: 0)

    def make_paper(_client, item_id, count):
        crafted.append((item_id, count))
        inventory["minecraft:sugar_cane"] -= count
        inventory["minecraft:paper"] += count
        return True

    monkeypatch.setattr(enchanting, "craft", make_paper)
    monkeypatch.setattr(
        handler,
        "_leave_starter_house",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("banked cane should not leave home")
        ),
    )

    assert handler._harvest_sugarcane(client, state)
    assert crafted == [("minecraft:paper", 138)]


def test_sugar_cane_search_fails_closed_at_night(monkeypatch):
    client = SimpleNamespace(
        transport=SimpleNamespace(
            dispatch=lambda route, _payload: {"world_time": 13500}
            if route == "get_state"
            else {}
        )
    )
    monkeypatch.setattr(enchanting, "count_item", lambda *_args: 0)

    assert not enchanting.EnchantingPipelineHandler()._gather_sugar_cane(
        client,
        138,
        timeout=1,
    )


def test_sugar_cane_search_stops_at_expedition_radius(monkeypatch):
    positions = iter((0, 0, 200))
    calls = []

    class Transport:
        def dispatch(self, route, payload):
            calls.append((route, payload))
            if route == "get_state":
                x = next(positions, 200)
                return {
                    "world_time": 2000,
                    "health": 20,
                    "food_level": 20,
                    "is_pathing": True,
                    "block_position": {"x": x, "y": 64, "z": 0},
                }
            return {}

    client = SimpleNamespace(transport=Transport())
    monkeypatch.setattr(enchanting, "count_item", lambda *_args: 0)
    monkeypatch.setattr(enchanting, "scan_for_threats", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(enchanting.time, "sleep", lambda _seconds: None)

    assert not enchanting.EnchantingPipelineHandler()._gather_sugar_cane(
        client,
        138,
        timeout=1,
    )
    assert any(route == "cancel" for route, _payload in calls)


def test_sugar_cane_sector_rotation_persists():
    state = SimpleNamespace(custom_data={})
    origin = (-9, 78, -122)
    first = enchanting.EnchantingPipelineHandler()
    assert first._next_cane_exploration_center(origin, state) == (-6, -250)
    resumed = enchanting.EnchantingPipelineHandler()
    assert resumed._next_cane_exploration_center(origin, state) == (90, -218)
    assert state.custom_data["expeditions"]["sugar_cane_sector_index"] == 2


def test_storage_withdrawal_routes_interrupted_expedition_home(monkeypatch):
    class Transport:
        def dispatch(self, route, _payload):
            if route == "get_state":
                return {
                    "world_time": 4000,
                    "block_position": {"x": 120, "y": 70, "z": 120},
                }
            return {}

    client = SimpleNamespace(transport=Transport())
    state = SimpleNamespace(
        custom_data={
            "structures": {
                "starter_house": {"supply_chest": [-8, 79, -120]}
            }
        }
    )
    handler = enchanting.EnchantingPipelineHandler()
    calls = []
    monkeypatch.setattr(
        handler,
        "_return_home",
        lambda *_args: calls.append("home") or True,
    )
    monkeypatch.setattr(
        enchanting,
        "withdraw_required_from_chest",
        lambda _client, chest, requirements: calls.append(
            (chest, requirements)
        )
        or 1,
    )

    assert handler._withdraw_at_home(
        client,
        state,
        {"minecraft:leather": 46},
    ) == 1
    assert calls == [
        "home",
        ((-8, 79, -120), {"minecraft:leather": 46}),
    ]


def test_leather_expeditions_rotate_without_centering_south_of_house():
    handler = enchanting.EnchantingPipelineHandler()
    origin = (-9, 78, -122)
    centers = [handler._next_leather_exploration_center(origin) for _ in range(5)]
    assert centers == [
        (-6, -250),
        (122, -186),
        (-134, -186),
        (122, -146),
        (-134, -146),
    ]
    assert all(z < -122 for _x, z in centers)


def test_leather_expedition_rotation_persists_across_handler_restart():
    state = SimpleNamespace(custom_data={})
    origin = (-9, 78, -122)

    first = enchanting.EnchantingPipelineHandler()
    assert first._next_leather_exploration_center(origin, state) == (-6, -250)

    resumed = enchanting.EnchantingPipelineHandler()
    assert resumed._next_leather_exploration_center(origin, state) == (122, -186)
    assert state.custom_data["expeditions"]["leather_sector_index"] == 2


def test_paper_falls_back_to_explicit_manual_recipe(monkeypatch):
    client = SimpleNamespace(
        transport=SimpleNamespace(dispatch=lambda *_args, **_kwargs: {})
    )
    inventory = {"minecraft:paper": 0, "minecraft:sugar_cane": 6}
    recipes = []
    monkeypatch.setattr(
        enchanting,
        "count_item",
        lambda _client, item_id: inventory.get(item_id, 0),
    )
    monkeypatch.setattr(enchanting, "craft", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        enchanting.harness_ops,
        "ensure_crafting_table_open",
        lambda _client: True,
    )

    def manual(_client, result_id, placements, **kwargs):
        recipes.append((result_id, placements, kwargs))
        inventory["minecraft:paper"] = 6
        return True

    monkeypatch.setattr(enchanting.harness_ops, "craft_recipe_manual", manual)

    assert enchanting.EnchantingPipelineHandler()._craft_available_paper(
        client, 6
    )
    assert recipes[0][0] == "minecraft:paper"
    assert recipes[0][2] == {"crafts": 2, "output_per_recipe": 3}


def test_morning_window_accepts_early_day_immediately(monkeypatch):
    client = SimpleNamespace(
        transport=SimpleNamespace(
            dispatch=lambda route, _payload: {"world_time": 2500}
            if route == "get_state"
            else {}
        )
    )
    monkeypatch.setattr(
        enchanting,
        "wait_for_safe_daylight",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("early morning should not enter shelter wait")
        ),
    )
    assert enchanting.EnchantingPipelineHandler()._wait_for_daylight(
        client,
        timeout=1,
    )


def test_late_day_wait_stages_away_from_door(monkeypatch):
    times = iter((8000, 100))
    client = SimpleNamespace(
        transport=SimpleNamespace(
            dispatch=lambda route, _payload: {
                "world_time": next(times, 100),
            }
            if route == "get_state"
            else {}
        )
    )
    state = SimpleNamespace(custom_data={})
    handler = enchanting.EnchantingPipelineHandler()
    staged = []
    monkeypatch.setattr(
        handler,
        "_stage_inside_house",
        lambda *_args: staged.append(True) or True,
    )

    assert handler._wait_for_daylight(client, state, timeout=1)
    assert staged == [True]


def test_obsidian_mining_refuses_to_start_without_diamond_pick(monkeypatch):
    client = SimpleNamespace(
        transport=SimpleNamespace(
            dispatch=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("missing pick must fail before bridge command")
            )
        )
    )
    monkeypatch.setattr(enchanting, "count_item", lambda *_args: 0)
    assert not enchanting.EnchantingPipelineHandler()._mine_obsidian(client)


def test_obsidian_mining_resumes_after_eating(monkeypatch):
    calls = []
    inventory = {"minecraft:diamond_pickaxe": 1, "minecraft:obsidian": 0}
    food = {"level": 10}

    class Transport:
        def dispatch(self, route, payload):
            calls.append((route, payload))
            if route == "get_state":
                return {
                    "health": 20,
                    "food_level": food["level"],
                    "world_time": 2000,
                    "is_pathing": True,
                }
            if route == "mine" and len(
                [call for call in calls if call[0] == "mine"]
            ) >= 2:
                inventory["minecraft:obsidian"] = 4
            return {}

    client = SimpleNamespace(transport=Transport())
    monkeypatch.setattr(
        enchanting,
        "count_item",
        lambda _client, item_id: inventory.get(item_id, 0),
    )
    monkeypatch.setattr(enchanting.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(enchanting, "scan_for_threats", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        "baritone_client.common.combat.eat_until_hunger",
        lambda *_args, **_kwargs: food.update(level=16) or True,
    )

    assert enchanting.EnchantingPipelineHandler()._mine_obsidian(
        client,
        target=4,
        timeout=1,
    )
    assert len([call for call in calls if call[0] == "mine"]) == 2


def test_unimplemented_enchanting_completion_steps_fail_closed():
    handler = enchanting.EnchantingPipelineHandler()
    assert not handler._build_enchanting_room(SimpleNamespace())
    assert not handler._roll_enchants(SimpleNamespace())


def test_bookshelf_plank_requirement_uses_six_per_shelf(monkeypatch):
    client = SimpleNamespace()
    state = SimpleNamespace(custom_data={})
    handler = enchanting.EnchantingPipelineHandler()
    requested = []
    monkeypatch.setattr(enchanting, "count_item", lambda *_args: 0)
    monkeypatch.setattr(
        handler,
        "_ensure_bookshelf_planks",
        lambda _client, _state, target: requested.append(target) or False,
    )
    assert not handler._craft_bookshelves(client, state, target=15)
    assert requested == [90]
