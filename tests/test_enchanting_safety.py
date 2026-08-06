from types import SimpleNamespace

import pytest

from baritone_client.automator.phases import enchanting, leather_supply
from baritone_client.automator.state_manager import Phase, StateManager
from baritone_client.common.tasks import TaskResult


@pytest.fixture(autouse=True)
def equipped_for_expedition(monkeypatch):
    """Treat the bot as combat-ready unless a test says otherwise.

    Leather expeditions are now refused below 3/4 armor, because the live
    worker was hunting in the open at 1/4 armor while dying repeatedly. These
    tests exercise hunt routing and cost control, not equipment, and their
    stubs report no armor at all -- without this they would all defer and
    assert nothing. Equipment itself is covered by
    ``tests/test_leather_expedition_cost.py``.
    """
    monkeypatch.setattr(
        leather_supply, "expedition_is_too_dangerous", lambda *_a, **_k: False
    )


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
    assert hunt_call[1]["mob_types"] == [
        "cow",
        "mooshroom",
        "horse",
        "donkey",
        "mule",
        "llama",
    ]
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
    client = SimpleNamespace(
        transport=SimpleNamespace(dispatch=lambda *_args, **_kwargs: {})
    )
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


def test_owned_workstation_clears_exact_wrong_target_before_placement(monkeypatch):
    calls = []

    class Transport:
        def dispatch(self, route, payload):
            calls.append((route, payload))
            if route == "get_block":
                return {"id": "minecraft:pointed_dripstone"}
            return {}

    client = SimpleNamespace(transport=Transport())
    handler = enchanting.EnchantingPipelineHandler()
    monkeypatch.setattr(
        enchanting,
        "_clear_wrong_house_target",
        lambda _client, x, y, z, requested: calls.append(
            ("clear", (x, y, z), requested)
        )
        or True,
    )
    monkeypatch.setattr(
        enchanting.harness_ops,
        "place_block_exact",
        lambda *_args, **_kwargs: True,
    )

    assert handler._place_owned_base_block(
        client,
        (-162, 104, -384),
        "minecraft:chest",
    )
    assert (
        "clear",
        (-162, 104, -384),
        "minecraft:chest",
    ) in calls


def test_base_restore_defers_when_far_from_starter_house(monkeypatch):
    """This is the first task in ENCHANTING_PIPELINE's sequence, so it
    reruns on every phase-level retry -- including mid-expedition, hundreds
    of blocks from base, where the origin chunk is genuinely unloaded. It
    must defer instead of surveying from there and misreading the intact
    house as destroyed. Confirmed live: Bot07 started gathering wood for a
    "destroyed" house 200+ blocks from its real one."""
    client = SimpleNamespace(
        transport=SimpleNamespace(
            dispatch=lambda *_args, **_kwargs: {
                "block_position": {"x": -9, "y": 78, "z": 300}
            }
        )
    )
    state = SimpleNamespace(
        custom_data={
            "structures": {
                "starter_house": {"origin": [-9, 78, -122]},
            }
        }
    )
    handler = enchanting.EnchantingPipelineHandler()
    monkeypatch.setattr(
        handler,
        "_starter_house_integrity",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("must not survey the house from far away")
        ),
    )

    assert handler._ensure_starter_base(client, state)


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
        enchanting, "visit_known_herd_for_loot", lambda *_args, **_kwargs: False
    )
    monkeypatch.setattr(
        handler,
        "_return_home",
        lambda *_args: returned.append(True) or True,
    )

    assert not handler._gather_leather(client, state)
    assert returned == [True]


