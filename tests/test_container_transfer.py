from types import SimpleNamespace

import pytest

from baritone_client.common import container_transfer as module


class TransferTransport:
    def __init__(self, source_count: int) -> None:
        self.source_count = source_count
        self.player_count = 0
        self.cursor_count = 0
        self.clicks = []

    def _slots(self):
        slots = [
            {
                "slot": 0,
                "id": "minecraft:wheat" if self.source_count else "minecraft:air",
                "count": self.source_count,
                "max_count": 64,
            }
        ]
        slots.extend(
            {
                "slot": slot,
                "id": "minecraft:wheat" if slot == 27 and self.player_count else "minecraft:air",
                "count": self.player_count if slot == 27 else 0,
                "max_count": 64,
            }
            for slot in range(27, 63)
        )
        return slots

    def dispatch(self, route, payload):
        if route == "get_screen":
            return {"total_slots": 63, "sync_id": 4, "slots": self._slots()}
        if route == "inventory_click":
            self.clicks.append(dict(payload))
            slot = payload["slot"]
            if payload["type"] == "QUICK_MOVE" and slot == 0:
                self.player_count += self.source_count
                self.source_count = 0
            elif payload["type"] == "PICKUP" and slot == 0:
                self.source_count, self.cursor_count = (
                    self.cursor_count,
                    self.source_count,
                )
            elif payload["type"] == "PICKUP" and slot == 27 and payload["button"] == 1:
                self.player_count += 1
                self.cursor_count -= 1
            return {}
        return {}


def test_bounded_transfer_splits_large_shared_stack(monkeypatch):
    transport = TransferTransport(64)
    client = SimpleNamespace(transport=transport)
    monkeypatch.setattr(module, "count_item", lambda *_args: 0)
    monkeypatch.setattr(module.harness_ops, "open_container", lambda *_a, **_k: True)

    assert module.withdraw_bounded_food(
        client,
        (1, 2, 3),
        ("minecraft:wheat",),
        target_total=6,
    ) == 6
    assert transport.source_count == 58
    assert transport.player_count == 6
    assert sum(
        click["type"] == "PICKUP" and click["slot"] == 27
        for click in transport.clicks
    ) == 6


def test_bounded_transfer_shift_clicks_small_complete_stack(monkeypatch):
    transport = TransferTransport(4)
    client = SimpleNamespace(transport=transport)
    monkeypatch.setattr(module, "count_item", lambda *_args: 0)
    monkeypatch.setattr(module.harness_ops, "open_container", lambda *_a, **_k: True)

    assert module.withdraw_bounded_food(
        client,
        (1, 2, 3),
        ("minecraft:wheat",),
        target_total=8,
    ) == 4
    assert transport.source_count == 0
    assert transport.player_count == 4


def test_generic_bounded_transfer_uses_the_same_exact_split(monkeypatch):
    transport = TransferTransport(64)
    client = SimpleNamespace(transport=transport)
    monkeypatch.setattr(module, "count_item", lambda *_args: 0)
    monkeypatch.setattr(module.harness_ops, "open_container", lambda *_a, **_k: True)

    assert module.withdraw_bounded_items(
        client,
        (1, 2, 3),
        ("minecraft:wheat",),
        target_total=5,
    ) == 5
    assert transport.source_count == 59
    assert transport.player_count == 5


class DepositTransport:
    """One player stack (slot 27) depositing into one container slot (slot 0).

    Mirrors ``TransferTransport`` with source/target swapped: deposits move
    from the player side into the container, the opposite of a withdrawal.
    """

    def __init__(self, player_count: int, container_count: int = 0) -> None:
        self.player_count = player_count
        self.container_count = container_count
        self.cursor_count = 0
        self.clicks = []

    def _slots(self):
        slots = [
            {
                "slot": 0,
                "id": "minecraft:chest" if self.container_count else "minecraft:air",
                "count": self.container_count,
                "max_count": 64,
            }
        ]
        slots.extend(
            {
                "slot": slot,
                "id": "minecraft:chest" if slot == 27 and self.player_count else "minecraft:air",
                "count": self.player_count if slot == 27 else 0,
                "max_count": 64,
            }
            for slot in range(27, 63)
        )
        return slots

    def dispatch(self, route, payload):
        if route == "get_screen":
            return {"total_slots": 63, "sync_id": 4, "slots": self._slots()}
        if route == "inventory_click":
            self.clicks.append(dict(payload))
            slot = payload["slot"]
            if payload["type"] == "QUICK_MOVE" and slot == 27:
                self.container_count += self.player_count
                self.player_count = 0
            elif payload["type"] == "PICKUP" and slot == 27 and payload["button"] == 0:
                self.player_count, self.cursor_count = (
                    self.cursor_count,
                    self.player_count,
                )
            elif payload["type"] == "PICKUP" and slot == 0 and payload["button"] == 1:
                self.container_count += 1
                self.cursor_count -= 1
            return {}
        return {}


