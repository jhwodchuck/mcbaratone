from baritone_client.common import inventory
from baritone_client.common import harness_ops
from baritone_client.common.state import WorldState
from baritone_client.actions.crafting import CraftingAction
from types import SimpleNamespace
import json


class DummyTransport:
    def __init__(self, responses=None):
        self.responses = responses or {}
        self.calls = []

    def dispatch(self, route, payload):
        self.calls.append((route, payload))
        return self.responses.get(route, {"status": "ok", "data": {}})


class DummyClient:
    def __init__(self, transport):
        self.transport = transport


class RecipeAwareTransport:
    """Tiny inventory simulator for dependency-order regression tests."""

    def __init__(self):
        self.items = {"minecraft:oak_planks": 60}
        self.calls = []

    def dispatch(self, route, payload):
        self.calls.append((route, payload))
        if route == "get_inventory":
            entries = [
                {"id": item_id, "count": count, "slot": slot}
                for slot, (item_id, count) in enumerate(self.items.items())
                if count > 0
            ]
            return {"status": "ok", "data": {"inventory": entries}}

        if route == "craft":
            item_id = payload.get("item")
            count = payload.get("count", 1)
            if item_id == "minecraft:stick" and self.items.get("minecraft:oak_planks", 0) >= 2:
                self.items["minecraft:oak_planks"] -= 2
                self.items[item_id] = self.items.get(item_id, 0) + count
            elif (
                item_id == "minecraft:wooden_pickaxe"
                and self.items.get("minecraft:oak_planks", 0) >= 3
                and self.items.get("minecraft:stick", 0) >= 2
            ):
                self.items["minecraft:oak_planks"] -= 3
                self.items["minecraft:stick"] -= 2
                self.items[item_id] = self.items.get(item_id, 0) + count
        return {"status": "ok"}


class FoodAndIronTransport:
    """Simulator for the resumed FOOD_AND_IRON blocker state."""

    def __init__(self):
        self.items = {
            "minecraft:iron_ingot": 9,
            "minecraft:oak_log": 2,
            "minecraft:stick": 1,
            "minecraft:iron_pickaxe": 1,
        }
        self.calls = []

    def dispatch(self, route, payload):
        self.calls.append((route, payload))
        if route == "get_inventory":
            entries = [
                {"id": item_id, "count": count, "slot": slot}
                for slot, (item_id, count) in enumerate(self.items.items())
                if count > 0
            ]
            return {"status": "ok", "data": {"inventory": entries}}

        if route != "craft":
            return {"status": "ok"}

        item_id = payload.get("item")
        count = int(payload.get("count", 1))
        if item_id == "minecraft:oak_planks" and self.items.get("minecraft:oak_log", 0) > 0:
            self.items["minecraft:oak_log"] -= 1
            self.items["minecraft:oak_planks"] = (
                self.items.get("minecraft:oak_planks", 0) + count
            )
        elif item_id == "minecraft:stick" and self.items.get("minecraft:oak_planks", 0) >= 2:
            self.items["minecraft:oak_planks"] -= 2
            self.items[item_id] = self.items.get(item_id, 0) + count
        elif (
            item_id == "minecraft:iron_pickaxe"
            and self.items.get("minecraft:iron_ingot", 0) >= 3
            and self.items.get("minecraft:stick", 0) >= 2
        ):
            self.items["minecraft:iron_ingot"] -= 3
            self.items["minecraft:stick"] -= 2
            self.items[item_id] = self.items.get(item_id, 0) + count
        return {"status": "ok"}


def test_world_state_defaults_to_isolated_run_directory(monkeypatch, tmp_path):
    isolated = tmp_path / "Bot99"
    monkeypatch.setenv("MC_RUN_DIR", str(isolated))
    monkeypatch.chdir(tmp_path)

    world = WorldState(DummyClient(DummyTransport()))
    written = world.save_checkpoint("storage", {"x": 1, "y": 2, "z": 3})

    assert written == str(isolated / "checkpoint_storage.json")
    assert (isolated / "checkpoint_storage.json").exists()
    assert not (tmp_path / "checkpoint_storage.json").exists()


def test_check_craft_via_bridge():
    t = DummyTransport(responses={
        "check_craft": {"status": "ok", "data": {"can_craft": True, "missing": [], "recipe_id": "r1"}}
    })
    client = DummyClient(t)
    res = inventory.check_craft(client, "minecraft:stone_pickaxe", count=1)
    assert res["can_craft"] is True
    assert res["recipe_id"] == "r1"


