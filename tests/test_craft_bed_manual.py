"""craft_bed_manual crafts the bed of whatever wool is actually in the grid."""

from types import SimpleNamespace

from tests.functional.shared import inventory_ops


class _Ctx:
    def __init__(self, wool):
        self.inventory = {"minecraft:oak_planks": 6, **wool}
        self.crafted = set()
        self.cursor = ""
        self.grid_wool = ""
        slots = [{"slot": 10 + n, "id": item, "count": count} for n, (item, count) in enumerate(self.inventory.items())]
        self.client = SimpleNamespace(transport=SimpleNamespace(dispatch=self._dispatch))
        self._slots = slots

    def _dispatch(self, route, payload):
        if route == "get_screen":
            return {"type": "CraftingScreenHandler", "slots": self._slots}
        if route == "inventory_click":
            slot, kind, button = payload.get("slot"), payload.get("type"), payload.get("button", 0)
            by_slot = {s["slot"]: s["id"] for s in self._slots}
            if kind == "PICKUP" and button == 0 and slot in by_slot:
                self.cursor = by_slot[slot]  # picked a stack up
            elif kind == "PICKUP" and button == 1 and slot == 1:
                self.grid_wool = self.cursor if self.cursor.endswith("_wool") else self.grid_wool
            elif slot == 0 and self.grid_wool:
                # The output click: the bed made from the wool that was placed.
                self.crafted.add(self.grid_wool.replace("_wool", "_bed"))
        return {"success": True}

    def log_event(self, *_a, **_k):
        pass

    def get_state(self):
        return {"screen": "crafting"}

    def has_item(self, item):
        return item in self.crafted


def _craft(ctx, bed_id):
    import time as _t
    original = _t.sleep
    _t.sleep = lambda _s: None
    try:
        return inventory_ops.craft_bed_manual(ctx, bed_id)
    finally:
        _t.sleep = original


def test_a_black_wool_stack_makes_a_black_bed_even_when_white_is_requested():
    ctx = _Ctx({"minecraft:black_wool": 3})
    assert _craft(ctx, "minecraft:white_bed") is True
    assert ctx.crafted == {"minecraft:black_bed"}


def test_the_requested_colour_is_preferred_when_two_stacks_could_make_a_bed():
    ctx = _Ctx({"minecraft:black_wool": 3, "minecraft:white_wool": 3})
    assert _craft(ctx, "minecraft:white_bed") is True
    assert ctx.crafted == {"minecraft:white_bed"}


def test_fewer_than_three_of_one_colour_is_not_a_bed():
    ctx = _Ctx({"minecraft:black_wool": 2, "minecraft:white_wool": 2})
    assert _craft(ctx, "minecraft:white_bed") is False
