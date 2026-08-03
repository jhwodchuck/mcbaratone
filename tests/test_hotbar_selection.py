from tests.utils.mc_harness import inventory
from baritone_client.common import inventory as common_inventory


class _Context:
    def __init__(self, logical_inventory, *, mutate_swap=True, mutate_select=True):
        self.logical_inventory = {
            int(entry["slot"]): dict(entry) for entry in logical_inventory
        }
        self.calls = []
        self.selected_slot = 0
        self.mutate_swap = mutate_swap
        self.mutate_select = mutate_select
        self.client = type("Client", (), {"transport": self})()

    def dispatch(self, route, payload, **_kwargs):
        self.calls.append((route, dict(payload)))
        if route == "get_inventory":
            return {
                "inventory": [
                    self.logical_inventory.get(
                        slot,
                        {"slot": slot, "id": "minecraft:air", "count": 0},
                    )
                    for slot in range(36)
                ],
                "selected_slot": self.selected_slot,
            }
        if route == "inventory_click":
            menu_slot = int(payload["slot"])
            logical_slot = menu_slot - 36 if 36 <= menu_slot <= 44 else menu_slot
            assert payload["type"] == "SWAP"
            target_slot = int(payload["button"])
            if self.mutate_swap:
                source = dict(self.logical_inventory[logical_slot])
                target = dict(
                    self.logical_inventory.get(
                        target_slot,
                        {"slot": target_slot, "id": "minecraft:air", "count": 0},
                    )
                )
                source["slot"], target["slot"] = target_slot, logical_slot
                self.logical_inventory[target_slot] = source
                self.logical_inventory[logical_slot] = target
            return {"clicked": True}
        if route == "select_slot":
            if self.mutate_select:
                self.selected_slot = int(payload["slot"])
            return {"success": True}
        return {}

    def log_event(self, _event):
        return None


def test_player_inventory_menu_slot_maps_hotbar_after_crafting_slots():
    assert inventory.player_inventory_menu_slot(0) == 36
    assert inventory.player_inventory_menu_slot(8) == 44
    assert inventory.player_inventory_menu_slot(9) == 9
    assert inventory.player_inventory_menu_slot(35) == 35


def test_ensure_item_in_hotbar_targets_container_slot_36(monkeypatch):
    ctx = _Context(
        [
            {"slot": 0, "id": "minecraft:iron_sword", "count": 1},
            {"slot": 10, "id": "minecraft:cobblestone", "count": 64},
        ]
    )
    monkeypatch.setattr(inventory.time, "sleep", lambda _seconds: None)

    assert inventory.ensure_item_in_hotbar(ctx, "minecraft:cobblestone") == 1
    clicks = [payload for route, payload in ctx.calls if route == "inventory_click"]
    assert clicks == [{"slot": 10, "type": "SWAP", "button": 1}]
    assert ctx.logical_inventory[0]["id"] == "minecraft:iron_sword"
    assert ctx.logical_inventory[1]["id"] == "minecraft:cobblestone"


def test_full_hotbar_swap_returns_displaced_stack_to_source(monkeypatch):
    logical_inventory = [
        {"slot": slot, "id": f"minecraft:occupied_{slot}", "count": 1}
        for slot in range(9)
    ]
    logical_inventory.append(
        {"slot": 10, "id": "minecraft:cobblestone", "count": 64}
    )
    ctx = _Context(logical_inventory)
    monkeypatch.setattr(inventory.time, "sleep", lambda _seconds: None)

    assert inventory.ensure_item_in_hotbar(ctx, "minecraft:cobblestone") == 0
    clicks = [payload for route, payload in ctx.calls if route == "inventory_click"]
    assert clicks == [{"slot": 10, "type": "SWAP", "button": 0}]
    assert ctx.logical_inventory[0]["id"] == "minecraft:cobblestone"
    assert ctx.logical_inventory[10]["id"] == "minecraft:occupied_0"


def test_accepted_but_unobserved_swap_fails_closed(monkeypatch):
    ctx = _Context(
        [{"slot": 33, "id": "minecraft:furnace", "count": 1}],
        mutate_swap=False,
    )
    monkeypatch.setattr(inventory.time, "sleep", lambda _seconds: None)

    assert inventory.ensure_item_in_hotbar(ctx, "minecraft:furnace") is None


def test_accepted_but_unobserved_selection_fails_closed(monkeypatch):
    ctx = _Context(
        [{"slot": 2, "id": "minecraft:cobblestone", "count": 64}],
        mutate_select=False,
    )
    monkeypatch.setattr(inventory.time, "sleep", lambda _seconds: None)

    assert not inventory.select_hotbar_item(ctx, "minecraft:cobblestone")


def test_common_selector_atomically_swaps_and_verifies_selection():
    ctx = _Context(
        [
            {"slot": 0, "id": "minecraft:iron_sword", "count": 1},
            {"slot": 33, "id": "minecraft:furnace", "count": 1},
        ]
    )

    assert common_inventory.select_item(
        ctx.client, "minecraft:furnace", allow_swap=True
    )
    assert ctx.logical_inventory[1]["id"] == "minecraft:furnace"
    assert ctx.selected_slot == 1


def test_common_selector_rejects_accepted_but_ignored_swap():
    ctx = _Context(
        [{"slot": 34, "id": "minecraft:chest", "count": 1}],
        mutate_swap=False,
    )

    assert not common_inventory.select_item(
        ctx.client, "minecraft:chest", allow_swap=True
    )