def test_leather_hunt_falls_back_to_known_herd_when_local_search_fails(monkeypatch):
    """The local bounded hunt rotates through dozens of sectors and can still
    fail entirely if the biome has no huntable animals anywhere in range --
    confirmed live: Bot07 spent 36+ sector rotations finding zero cows. The
    known-herd fallback must be tried before giving up, and the final result
    must reflect whatever it deposits (not just the local hunt's outcome)."""
    client = SimpleNamespace(
        transport=SimpleNamespace(dispatch=lambda *_args, **_kwargs: {})
    )
    state = SimpleNamespace(custom_data={})
    handler = enchanting.EnchantingPipelineHandler()
    returns = []
    leather = {"count": 0}

    monkeypatch.setattr(
        enchanting, "count_item",
        lambda _client, item_id: leather["count"] if item_id == "minecraft:leather" else 0,
    )
    monkeypatch.setattr(handler, "_withdraw_at_home", lambda *_args: 0)
    monkeypatch.setattr(handler, "_wait_for_daylight", lambda *_args: True)
    monkeypatch.setattr(handler, "_leave_starter_house", lambda *_args: True)
    monkeypatch.setattr(enchanting, "hunt_mobs", lambda *_a, **_k: TaskResult.fail("no targets"))
    monkeypatch.setattr(handler, "_return_home", lambda *_args: returns.append("return") or True)

    herd_calls = []
    def herd_fallback(_client, required_loot, animal_type, **kwargs):
        herd_calls.append((dict(required_loot), animal_type, kwargs))
        leather["count"] = 46
        return True
    monkeypatch.setattr(enchanting, "visit_known_herd_for_loot", herd_fallback)

    assert handler._gather_leather(client, state) is True
    # The herd must be left with a breeding pair: 46 leather is ~46 kills, and
    # passive mobs never respawn in already-generated chunks, so hunting the
    # waypoint flat permanently destroys the fleet's only renewable source.
    assert herd_calls == [
        ({"minecraft:leather": 46}, "cow", {"preserve_breeding_pair": True})
    ]
    # _return_home must run again after the herd trip, on top of the one
    # already run in the failed local hunt's finally block.
    assert returns == ["return", "return"]


def test_leather_hunt_shards_from_live_position_when_home_route_is_unavailable(
    monkeypatch,
):
    class Transport:
        def dispatch(self, route, _payload):
            if route == "get_state":
                return {
                    "world_time": 1000,
                    "block_position": {"x": 10, "y": 64, "z": 20},
                }
            return {}

    client = SimpleNamespace(transport=Transport())
    state = SimpleNamespace(custom_data={})
    handler = enchanting.EnchantingPipelineHandler()
    leather = {"count": 0}
    hunts = []
    monkeypatch.setattr(
        enchanting,
        "count_item",
        lambda _client, item_id: (
            leather["count"] if item_id == "minecraft:leather" else 0
        ),
    )
    monkeypatch.setattr(handler, "_withdraw_at_home", lambda *_args: -1)
    monkeypatch.setattr(handler, "_wait_for_daylight", lambda *_args: True)
    monkeypatch.setattr(
        handler,
        "_leave_starter_house",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("a bot already outside must not use the house exit")
        ),
    )
    monkeypatch.setattr(
        handler,
        "_return_home",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("the same rejected home route must not be retried")
        ),
    )

    def hunt(_client, **kwargs):
        hunts.append(kwargs)
        leather["count"] = 46
        return TaskResult.ok("local herd found")

    monkeypatch.setattr(enchanting, "hunt_mobs", hunt)

    assert handler._gather_leather(client, state)
    assert hunts[0]["exploration_center"] == (13, -108)
    assert hunts[0]["max_distance_from_origin"] == 160.0
    assert state.custom_data["expeditions"]["leather_sector_index"] == 1