def test_verified_partial_move_splits_the_stack_into_an_empty_container_slot():
    transport = DepositTransport(player_count=17, container_count=0)
    client = SimpleNamespace(transport=transport)

    moved = module.verified_partial_move(
        client,
        slot=27,
        item_id="minecraft:chest",
        before_count=17,
        transfer_count=15,
        sync_id=4,
    )

    assert moved == 15
    assert transport.player_count == 2
    assert transport.container_count == 15


def test_deposit_respecting_reserve_protects_a_stack_entirely_below_floor():
    transport = DepositTransport(player_count=17, container_count=0)
    client = SimpleNamespace(transport=transport)

    moved = module.deposit_stack_respecting_reserve(
        client,
        slot=27,
        item_id="minecraft:chest",
        count=17,
        totals={"minecraft:chest": 17},
        retains={"minecraft:chest": 20},
        sync_id=4,
    )

    assert moved is None
    assert transport.clicks == []


def test_deposit_respecting_reserve_shift_clicks_a_stack_that_fully_clears_floor():
    transport = DepositTransport(player_count=17, container_count=0)
    client = SimpleNamespace(transport=transport)

    moved = module.deposit_stack_respecting_reserve(
        client,
        slot=27,
        item_id="minecraft:chest",
        count=17,
        totals={"minecraft:chest": 17},
        retains={"minecraft:chest": 0},
        sync_id=4,
    )

    assert moved == 17
    assert transport.player_count == 0
    assert transport.container_count == 17
    assert any(click["type"] == "QUICK_MOVE" for click in transport.clicks)


def test_deposit_respecting_reserve_splits_a_single_slot_to_honor_the_floor():
    """The exact A1 live bug: 17 chests in one stack, retain floor 2, room to spare.

    ``verified_quick_move`` can only shift-click a whole slot, so depositing
    it would drop the total from 17 to 0 -- below the floor -- and the old
    caller skipped the item outright rather than banking any of the 15-item
    surplus. Confirmed live: correct item selection, a correct 2-chest floor,
    and 36 free chest slots still produced "deposited 0 excess stacks".
    """
    transport = DepositTransport(player_count=17, container_count=0)
    client = SimpleNamespace(transport=transport)

    moved = module.deposit_stack_respecting_reserve(
        client,
        slot=27,
        item_id="minecraft:chest",
        count=17,
        totals={"minecraft:chest": 17},
        retains={"minecraft:chest": 2},
        sync_id=4,
    )

    assert moved == 15
    assert transport.player_count == 2
    assert transport.container_count == 15
    assert not any(click["type"] == "QUICK_MOVE" for click in transport.clicks)


class CapacityTransport:
    """A1 shape: every chest slot occupied, dirt full, furnace merge possible."""

    def __init__(self):
        self.slots = [dict(slot=i, id="minecraft:cobblestone", count=64, max_count=64)
                      for i in range(27)]
        self.slots += [dict(slot=i, id="minecraft:air", count=0) for i in range(27, 63)]
        self.slots[12].update(id="minecraft:furnace", count=1)
        self.slots[13].update(id="minecraft:dirt", count=64)
        self.slots[27].update(id="minecraft:dirt", count=46, max_count=64)
        self.slots[28].update(id="minecraft:furnace", count=4, max_count=64)
        self.clicks = []
        self.sync_id = 39
        self.error = None

    def dispatch(self, route, payload):
        if route == "get_inventory":
            return dict(snapshot_valid=True, inventory=[
                dict(item, slot=item["slot"] - 27) for item in self.slots[27:]
            ])
        if route == "get_screen":
            return dict(type="ChestMenu", total_slots=63, sync_id=self.sync_id,
                        slots=[dict(item) for item in self.slots])
        if route == "get_block":
            return {"id": "minecraft:chest"}
        if route == "get_state":
            return dict(block_position=dict(x=0, y=64, z=0), health=20, food_level=20)
        if route == "inventory_click":
            self.clicks.append(dict(payload))
            if self.error:
                raise self.error
            assert payload == dict(slot=28, type="QUICK_MOVE", button=0, sync_id=39)
            self.slots[12]["count"] += 4
            self.slots[28].update(id="minecraft:air", count=0)
        return {}