def test_craft_item_delegates():
    t = DummyTransport(responses={
        "craft": {"status": "ok", "data": {"crafted": True}}
    })
    client = DummyClient(t)
    ok = inventory.craft_item(client, "r1", count=1)
    assert ok is True
    assert t.calls and t.calls[-1][0] == "craft"


def test_plank_craft_uses_manual_grid_before_native_recipe(monkeypatch):
    transport = FoodAndIronTransport()
    client = DummyClient(transport)
    monkeypatch.setattr(harness_ops, "available", lambda: True)

    def manual_planks(_client, item_id, output_count):
        assert item_id == "minecraft:oak_planks"
        assert output_count == 4
        transport.items["minecraft:oak_log"] -= 1
        transport.items[item_id] = transport.items.get(item_id, 0) + 4
        return True

    monkeypatch.setattr(harness_ops, "craft_planks_manual", manual_planks)

    assert inventory.craft(client, "minecraft:oak_planks", 4)
    assert not any(
        route in {"craft", "auto_craft"} and payload.get("item") == "minecraft:oak_planks"
        for route, payload in transport.calls
    )


def test_tool_craft_prepares_sticks_before_pickaxe(monkeypatch):
    transport = RecipeAwareTransport()
    client = DummyClient(transport)
    monkeypatch.setattr(harness_ops, "available", lambda: True)
    monkeypatch.setattr(harness_ops, "ensure_crafting_table_open", lambda _client: True)

    def manual_tool(_client, item_id):
        assert item_id == "minecraft:wooden_pickaxe"
        transport.items["minecraft:oak_planks"] -= 3
        transport.items["minecraft:stick"] -= 2
        transport.items[item_id] = transport.items.get(item_id, 0) + 1
        return True

    monkeypatch.setattr(harness_ops, "craft_tool_manual", manual_tool)

    assert inventory.craft(client, "minecraft:wooden_pickaxe", 1) is True

    craft_items = [payload["item"] for route, payload in transport.calls if route == "craft"]
    assert craft_items == ["minecraft:stick"]
    assert transport.items["minecraft:wooden_pickaxe"] == 1


def test_food_and_iron_resumes_from_log_state_for_second_pickaxe(monkeypatch):
    transport = FoodAndIronTransport()
    client = DummyClient(transport)
    monkeypatch.setattr(harness_ops, "available", lambda: True)
    monkeypatch.setattr(harness_ops, "ensure_crafting_table_open", lambda _client: True)

    def manual_tool(_client, item_id):
        assert item_id == "minecraft:iron_pickaxe"
        transport.items["minecraft:iron_ingot"] -= 3
        transport.items["minecraft:stick"] -= 2
        transport.items[item_id] += 1
        return True

    monkeypatch.setattr(harness_ops, "craft_tool_manual", manual_tool)

    assert inventory.craft(client, "minecraft:iron_pickaxe", 1) is True

    craft_items = [payload["item"] for route, payload in transport.calls if route == "craft"]
    assert craft_items == [
        "minecraft:oak_planks",
        "minecraft:stick",
    ]
    assert transport.items["minecraft:iron_pickaxe"] == 2
    assert transport.items["minecraft:oak_log"] == 1
    assert transport.items["minecraft:stick"] >= 1


def test_crafting_action_uses_same_tool_dependency_guard(monkeypatch):
    transport = RecipeAwareTransport()
    context = SimpleNamespace(client=DummyClient(transport))
    action = CraftingAction()
    monkeypatch.setattr(harness_ops, "available", lambda: True)
    monkeypatch.setattr(action, "ensure_crafting_table", lambda _context: True)

    def manual_tool(_client, item_id):
        assert item_id == "minecraft:wooden_pickaxe"
        transport.items["minecraft:oak_planks"] -= 3
        transport.items["minecraft:stick"] -= 2
        transport.items[item_id] = transport.items.get(item_id, 0) + 1
        return True

    monkeypatch.setattr(harness_ops, "craft_tool_manual", manual_tool)

    assert action.craft(context, "minecraft:wooden_pickaxe", 1) is True

    craft_items = [payload["item"] for route, payload in transport.calls if route == "craft"]
    assert craft_items == ["minecraft:stick"]