def test_local_leather_hunt_leaves_boxed_swamp_before_selecting_sector(monkeypatch):
    class Transport:
        def __init__(self):
            self.state_reads = 0

        def dispatch(self, route, _payload):
            if route == "get_state":
                self.state_reads += 1
                position = (
                    {"x": 10, "y": 64, "z": 20}
                    if self.state_reads == 1
                    else {"x": 34, "y": 66, "z": 20}
                )
                return {
                    "world_time": 1000,
                    "biome": "minecraft:mangrove_swamp",
                    "block_position": position,
                }
            return {}

    client = SimpleNamespace(transport=Transport())
    state = SimpleNamespace(custom_data={})
    handler = enchanting.EnchantingPipelineHandler()
    leather = {"count": 0}
    relocations = []
    hunts = []
    monkeypatch.setattr(
        enchanting,
        "count_item",
        lambda _client, item_id: (
            leather["count"] if item_id == "minecraft:leather" else 0
        ),
    )
    monkeypatch.setattr(handler, "_withdraw_at_home", lambda *_args: -1)
    monkeypatch.setattr(handler, "_wait_for_daylight", lambda *_args: True)
    monkeypatch.setattr(
        enchanting,
        "_relocate_to_dry_stone_terrain",
        lambda _client: relocations.append(True) or True,
    )

    def hunt(_client, **kwargs):
        hunts.append(kwargs)
        leather["count"] = 46
        return TaskResult.ok("dry herd found")

    monkeypatch.setattr(enchanting, "hunt_mobs", hunt)

    assert handler._gather_leather(client, state)
    assert relocations == [True]
    assert hunts[0]["exploration_center"] == (37, -108)


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


def test_exterior_staging_uses_lateral_clear_ground(monkeypatch):
    handler = enchanting.EnchantingPipelineHandler()
    probes = []

    def standing_y(_client, x, _nominal_y, z):
        probes.append((x, z))
        return 79 if (x, z) == (-8, -128) else None

    monkeypatch.setattr(handler, "_surface_staging_y", standing_y)

    assert handler._exterior_staging_tile(SimpleNamespace(), (-6, 79, -122)) == (
        -8,
        79,
        -128,
    )
    assert probes == [(-6, -128), (-8, -128)]


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


def test_sugar_cane_sectors_cover_all_directions_and_shard_fleet_bots(tmp_path):
    handler = enchanting.EnchantingPipelineHandler()
    origin = (0, 64, 0)
    state = SimpleNamespace(
        custom_data={}, checkpoint_dir=tmp_path / "Bot07" / "controller"
    )
    centers = [handler._next_cane_exploration_center(origin, state) for _ in range(20)]
    offsets = {(x - 3, z) for x, z in centers}

    assert len(offsets) == 20
    assert any(x > 0 for x, _z in offsets)
    assert any(x < 0 for x, _z in offsets)
    assert any(z > 0 for _x, z in offsets)
    assert any(z < 0 for _x, z in offsets)

    bot17 = SimpleNamespace(
        custom_data={}, checkpoint_dir=tmp_path / "Bot17" / "controller"
    )
    assert handler._next_cane_exploration_center(origin, bot17) != centers[0]


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


def test_night_wait_accepts_adjacent_safe_interior_tile(monkeypatch):
    origin = [-145, 79, -96]

    class Transport:
        def dispatch(self, route, _payload):
            if route == "get_block":
                return {"id": "minecraft:air"}
            if route == "get_state":
                return {"block_position": {"x": -141, "y": 80, "z": -92}}
            return {}

    monkeypatch.setattr(
        enchanting,
        "goto",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("already-safe adjacent interior position must not path")
        ),
    )
    state = SimpleNamespace(
        custom_data={"structures": {"starter_house": {"origin": origin}}}
    )

    assert enchanting.EnchantingPipelineHandler()._stage_inside_house(
        SimpleNamespace(transport=Transport()), state
    )


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


def test_enchanting_station_is_built_verified_and_persisted(monkeypatch, tmp_path):
    world = {}

    class Transport:
        def dispatch(self, route, payload):
            if route == "get_block":
                key = (payload["x"], payload["y"], payload["z"])
                return {"id": world.get(key, "minecraft:stone" if key[1] == 64 else "minecraft:air")}
            if route == "get_inventory":
                return {"inventory": []}
            return {}

    client = SimpleNamespace(transport=Transport())
    state = StateManager(tmp_path)
    state.custom_data = {
        "base_location": [0, 64, 0],
        "structures": {"starter_house": {"origin": [0, 64, 0]}},
    }
    handler = enchanting.EnchantingPipelineHandler()
    monkeypatch.setattr(enchanting, "goto", lambda *_a, **_k: True)

    def place(_client, x, y, z, item_id, **_kwargs):
        world[(x, y, z)] = item_id
        return True

    monkeypatch.setattr(enchanting.harness_ops, "place_block_exact", place)

    assert handler._build_enchanting_room(client, state)
    station = state.custom_data["structures"]["enchanting_station"]
    assert station["verified"] is True
    assert len(station["bookshelves"]) == 15
    assert handler._roll_enchants(client, state)
    assert state.custom_data["capabilities"]["level_30_enchanting"] is True
    assert state.get_phase_payload(Phase.ENCHANTING_PIPELINE)["level_30_ready"] is True


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