def test_known_full_destination_never_dispatches_a_noop_click():
    transport = CapacityTransport()
    assert module.verified_quick_move(SimpleNamespace(transport=transport),
        slot=27, item_id="minecraft:dirt", before_count=46, sync_id=39) == 0
    assert transport.clicks == []


def test_full_chest_can_still_accept_a_compatible_stack():
    transport = CapacityTransport()
    assert module.verified_quick_move(SimpleNamespace(transport=transport),
        slot=28, item_id="minecraft:furnace", before_count=4, sync_id=39) == 4
    assert transport.slots[12]["count"] == 5
    assert transport.slots[28]["count"] == 0
    assert len(transport.clicks) == 1


def test_deposit_skips_full_item_and_banks_a_later_compatible_item(monkeypatch):
    from baritone_client.common import inventory

    transport = CapacityTransport()
    monkeypatch.setattr(module.harness_ops, "open_container", lambda *_a, **_k: True)
    assert inventory.deposit_excess_to_chest(SimpleNamespace(transport=transport), (0,64,0),
        deposit_items={"minecraft:dirt", "minecraft:furnace"}) == 1
    assert transport.slots[27]["count"] == 46
    assert transport.slots[28]["count"] == 0
    assert transport.slots[12]["count"] == 5
    assert len(transport.clicks) == 1


@pytest.mark.parametrize("change", ["sync", "count", "components", "missing_capacity"])
def test_stale_source_or_incompatible_destination_prevents_dispatch(change):
    transport = CapacityTransport()
    if change == "sync":
        transport.sync_id = 40
    elif change == "count":
        transport.slots[28]["count"] = 3
    elif change == "components":
        transport.slots[12]["components"] = {"minecraft:custom_name": "other furnace"}
    else:
        transport.slots[12].pop("max_count")
    assert module.verified_quick_move(SimpleNamespace(transport=transport),
        slot=28, item_id="minecraft:furnace", before_count=4, sync_id=39) == 0
    assert transport.clicks == []


def test_cleanup_tour_banks_compatible_items_in_a_chest_with_no_empty_slots(monkeypatch):
    from baritone_client.common import inventory, storage_safety

    transport = CapacityTransport()
    client = SimpleNamespace(transport=transport)
    monkeypatch.setattr(module.harness_ops, "available", lambda: True)
    monkeypatch.setattr(module.harness_ops, "open_container", lambda *_a, **_k: True)
    monkeypatch.setattr(module.harness_ops, "chest_is_full", lambda *_a: True)
    monkeypatch.setattr(storage_safety, "nearby_storage_positions", lambda *_a: [(0, 64, 0)])
    # 34 free player slots before, 35 after. Every chest slot is occupied.
    assert inventory.free_inventory_slots(client) == 34
    assert storage_safety.store_surplus_in_chest(
        client, 35, deposit_items={"minecraft:furnace"}, retain_counts={"minecraft:furnace": 0},
    )
    assert inventory.free_inventory_slots(client) == 35
    assert transport.slots[12]["count"] == 5
    assert transport.slots[27]["count"] == 46
    assert len(transport.clicks) == 1


def test_unknown_click_outcome_is_not_retried_or_hidden():
    transport = CapacityTransport()
    transport.error = RuntimeError("Observed effect deadline exceeded; reconcile before retry")
    with pytest.raises(RuntimeError, match="reconcile"):
        module.verified_quick_move(SimpleNamespace(transport=transport),
            slot=28, item_id="minecraft:furnace", before_count=4, sync_id=39)
    assert len(transport.clicks) == 1