def test_crafting_table_manual_fallback_does_not_require_existing_table(monkeypatch):
    transport = DummyTransport()
    client = DummyClient(transport)
    monkeypatch.setattr(inventory, "_wait_craft_result", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(harness_ops, "available", lambda: True)
    monkeypatch.setattr(harness_ops, "craft_crafting_table_manual", lambda _client: True)
    monkeypatch.setattr(
        harness_ops,
        "ensure_crafting_table_open",
        lambda _client: (_ for _ in ()).throw(AssertionError("must not recurse while crafting a table")),
    )

    assert inventory.craft(client, "minecraft:crafting_table", 1) is True


def test_furnace_manual_fallback_requires_verified_table(monkeypatch):
    transport = DummyTransport()
    client = DummyClient(transport)
    calls = []
    monkeypatch.setattr(inventory, "_wait_craft_result", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(harness_ops, "available", lambda: True)
    monkeypatch.setattr(
        harness_ops,
        "ensure_crafting_table_open",
        lambda _client: calls.append("table") or True,
    )
    monkeypatch.setattr(
        harness_ops,
        "craft_furnace_manual",
        lambda _client: calls.append("furnace") or True,
    )

    assert inventory.craft(client, "minecraft:furnace", 1) is True
    assert calls == ["table", "furnace"]


def test_bucket_manual_fallback_places_iron_v_recipe(monkeypatch):
    # auto_craft's hardcoded recipe slice has no bucket; the manual grid
    # fallback must drive the iron V shape (grid slots 1, 3, 5).
    transport = DummyTransport()
    client = DummyClient(transport)
    calls = []
    monkeypatch.setattr(inventory, "_wait_craft_result", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(inventory, "count_item", lambda _client, item_id: 1 if item_id == "minecraft:crafting_table" else 0)
    monkeypatch.setattr(harness_ops, "available", lambda: True)
    monkeypatch.setattr(
        harness_ops,
        "ensure_crafting_table_open",
        lambda _client: calls.append("table") or True,
    )

    def manual_recipe(_client, result_id, placements, crafts=1, output_per_recipe=1):
        calls.append(("recipe", result_id, tuple(placements), crafts))
        return True

    monkeypatch.setattr(harness_ops, "craft_recipe_manual", manual_recipe)

    assert inventory.craft(client, "minecraft:bucket", 1) is True
    assert calls == [
        "table",
        (
            "recipe",
            "minecraft:bucket",
            (
                ("minecraft:iron_ingot", 1),
                ("minecraft:iron_ingot", 3),
                ("minecraft:iron_ingot", 5),
            ),
            1,
        ),
    ]


def test_crafting_action_routes_furnace_to_manual_harness(monkeypatch):
    monkeypatch.setattr(
        harness_ops,
        "craft_furnace_manual",
        lambda client: client == "client",
    )

    fallback = CraftingAction._manual_fallback_for("minecraft:furnace")

    assert fallback is not None
    assert fallback("client")


def test_equip_best_armor_maps_hotbar_slot_and_verifies_bridge_armor():
    class ArmorTransport:
        def __init__(self):
            self.inventory_items = [
                {
                    "slot": 0,
                    "id": "minecraft:iron_helmet",
                    "count": 1,
                }
            ]
            self.armor_items = [
                {"slot": 36, "id": "minecraft:air", "count": 0},
                {"slot": 37, "id": "minecraft:air", "count": 0},
                {"slot": 38, "id": "minecraft:air", "count": 0},
                {"slot": 39, "id": "minecraft:air", "count": 0},
            ]
            self.calls = []

        def dispatch(self, route, payload):
            self.calls.append((route, payload))
            if route == "get_inventory":
                return {
                    "inventory": list(self.inventory_items),
                    "armor": list(self.armor_items),
                    "offhand": [],
                }
            if route == "inventory_click" and payload["slot"] == 36:
                self.inventory_items = []
                self.armor_items[3] = {
                    "slot": 39,
                    "id": "minecraft:iron_helmet",
                    "count": 1,
                }
            return {}

    transport = ArmorTransport()
    client = DummyClient(transport)

    assert inventory.equip_best_armor(client) == 1
    assert (
        "inventory_click",
        {"slot": 36, "type": "QUICK_MOVE", "button": 0},
    ) in transport.calls
    assert inventory.get_equipped_armor(client) == {
        "helmet": "minecraft:iron_helmet"
    }


def test_full_armor_verification_reads_equipped_section_only():
    class EquippedTransport:
        def dispatch(self, route, _payload):
            if route == "get_inventory":
                return {
                    "inventory": [],
                    "armor": [
                        {"id": "minecraft:iron_helmet", "count": 1},
                        {"id": "minecraft:iron_chestplate", "count": 1},
                        {"id": "minecraft:iron_leggings", "count": 1},
                        {"id": "minecraft:iron_boots", "count": 1},
                    ],
                    "offhand": [],
                }
            return {}

    assert inventory.has_full_armor(
        DummyClient(EquippedTransport()),
        minimum_material="iron",
    )


def test_deposit_excess_uses_live_chest_screen_player_slots_only():
    class ChestTransport:
        def __init__(self):
            self.clicks = []

        def dispatch(self, route, payload):
            if route == "get_block":
                return {"id": "minecraft:chest"}
            if route == "get_state":
                return {"block_position": {"x": 1, "y": 65, "z": 1}}
            if route == "get_screen":
                slots = [
                    {"slot": index, "id": "minecraft:air", "count": 0}
                    for index in range(63)
                ]
                slots[27] = {
                    "slot": 27,
                    "id": "minecraft:rotten_flesh",
                    "count": 4,
                }
                slots[28] = {
                    "slot": 28,
                    "id": "minecraft:diamond",
                    "count": 5,
                }
                return {
                    "sync_id": 7,
                    "total_slots": 63,
                    "slots": slots,
                }
            if route == "inventory_click":
                self.clicks.append(payload)
            return {}

    transport = ChestTransport()
    client = DummyClient(transport)

    assert inventory.deposit_excess_to_chest(client, (1, 65, 1)) == 1
    assert transport.clicks == [
        {
            "slot": 27,
            "type": "QUICK_MOVE",
            "button": 0,
            "sync_id": 7,
        }
    ]


def test_withdraw_required_uses_chest_slots_and_leaves_other_storage(monkeypatch):
    class ChestTransport:
        def __init__(self):
            self.clicks = []

        def dispatch(self, route, payload):
            if route == "get_inventory":
                return {"inventory": [], "armor": [], "offhand": []}
            if route == "get_block":
                return {"id": "minecraft:chest"}
            if route == "get_screen":
                slots = [
                    {"slot": index, "id": "minecraft:air", "count": 0}
                    for index in range(63)
                ]
                slots[3] = {
                    "slot": 3,
                    "id": "minecraft:leather",
                    "count": 2,
                }
                slots[4] = {
                    "slot": 4,
                    "id": "minecraft:redstone",
                    "count": 11,
                }
                return {"sync_id": 8, "total_slots": 63, "slots": slots}
            if route == "inventory_click":
                self.clicks.append(payload)
            return {}

    transport = ChestTransport()
    client = DummyClient(transport)
    monkeypatch.setattr(
        "baritone_client.common.harness_ops.open_container",
        lambda *_args, **_kwargs: True,
    )

    assert inventory.withdraw_required_from_chest(
        client,
        (1, 65, 1),
        {"minecraft:leather": 46},
    ) == 1
    assert transport.clicks == [
        {
            "slot": 3,
            "type": "QUICK_MOVE",
            "button": 0,
            "sync_id": 8,
        }
    ]


def test_deposit_loads_persisted_chest_chunk_before_rejecting_it(monkeypatch):
    class UnloadedChestTransport:
        def __init__(self):
            self.loaded = False

        def dispatch(self, route, payload):
            if route == "get_block":
                return {"id": "minecraft:chest" if self.loaded else "minecraft:void_air"}
            if route == "get_state":
                position = {"x": 1, "y": 65, "z": 1} if self.loaded else {
                    "x": 40, "y": 65, "z": 40
                }
                return {"block_position": position}
            if route == "get_screen":
                return {
                    "sync_id": 9,
                    "total_slots": 63,
                    "slots": [
                        {"slot": index, "id": "minecraft:air", "count": 0}
                        for index in range(63)
                    ],
                }
            return {}

    transport = UnloadedChestTransport()
    client = DummyClient(transport)
    moves = []

    def load_chunk(_client, x, y, z, **_kwargs):
        moves.append((x, y, z))
        transport.loaded = True
        return True

    monkeypatch.setattr("baritone_client.common.navigation.goto", load_chunk)
    monkeypatch.setattr(harness_ops, "available", lambda: True)
    monkeypatch.setattr(harness_ops, "open_container", lambda *_args, **_kwargs: True)

    assert inventory.deposit_excess_to_chest(client, (1, 65, 1)) == 0
    assert moves == [(1, 65, 1)]


def test_deposit_resumes_long_chest_return_after_progress_timeout(monkeypatch):
    class DistantChestTransport:
        def __init__(self):
            self.loaded = False
            self.position = {"x": 600, "y": 65, "z": 1}

        def dispatch(self, route, _payload):
            if route == "get_block":
                return {
                    "id": (
                        "minecraft:chest"
                        if self.loaded else "minecraft:void_air"
                    )
                }
            if route == "get_state":
                return {"block_position": dict(self.position)}
            if route == "get_screen":
                return {
                    "sync_id": 9,
                    "total_slots": 63,
                    "slots": [
                        {"slot": index, "id": "minecraft:air", "count": 0}
                        for index in range(63)
                    ],
                }
            return {}

    transport = DistantChestTransport()
    client = DummyClient(transport)
    moves = []

    def load_chunk(_client, x, y, z, **_kwargs):
        moves.append((x, y, z))
        if len(moves) == 1:
            transport.position = {"x": 200, "y": 65, "z": 1}
            return False
        transport.position = {"x": 1, "y": 65, "z": 1}
        transport.loaded = True
        return True

    monkeypatch.setattr("baritone_client.common.navigation.goto", load_chunk)
    monkeypatch.setattr(harness_ops, "available", lambda: True)
    monkeypatch.setattr(
        harness_ops, "open_container", lambda *_args, **_kwargs: True
    )

    assert inventory.deposit_excess_to_chest(client, (1, 65, 1)) == 0
    assert moves == [(1, 65, 1), (1, 65, 1)]


def test_storage_location_resolves_from_production_checkpoint_state(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    class Transport:
        def dispatch(self, route, payload):
            if route == "get_block":
                assert payload == {"x": 10, "y": 64, "z": 20}
                return {"id": "minecraft:chest"}
            return {}

    state = SimpleNamespace(
        get_locations=lambda category: {
            category: [{"x": 10, "y": 64, "z": 20}]
        }
    )

    assert inventory.resolve_storage_location(
        DummyClient(Transport()), state=state
    ) == (10, 64, 20)


def test_persist_storage_writes_both_checkpoint_formats(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    class Transport:
        def dispatch(self, route, _payload):
            if route == "get_block":
                return {"id": "minecraft:chest"}
            if route == "get_inventory":
                return {"inventory": []}
            return {}

    class State:
        def __init__(self):
            self.locations = []
            self.saved = []

        def add_location(self, category, x, y, z, **_kwargs):
            self.locations.append((category, x, y, z))

        def save_checkpoint(self, inventory_summary):
            self.saved.append(inventory_summary)

    state = State()
    assert inventory.persist_storage_location(
        DummyClient(Transport()), (10, 64, 20), state=state
    )

    compatibility = json.loads(
        (tmp_path / "checkpoint_storage.json").read_text(encoding="utf-8")
    )
    assert compatibility["data"] == {"x": 10, "y": 64, "z": 20}
    assert state.locations == [("chest", 10, 64, 20)]
    assert state.saved == [{}]


def test_dump_to_chest_uses_production_location_and_verified_screen(
    monkeypatch, tmp_path
):
    monkeypatch.chdir(tmp_path)

    class ChestTransport:
        def __init__(self):
            self.clicks = []

        def dispatch(self, route, payload):
            if route == "get_block":
                return {"id": "minecraft:chest"}
            if route == "get_state":
                return {"block_position": {"x": 11, "y": 64, "z": 20}}
            if route == "get_screen":
                slots = [
                    {"slot": index, "id": "minecraft:air", "count": 0}
                    for index in range(63)
                ]
                slots[27] = {
                    "slot": 27,
                    "id": "minecraft:dirt",
                    "count": 64,
                }
                slots[28] = {
                    "slot": 28,
                    "id": "minecraft:stone_pickaxe",
                    "count": 1,
                }
                return {
                    "sync_id": 9,
                    "total_slots": 63,
                    "slots": slots,
                }
            if route == "inventory_click":
                self.clicks.append(payload)
            return {}

    state = SimpleNamespace(
        get_locations=lambda category: {
            category: [{"x": 10, "y": 64, "z": 20}]
        }
    )
    transport = ChestTransport()
    monkeypatch.setattr(harness_ops, "open_container", lambda *_args, **_kwargs: True)

    moved = inventory.dump_to_chest(
        DummyClient(transport),
        keep_items=["minecraft:stone_pickaxe"],
        state=state,
    )

    assert moved == 1
    assert transport.clicks == [
        {
            "slot": 27,
            "type": "QUICK_MOVE",
            "button": 0,
            "sync_id": 9,
        }
    ]


def test_dump_to_chest_rejects_stale_saved_location(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    class Transport:
        def dispatch(self, route, _payload):
            if route == "get_block":
                return {"id": "minecraft:air"}
            return {}

    state = SimpleNamespace(
        get_locations=lambda category: {
            category: [{"x": 10, "y": 64, "z": 20}]
        }
    )

    assert (
        inventory.dump_to_chest(
            DummyClient(Transport()), keep_items=[], state=state
        )
        == -1
    )


def test_stick_plank_prep_gathers_logs_when_none_carried(monkeypatch):
    # Live blocker: worn iron pickaxe replacement needed sticks, sticks needed
    # planks, the bot carried zero logs, and plank prep failed closed without
    # ever gathering wood. It must acquire a small log reserve instead.
    from baritone_client.common import resources

    client = DummyClient(DummyTransport())
    counts = {"minecraft:stick": 0, "minecraft:oak_log": 0, "minecraft:oak_planks": 0}
    gathered = []

    def fake_gather_wood(_client, count=16, **_kwargs):
        gathered.append(count)
        counts["minecraft:oak_log"] = count
        return True

    def fake_craft(_client, item_id, qty):
        if item_id.endswith("_planks"):
            counts["minecraft:oak_planks"] += qty
            return True
        return False

    monkeypatch.setattr(inventory, "count_item", lambda _c, item: counts.get(item, 0))
    monkeypatch.setattr(
        inventory,
        "_craft_family_count",
        lambda _c, item: counts.get("minecraft:oak_planks", 0)
        if item.endswith("_planks")
        else counts.get(item, 0),
    )
    monkeypatch.setattr(resources, "gather_wood", fake_gather_wood)
    monkeypatch.setattr(inventory, "craft", fake_craft)

    assert inventory._ensure_planks_for_sticks(client, required_sticks=2)
    assert gathered == [1]  # 4 planks needed -> 1 log reserve


def test_stick_plank_prep_still_fails_closed_when_no_wood_exists(monkeypatch):
    from baritone_client.common import resources

    client = DummyClient(DummyTransport())
    monkeypatch.setattr(inventory, "count_item", lambda _c, _item: 0)
    monkeypatch.setattr(inventory, "_craft_family_count", lambda _c, _item: 0)
    monkeypatch.setattr(resources, "gather_wood", lambda *_a, **_k: False)

    assert not inventory._ensure_planks_for_sticks(client, required_sticks=2)


def test_manual_grid_recipe_shapes_match_vanilla():
    # Ingredient totals per vanilla; a wrong shape silently crafts nothing.
    expected_counts = {
        "minecraft:stick": {"#planks": 2},
        "minecraft:bucket": {"minecraft:iron_ingot": 3},
        "minecraft:shield": {"#planks": 6, "minecraft:iron_ingot": 1},
        "minecraft:flint_and_steel": {"minecraft:iron_ingot": 1, "minecraft:flint": 1},
        "minecraft:paper": {"minecraft:sugar_cane": 3},
        "minecraft:book": {"minecraft:paper": 3, "minecraft:leather": 1},
        "minecraft:bookshelf": {"#planks": 6, "minecraft:book": 3},
        "minecraft:enchanting_table": {
            "minecraft:book": 1,
            "minecraft:diamond": 2,
            "minecraft:obsidian": 4,
        },
        "minecraft:blaze_powder": {"minecraft:blaze_rod": 1},
        "minecraft:ender_eye": {
            "minecraft:blaze_powder": 1,
            "minecraft:ender_pearl": 1,
        },
        "minecraft:bow": {"minecraft:stick": 3, "minecraft:string": 3},
        "minecraft:arrow": {
            "minecraft:flint": 1,
            "minecraft:stick": 1,
            "minecraft:feather": 1,
        },
        "minecraft:ladder": {"minecraft:stick": 7},
    }
    assert set(inventory._MANUAL_GRID_RECIPES) == set(expected_counts)
    for item_id, spec in inventory._MANUAL_GRID_RECIPES.items():
        placements = spec["placements"]
        slots = [slot for _sel, slot in placements]
        assert len(slots) == len(set(slots)), f"{item_id}: duplicate grid slot"
        assert all(1 <= slot <= 9 for slot in slots), f"{item_id}: slot out of range"
        counts = {}
        for selector, _slot in placements:
            counts[selector] = counts.get(selector, 0) + 1
        assert counts == expected_counts[item_id], f"{item_id}: wrong ingredients"


def test_every_manual_grid_recipe_routes_to_manual_driver(monkeypatch):
    driven = []

    def fake_recipe_manual(_client, result_id, placements, crafts=1, output_per_recipe=1):
        driven.append((result_id, tuple(placements), crafts, output_per_recipe))
        return True

    for item_id, spec in inventory._MANUAL_GRID_RECIPES.items():
        driven.clear()
        transport = DummyTransport()
        client = DummyClient(transport)
        monkeypatch.setattr(inventory, "_wait_craft_result", lambda *_a, **_k: False)
        monkeypatch.setattr(
            inventory,
            "count_item",
            lambda _c, item: 1 if item == "minecraft:crafting_table" else 0,
        )
        monkeypatch.setattr(harness_ops, "available", lambda: True)
        monkeypatch.setattr(harness_ops, "ensure_crafting_table_open", lambda _c: True)
        monkeypatch.setattr(harness_ops, "craft_recipe_manual", fake_recipe_manual)

        assert inventory.craft(client, item_id, 1) is True, item_id
        output = spec.get("output", 1)
        assert driven == [(item_id, tuple(spec["placements"]), 1, output)], item_id


def test_manual_grid_recipe_scales_crafts_for_multi_output(monkeypatch):
    # 6 arrows at 4 per craft -> 2 grid cycles.
    driven = []

    def fake_recipe_manual(_client, result_id, placements, crafts=1, output_per_recipe=1):
        driven.append((crafts, output_per_recipe))
        return True

    transport = DummyTransport()
    client = DummyClient(transport)
    monkeypatch.setattr(inventory, "_wait_craft_result", lambda *_a, **_k: False)
    monkeypatch.setattr(
        inventory,
        "count_item",
        lambda _c, item: 1 if item == "minecraft:crafting_table" else 0,
    )
    monkeypatch.setattr(harness_ops, "available", lambda: True)
    monkeypatch.setattr(harness_ops, "ensure_crafting_table_open", lambda _c: True)
    monkeypatch.setattr(harness_ops, "craft_recipe_manual", fake_recipe_manual)

    assert inventory.craft(client, "minecraft:arrow", 6) is True
    assert driven == [(2, 4)]


def test_craft_recipe_manual_uses_bridge_place_recipe(monkeypatch):
    calls = []

    class DummyTransport:
        def dispatch(self, route, payload):
            calls.append((route, payload))
            if route == "place_recipe":
                return {"data": {"crafted": True, "crafts_completed": 1}}
            return {}

    class DummyClient:
        def __init__(self, transport):
            self.transport = transport

    transport = DummyTransport()
    client = DummyClient(transport)

    placements = [("minecraft:iron_ingot", 1), ("#planks", 4)]

    res = harness_ops.place_recipe(client, "minecraft:shield", placements, crafts=1, output_per_recipe=1)
    assert res is True
    assert len(calls) == 1
    assert calls[0][0] == "place_recipe"
    assert calls[0][1] == {
        "placements": [
            {"selector": "minecraft:iron_ingot", "grid_slot": 1},
            {"selector": "#planks", "grid_slot": 4},
        ],
        "expected_output": "minecraft:shield",
        "expected_count": 1,
        "crafts": 1,
    }


def test_craft_recipe_manual_bridge_fallback(monkeypatch):
    from tests.functional.shared import inventory_ops

    class DummyTransport:
        def __init__(self):
            self.calls = []

        def dispatch(self, route, payload):
            self.calls.append((route, payload))
            if route == "place_recipe":
                raise RuntimeError("Unsupported command")
            if route == "get_screen":
                return {
                    "type": "class_1714",
                    "slots": [
                        {"slot": 0, "id": "minecraft:shield", "count": 1},
                        *[{"slot": slot, "id": "minecraft:air", "count": 0} for slot in range(1, 10)],
                        {"slot": 10, "id": "minecraft:iron_ingot", "count": 1},
                        {"slot": 11, "id": "minecraft:oak_planks", "count": 1},
                        *[{"slot": slot, "id": "minecraft:air", "count": 0} for slot in range(12, 46)],
                    ]
                }
            return {}

    class DummyContext:
        def __init__(self, transport):
            self.client = DummyClient(transport)
            self._books = 0

        def log_event(self, _event):
            pass

    class DummyClient:
        def __init__(self, transport):
            self.transport = transport

    # We mock do_close_container to avoid sleeping
    # and to simulate successful click / output capture
    monkeypatch.setattr(inventory_ops, "do_close_container", lambda _ctx: None)
    monkeypatch.setattr(inventory_ops.time, "sleep", lambda _seconds: None)

    transport = DummyTransport()
    ctx = DummyContext(transport)

    placements = [("minecraft:iron_ingot", 1), ("#planks", 4)]

    # We mock count_item to simulate item crafted
    count_calls = 0
    def mock_count_item(item_id):
        nonlocal count_calls
        count_calls += 1
        if count_calls > 1:
            return 1 # after craft
        return 0
    ctx.count_item = mock_count_item

    # This should fall back to python clicks and return true
    res = inventory_ops.craft_recipe_manual(ctx, "minecraft:shield", placements, crafts=1, output_per_recipe=1)
    assert res is True

    # Verify we tried place_recipe and then did get_screen and inventory_clicks
    routes = [r for r, p in transport.calls]
    assert "place_recipe" in routes
    assert "get_screen" in routes
    assert "inventory_click" in routes


def test_harness_fallback_does_not_submit_atomic_recipe_twice(monkeypatch):
    calls = []

    class DummyTransport:
        def dispatch(self, route, payload):
            calls.append((route, payload))
            if route == "place_recipe":
                return {"data": {"crafted": False, "error": "output_not_collected"}}
            return {}

    client = DummyClient(DummyTransport())
    fallback_args = {}

    monkeypatch.setattr(
        harness_ops,
        "_load",
        lambda: {
            "ensure_crafting_output_space": lambda _ctx: True,
            "craft_recipe_manual": lambda _ctx, result_id, placements, **kwargs: (
                fallback_args.update(
                    result_id=result_id,
                    placements=placements,
                    **kwargs,
                )
                or True
            ),
            "TestContext": lambda client: type(
                "Context", (), {"client": client, "log_event": lambda *_args: None}
            )(),
        },
    )

    assert harness_ops.craft_recipe_manual(
        client,
        "minecraft:bucket",
        [("minecraft:iron_ingot", 1), ("minecraft:iron_ingot", 3), ("minecraft:iron_ingot", 5)],
    )
    assert [route for route, _payload in calls].count("place_recipe") == 1
    assert fallback_args["try_bridge"] is False


def test_plank_requirement_uses_batch_yield_not_one_per_stick(monkeypatch):
    # 2 planks -> 4 sticks. 3 sticks needs 2 planks (one batch), NOT 6.
    # Regression for the FOOD_AND_IRON deep-mining-prep deadlock: with 5 planks
    # and no logs, the old `sticks*2` formula demanded 6 and failed to gather
    # a log it did not need.
    client = DummyClient(DummyTransport())
    gathered = []
    monkeypatch.setattr(inventory, "_craft_family_count", lambda _c, _item: 5)
    monkeypatch.setattr(
        inventory,
        "_first_log_family_with_stock",
        lambda _c, _n=1: gathered.append("looked_for_log") or "",
    )

    # 5 planks already cover 3 sticks (needs 2) -> returns True, never touches logs
    assert inventory._ensure_planks_for_sticks(client, required_sticks=3)
    assert gathered == [], "must not look for logs when planks already suffice"


def test_plank_requirement_batches_round_up(monkeypatch):
    # 5 sticks -> 2 batches -> 4 planks required.
    seen = {}
    client = DummyClient(DummyTransport())
    monkeypatch.setattr(inventory, "_craft_family_count", lambda _c, _item: 4)
    monkeypatch.setattr(
        inventory,
        "_first_log_family_with_stock",
        lambda _c, _n=1: seen.setdefault("looked", True) or "",
    )
    assert inventory._ensure_planks_for_sticks(client, required_sticks=5)
    assert "looked" not in seen  # 4 planks exactly cover 2 batches


def test_drop_items_maps_hotbar_inventory_index_to_player_handler(monkeypatch):
    items = [{"slot": 0, "id": "minecraft:dirt", "count": 64}]

    class Transport:
        def __init__(self):
            self.calls = []

        def dispatch(self, route, payload):
            self.calls.append((route, payload))
            if route == "get_inventory":
                return {"data": {"inventory": list(items)}}
            if route == "inventory_click":
                assert payload == {"slot": 36, "type": "THROW", "button": 1}
                items.clear()
            return {}

    transport = Transport()
    monkeypatch.setattr(inventory.time, "sleep", lambda _seconds: None)

    assert inventory.drop_items(
        DummyClient(transport), ["minecraft:dirt"], max_stacks=1
    ) == 1
    assert any(route == "close_screen" for route, _payload in transport.calls)


def test_drop_items_can_remove_duplicate_tool_while_retaining_one(monkeypatch):
    items = [
        {"slot": 0, "id": "minecraft:shears", "count": 1},
        {"slot": 1, "id": "minecraft:shears", "count": 1},
    ]

    class Transport:
        def dispatch(self, route, payload):
            if route == "get_inventory":
                return {"data": {"inventory": list(items)}}
            if route == "inventory_click":
                inventory_slot = int(payload["slot"]) - 36
                items[:] = [item for item in items if item["slot"] != inventory_slot]
            return {}

    monkeypatch.setattr(inventory.time, "sleep", lambda _seconds: None)
    client = DummyClient(Transport())

    assert inventory.drop_items(
        client,
        ["minecraft:shears"],
        max_stacks=1,
        retain_counts={"minecraft:shears": 1},
    ) == 1
    assert sum(item["count"] for item in items) == 1


def test_storage_resolver_reads_starter_house_supply_chest(monkeypatch):
    state = SimpleNamespace(
        custom_data={
            "structures": {
                "starter_house": {"supply_chest": [12, 70, -4]}
            }
        }
    )
    assert inventory.resolve_storage_location(
        DummyClient(DummyTransport()), state=state, verify=False
    ) == (12, 70, -4)
