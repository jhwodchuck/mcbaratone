from baritone_client.common import inventory
from baritone_client.common import harness_ops
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


def test_tool_craft_prepares_sticks_before_pickaxe():
    transport = RecipeAwareTransport()
    client = DummyClient(transport)

    assert inventory.craft(client, "minecraft:wooden_pickaxe", 1) is True

    craft_items = [payload["item"] for route, payload in transport.calls if route == "craft"]
    assert craft_items == ["minecraft:stick", "minecraft:wooden_pickaxe"]
    assert transport.items["minecraft:wooden_pickaxe"] == 1


def test_food_and_iron_resumes_from_log_state_for_second_pickaxe():
    transport = FoodAndIronTransport()
    client = DummyClient(transport)

    assert inventory.craft(client, "minecraft:iron_pickaxe", 1) is True

    craft_items = [payload["item"] for route, payload in transport.calls if route == "craft"]
    assert craft_items == [
        "minecraft:oak_planks",
        "minecraft:stick",
        "minecraft:iron_pickaxe",
    ]
    assert transport.items["minecraft:iron_pickaxe"] == 2
    assert transport.items["minecraft:oak_log"] == 1
    assert transport.items["minecraft:stick"] >= 1


def test_crafting_action_uses_same_tool_dependency_guard():
    transport = RecipeAwareTransport()
    context = SimpleNamespace(client=DummyClient(transport))

    assert CraftingAction().craft(context, "minecraft:wooden_pickaxe", 1) is True

    craft_items = [payload["item"] for route, payload in transport.calls if route == "craft"]
    assert craft_items == ["minecraft:stick", "minecraft:wooden_pickaxe"]


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
