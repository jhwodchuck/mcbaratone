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