def test_bed_retry_attempts_are_checkpointed(monkeypatch):
    saved = []
    state = SimpleNamespace(
        custom_data={
            "structures": {
                "starter_house": {"origin": [10, 70, -10], "bed": None}
            },
        },
    )

    def save_checkpoint(payload):
        saved.append(payload)
        return "checkpoint.json"

    state.save_checkpoint = save_checkpoint

    class Transport:
        def dispatch(self, route, _payload):
            if route == "get_state":
                return {"food_level": 20, "world_time": 10000}
            return {}

    client = SimpleNamespace(transport=Transport())

    monkeypatch.setattr(
        enchanting,
        "count_item",
        lambda *_args: 0,
    )
    monkeypatch.setattr(
        enchanting,
        "find_nearby_block",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        enchanting,
        "get_inventory",
        lambda _client: {"minecraft:sticks": 3},
    )
    handler = enchanting.EnchantingPipelineHandler()
    monkeypatch.setattr(
        handler,
        "_withdraw_at_home",
        lambda *_args, **_kwargs: 1,
    )
    monkeypatch.setattr(
        handler,
        "_wait_for_daylight",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(enchanting.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        enchanting,
        "hunt_mobs",
        lambda *_args, **_kwargs: TaskResult.fail("no sheep"),
    )
    monkeypatch.setattr(
        handler,
        "_leave_starter_house",
        lambda *_args: True,
    )
    monkeypatch.setattr(
        handler,
        "_return_home",
        lambda *_args: True,
    )
    monkeypatch.setattr(
        enchanting.harness_ops,
        "craft_bed_manual",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("craft should not run after failed hunt")
        ),
    )

    assert not handler._ensure_sleeping_bed(client, state)
    assert state.custom_data["enchanting"]["bed_retry_attempts"] == 1
    assert saved == [{"minecraft:sticks": 3}]


def test_existing_enchanting_table_is_reused_without_rebuilding(monkeypatch):
    """A table standing in the world is as usable as one in the bag.

    This only asked whether the bot *carried* a table, so a placed one --
    built on an earlier run, or by an operator -- was invisible and the phase
    spent 2 diamonds and 4 obsidian rebuilding it. Mirrors the existing
    bed-reuse behaviour.
    """
    client = SimpleNamespace()
    house = {"origin": [-9, 78, -122]}
    state = SimpleNamespace(custom_data={"structures": {"starter_house": house}})
    handler = enchanting.EnchantingPipelineHandler()

    monkeypatch.setattr(
        enchanting, "find_nearby_block", lambda *_a, **_k: (-107, 80, -383)
    )
    # Carries a diamond pickaxe already, so the method's only remaining job is
    # deciding whether a table must be built.
    monkeypatch.setattr(
        enchanting, "count_item",
        lambda _c, item_id: 1 if item_id == "minecraft:diamond_pickaxe" else 0,
    )
    monkeypatch.setattr(
        handler, "_withdraw_at_home",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("must not gather table inputs when one already exists")
        ),
    )

    assert handler._craft_enchanting_table(client, state) is True
    assert house["enchanting_table"] == [-107, 80, -383]


