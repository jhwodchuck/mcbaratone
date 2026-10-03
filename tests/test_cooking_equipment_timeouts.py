"""Offline inventory-pressure and mutation-reconciliation regressions."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from baritone_client.common import inventory
from tests.functional.shared import inventory_ops


def stack(item="minecraft:air", count=0, **extra):
    return {"id": item, "count": count, "damage": 0, "components": {}, **extra}


class ArmorTransport:
    def __init__(self, source=22, fail_click=None, noop=False):
        self.main = [stack("minecraft:enchanted_book", 1) for _ in range(36)]
        self.main[0] = stack("minecraft:stone_pickaxe", 1)
        self.main[source] = stack("minecraft:iron_helmet", 1, max_damage=165)
        self.worn = stack("minecraft:iron_helmet", 1, damage=120, max_damage=165)
        self.calls = []
        self.fail_click = fail_click
        self.noop = noop

    def dispatch(self, route, payload):
        if route == "get_inventory":
            return deepcopy({"inventory": [dict(s, slot=i) for i, s in enumerate(self.main)],
                             "armor": [dict(self.worn, slot=39)]})
        if route == "inventory_click":
            self.calls.append(dict(payload))
            assert payload["type"] == "SWAP" and payload["button"] == 0
            assert payload["sync_id"] == 0
            slot = payload["slot"]
            if not self.noop:
                if slot == 5:
                    self.worn, self.main[0] = self.main[0], self.worn
                else:
                    logical = slot - 36 if slot >= 36 else slot
                    self.main[logical], self.main[0] = self.main[0], self.main[logical]
            if len(self.calls) == self.fail_click:
                raise RuntimeError("Observed effect deadline exceeded")
        return {"postcondition_verified": True}


@pytest.mark.parametrize("source", [0, 4, 22])
def test_armor_exchange_preserves_displaced_stacks_in_full_inventory(source):
    transport = ArmorTransport(source)
    before = deepcopy(transport.main[0])
    assert inventory.equip_best_armor(SimpleNamespace(transport=transport)) == 1
    assert transport.worn["damage"] == 0
    assert transport.main[source]["damage"] == 120
    if source != 0:
        assert transport.main[0] == before
    assert all(s["count"] == 1 for s in transport.main)
    assert len(transport.calls) == (1 if source == 0 else 3)


def test_armor_unknown_exchange_is_not_retried_or_speculatively_rolled_back(caplog):
    transport = ArmorTransport(fail_click=2)
    inventory.equip_best_armor(SimpleNamespace(transport=transport))
    assert len(transport.calls) == 2
    assert transport.worn["damage"] == 0
    assert "deadline exceeded" in caplog.text


def test_armor_uses_exact_durable_candidate_not_first_matching_id():
    transport = ArmorTransport()
    transport.main[0] = stack("minecraft:iron_helmet", 1, damage=140, max_damage=165)
    inventory.equip_best_armor(SimpleNamespace(transport=transport))
    assert transport.worn["damage"] == 0
    assert transport.main[0]["damage"] == 140
    assert transport.main[22]["damage"] == 120


def test_same_item_id_does_not_prove_fresh_armor_equipped(monkeypatch, caplog):
    transport = ArmorTransport(source=0, noop=True)
    ticks = iter(range(100))
    monkeypatch.setattr(inventory.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(inventory.time, "sleep", lambda _: None)
    inventory.equip_best_armor(SimpleNamespace(transport=transport))
    assert transport.worn["damage"] == 120
    assert len(transport.calls) == 1
    assert "not observed equipped" in caplog.text


class FurnaceContext:
    def __init__(self, full=False):
        self.slots = [stack() for _ in range(39)]
        if full:
            self.slots[3:] = [stack("minecraft:enchanted_book", 1, max_count=1) for _ in range(36)]
        self.slots[3] = stack("minecraft:beef", 4)
        self.slots[4] = stack("minecraft:oak_log", 3)
        self.client = SimpleNamespace(transport=self)
        self.calls = []
        self.events = []
        self.sync_id = 7
        self.loaded = False
        self.reads_after_load = 0
        self.load_result = {"moved": True, "postcondition_verified": True}
        self.fail_move = False
        self.closed = False

    def count_item(self, item):
        return sum(s["count"] for s in self.slots[3:] if s["id"] == item)

    def log_event(self, message):
        self.events.append(message)

    def dispatch(self, route, payload):
        if route == "get_screen":
            if self.loaded:
                self.reads_after_load += 1
                if self.reads_after_load == 3:
                    self.slots[2] = stack("minecraft:cooked_beef", self.slots[0]["count"])
                    self.slots[0] = stack()
            return deepcopy({"type": "FurnaceMenu", "sync_id": self.sync_id,
                             "slots": [dict(s, slot=i) for i, s in enumerate(self.slots)]})
        if route in {"smelt_items", "inventory_click"}:
            self.calls.append((route, dict(payload)))
            assert payload["sync_id"] == self.sync_id
        if route == "smelt_items":
            if isinstance(self.load_result, Exception):
                raise self.load_result
            for key, destination in (("input_slot", 0), ("fuel_slot", 1)):
                if key in payload:
                    source = payload[key]
                    self.slots[destination] = self.slots[source]
                    self.slots[source] = stack()
            self.loaded = True
            return self.load_result
        if route == "inventory_click":
            if self.fail_move:
                raise RuntimeError("unknown effect")
            source = payload["slot"]
            if payload["type"] == "THROW":
                assert source >= 3
                self.slots[source] = stack()
            else:
                assert payload["type"] == "QUICK_MOVE" and source < 3
                item = self.slots[source]
                destination = next((i for i in range(3, 39)
                                    if self.slots[i]["id"] == item["id"]
                                    and self.slots[i]["count"] + item["count"] <= 64), None)
                if destination is None:
                    destination = next(i for i in range(3, 39) if self.slots[i]["count"] == 0)
                if self.slots[destination]["count"]:
                    self.slots[destination]["count"] += item["count"]
                else:
                    self.slots[destination] = item
                self.slots[source] = stack()
            return {"postcondition_verified": True}
        return {}


@pytest.fixture
def furnace_run(monkeypatch):
    monkeypatch.setattr(inventory_ops, "block_id_at", lambda *_: "minecraft:furnace")
    monkeypatch.setattr(inventory_ops, "do_open_container", lambda *_a, **_k: True)
    monkeypatch.setattr(inventory_ops, "do_close_container", lambda ctx: setattr(ctx, "closed", True))
    monkeypatch.setattr(inventory_ops.time, "sleep", lambda _: None)
    return lambda ctx, count=4: inventory_ops.smelt_in_furnace(
        ctx, (1, 2, 3), "minecraft:beef", "minecraft:oak_log", "minecraft:cooked_beef", count)


def test_full_inventory_collects_existing_food_before_foreign_input(furnace_run):
    ctx = FurnaceContext(full=True)
    ctx.slots[0] = stack("minecraft:raw_iron", 2)
    ctx.slots[1] = stack("minecraft:oak_planks", 12)
    ctx.slots[2] = stack("minecraft:cooked_beef", 4)
    ctx.slots[5] = stack("minecraft:smooth_basalt", 1)
    assert furnace_run(ctx)
    assert ctx.count_item("minecraft:cooked_beef") == 4
    assert ctx.slots[0]["id"] == "minecraft:raw_iron"
    assert ctx.slots[1]["count"] == 12
    assert [r for r, _ in ctx.calls] == ["inventory_click", "inventory_click"]
    assert ctx.calls[0][1]["type"] == "THROW"
    assert ctx.calls[1][1]["slot"] == 2
    assert ctx.closed


def test_full_valuable_inventory_refuses_without_any_click(furnace_run):
    ctx = FurnaceContext(full=True)
    ctx.slots[2] = stack("minecraft:cooked_beef", 4)
    assert not furnace_run(ctx)
    assert ctx.calls == []
    assert ctx.closed


def test_full_inventory_merges_existing_output_without_discard(furnace_run):
    ctx = FurnaceContext(full=True)
    ctx.slots[5] = stack("minecraft:cooked_beef", 2, max_count=64)
    ctx.slots[2] = stack("minecraft:cooked_beef", 4)
    assert furnace_run(ctx)
    assert ctx.count_item("minecraft:cooked_beef") == 6
    assert len(ctx.calls) == 1 and ctx.calls[0][1]["type"] == "QUICK_MOVE"


def test_smeltable_logs_load_into_explicit_fuel_slot_and_output_is_observed(furnace_run):
    ctx = FurnaceContext(full=True)
    assert furnace_run(ctx)
    assert ctx.calls[0] == ("smelt_items", {"sync_id": 7, "input_slot": 3, "fuel_slot": 4})
    assert ctx.count_item("minecraft:cooked_beef") == 4
    assert ctx.reads_after_load >= 3
    assert all(p.get("type") != "THROW" for _, p in ctx.calls)


def test_existing_different_usable_fuel_is_not_evicted(furnace_run):
    ctx = FurnaceContext()
    ctx.slots[1] = stack("minecraft:coal", 10)
    assert furnace_run(ctx)
    assert ctx.calls[0] == ("smelt_items", {"sync_id": 7, "input_slot": 3})
    assert ctx.slots[1]["count"] == 10


def test_retained_fuel_running_out_is_refilled_from_fresh_evidence(furnace_run, monkeypatch):
    ctx = FurnaceContext()
    ctx.slots[1] = stack("minecraft:coal", 1)
    original = ctx.dispatch
    def dispatch(route, payload):
        result = original(route, payload)
        if route == "get_screen" and ctx.loaded and ctx.reads_after_load == 1:
            ctx.slots[1] = stack()
            result["slots"][1] = dict(stack(), slot=1)
        return result
    monkeypatch.setattr(ctx, "dispatch", dispatch)
    assert furnace_run(ctx)
    loads = [p for r, p in ctx.calls if r == "smelt_items"]
    assert loads == [{"sync_id": 7, "input_slot": 3}, {"sync_id": 7, "fuel_slot": 4}]


def test_unknown_space_reservation_is_not_followed_by_output_click(furnace_run):
    ctx = FurnaceContext(full=True)
    ctx.slots[2] = stack("minecraft:cooked_beef", 4)
    ctx.slots[5] = stack("minecraft:granite", 1)
    ctx.fail_move = True
    with pytest.raises(RuntimeError, match="unknown effect"):
        furnace_run(ctx)
    assert len(ctx.calls) == 1 and ctx.calls[0][1]["type"] == "THROW"
    assert ctx.closed


@pytest.mark.parametrize("result", [{"moved": True}, {"moved": False, "postcondition_verified": True}])
def test_unverified_furnace_load_fails_closed(furnace_run, result):
    ctx = FurnaceContext()
    ctx.load_result = result
    assert not furnace_run(ctx)
    assert len(ctx.calls) == 1 and ctx.closed


def test_unknown_furnace_load_is_not_retried(furnace_run):
    ctx = FurnaceContext()
    ctx.load_result = RuntimeError("unknown effect")
    with pytest.raises(RuntimeError, match="unknown effect"):
        furnace_run(ctx)
    assert len(ctx.calls) == 1 and ctx.closed


def test_menu_change_refuses_mutations(furnace_run, monkeypatch):
    ctx = FurnaceContext()
    original = ctx.dispatch
    reads = 0
    def dispatch(route, payload):
        nonlocal reads
        result = original(route, payload)
        if route == "get_screen":
            reads += 1
            if reads > 1:
                result["sync_id"] += 1
        return result
    monkeypatch.setattr(ctx, "dispatch", dispatch)
    with pytest.raises(RuntimeError, match="menu changed"):
        furnace_run(ctx)
    assert ctx.calls == [] and ctx.closed
