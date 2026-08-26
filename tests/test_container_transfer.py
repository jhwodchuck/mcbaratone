from types import SimpleNamespace

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