def test_missing_enchanting_table_still_gathers_its_inputs(monkeypatch):
    """With nothing nearby the phase must still pay for a real table."""
    client = SimpleNamespace()
    state = SimpleNamespace(custom_data={"structures": {"starter_house": {}}})
    handler = enchanting.EnchantingPipelineHandler()
    withdrawals = []

    monkeypatch.setattr(enchanting, "find_nearby_block", lambda *_a, **_k: None)
    monkeypatch.setattr(
        enchanting, "count_item",
        lambda _c, item_id: 1 if item_id == "minecraft:diamond_pickaxe" else 0,
    )
    monkeypatch.setattr(
        handler, "_withdraw_at_home",
        lambda _c, _s, req: withdrawals.append(req),
    )
    monkeypatch.setattr(handler, "_wait_for_daylight", lambda *_a: False)

    assert handler._craft_enchanting_table(client, state) is False
    assert withdrawals, "a missing table must trigger input gathering"


def test_reused_table_does_not_inflate_the_diamond_requirement(monkeypatch):
    """diamond_target is 3 + 2-for-a-table; reuse must drop it back to 3."""
    client = SimpleNamespace()
    state = SimpleNamespace(custom_data={"structures": {"starter_house": {}}})
    handler = enchanting.EnchantingPipelineHandler()
    requested = []

    monkeypatch.setattr(
        enchanting, "find_nearby_block", lambda *_a, **_k: (-107, 80, -383)
    )
    # No diamond pickaxe carried, so the pickaxe branch runs and reveals the target.
    monkeypatch.setattr(enchanting, "count_item", lambda _c, _item: 0)
    monkeypatch.setattr(
        handler, "_withdraw_at_home",
        lambda _c, _s, req: requested.append(req),
    )

    handler._craft_enchanting_table(client, state)

    assert requested, "expected a diamond withdrawal request"
    assert requested[0] == {"minecraft:diamond": 3}, requested[0]


def _leather_handler(monkeypatch, *, leather=0, sources=None):
    """Handler wired so only the futility/backoff logic is exercised."""
    client = SimpleNamespace(
        transport=SimpleNamespace(dispatch=lambda *_args, **_kwargs: {})
    )
    handler = enchanting.EnchantingPipelineHandler()
    monkeypatch.setattr(
        enchanting,
        "count_item",
        lambda _client, item_id: leather if item_id == "minecraft:leather" else 0,
    )
    monkeypatch.setattr(handler, "_withdraw_at_home", lambda *_args: 0)
    monkeypatch.setattr(
        leather_supply,
        "survey_leather_sources",
        lambda *_args, **_kwargs: dict(sources or {}),
    )
    return client, handler


def test_leather_skips_expedition_when_stalled_and_no_animals_are_loaded(monkeypatch):
    """A hunt that has gained nothing repeatedly must not keep paying its full
    cost. The objective graph re-arms abandoned objectives about once a minute
    so a run can never permanently stall, so an unconditional expedition here
    consumes the entire runtime -- live: 90 consecutive failed leather attempts
    on Bot17, zero leather gained, zero deaths, zero other progress."""
    client, handler = _leather_handler(monkeypatch, sources={})
    state = SimpleNamespace(
        custom_data={leather_supply.NO_GAIN_KEY: leather_supply.NO_GAIN_LIMIT}
    )

    def fail_if_called(*_args, **_kwargs):  # pragma: no cover - must not run
        raise AssertionError("a stalled leather search must not hunt again")

    monkeypatch.setattr(enchanting, "hunt_mobs", fail_if_called)
    monkeypatch.setattr(enchanting, "visit_known_herd_for_loot", fail_if_called)
    monkeypatch.setattr(handler, "_wait_for_daylight", fail_if_called)

    assert handler._gather_leather(client, state) is False


def test_leather_still_hunts_while_animals_are_actually_in_range(monkeypatch):
    """The backoff keys on a live survey, not on the failure count alone: a
    stalled streak next to a real herd must still hunt."""
    client, handler = _leather_handler(monkeypatch, sources={"cow": 4})
    state = SimpleNamespace(
        custom_data={leather_supply.NO_GAIN_KEY: leather_supply.NO_GAIN_LIMIT}
    )
    hunted = []
    monkeypatch.setattr(handler, "_wait_for_daylight", lambda *_args: True)
    monkeypatch.setattr(handler, "_leave_starter_house", lambda *_args: True)
    monkeypatch.setattr(handler, "_return_home", lambda *_args: True)
    monkeypatch.setattr(
        enchanting,
        "hunt_mobs",
        lambda *_a, **_k: hunted.append(True) or TaskResult.fail("no targets"),
    )
    monkeypatch.setattr(
        enchanting, "visit_known_herd_for_loot", lambda *_a, **_k: False
    )

    handler._gather_leather(client, state)
    assert hunted == [True]


def test_leather_forces_a_full_retry_periodically_while_stalled(monkeypatch):
    """Suppression must never be permanent -- a world can regain animals via
    breeding or newly generated chunks."""
    client, handler = _leather_handler(monkeypatch, sources={})
    state = SimpleNamespace(
        custom_data={leather_supply.NO_GAIN_KEY: leather_supply.FULL_RETRY_EVERY}
    )
    hunted = []
    monkeypatch.setattr(handler, "_wait_for_daylight", lambda *_args: True)
    monkeypatch.setattr(handler, "_leave_starter_house", lambda *_args: True)
    monkeypatch.setattr(handler, "_return_home", lambda *_args: True)
    monkeypatch.setattr(
        enchanting,
        "hunt_mobs",
        lambda *_a, **_k: hunted.append(True) or TaskResult.fail("no targets"),
    )
    monkeypatch.setattr(
        enchanting, "visit_known_herd_for_loot", lambda *_a, **_k: False
    )

    handler._gather_leather(client, state)
    assert hunted == [True], "periodic retry must still run a real expedition"


def test_fruitless_herd_visit_suppresses_that_waypoint_then_expires(monkeypatch):
    """A waypoint that is empty or unreachable must stop being walked to every
    pass -- live, the operator waypoint held zero animals yet was retried on
    every cycle -- but the suppression has to expire so a re-bred herd counts."""
    client, handler = _leather_handler(monkeypatch, sources={})
    state = SimpleNamespace(custom_data={})
    monkeypatch.setattr(handler, "_wait_for_daylight", lambda *_args: True)
    monkeypatch.setattr(handler, "_leave_starter_house", lambda *_args: True)
    monkeypatch.setattr(handler, "_return_home", lambda *_args: True)
    monkeypatch.setattr(
        enchanting, "hunt_mobs", lambda *_a, **_k: TaskResult.fail("no targets")
    )
    visits = []
    monkeypatch.setattr(
        enchanting,
        "visit_known_herd_for_loot",
        lambda *_a, **_k: visits.append(True) or False,
    )

    assert handler._gather_leather(client, state) is False
    assert visits == [True]
    assert leather_supply.herd_waypoint_is_exhausted(state) is True

    # Second pass must not walk to the same empty waypoint again.
    assert handler._gather_leather(client, state) is False
    assert visits == [True]

    # ...but the suppression is a cooldown, not a permanent blacklist.
    state.custom_data[leather_supply.HERD_EXHAUSTED_KEY] = 0
    assert leather_supply.herd_waypoint_is_exhausted(state) is False


def test_survey_leather_sources_ignores_undead_horses(monkeypatch):
    """Skeleton/zombie horses share the 'horse' substring but drop bones, so
    counting them would keep a dead-end hunt alive."""
    from baritone_client.common import husbandry

    entities = [
        {"type": "minecraft:cow"},
        {"type": "minecraft:skeleton_horse"},
        {"type": "minecraft:zombie_horse"},
        {"type": "minecraft:horse"},
        {"type": "minecraft:zombie"},
        {"type": "minecraft:llama"},
    ]
    monkeypatch.setattr(
        husbandry, "get_nearby_entities", lambda _client, _radius: entities
    )

    counts = husbandry.survey_leather_sources(SimpleNamespace(), radius=64)
    assert counts == {"cow": 1, "horse": 1, "llama": 1}
