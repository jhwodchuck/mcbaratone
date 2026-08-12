"""Bounded container transfers that avoid one bot hoarding shared supplies."""

from __future__ import annotations

import time
from typing import Any, Iterable, Mapping, Sequence

from . import harness_ops
Position = tuple[int, int, int]


def count_item(client: Any, item_id: str) -> int:
    """Resolve the inventory helper lazily to avoid a module import cycle."""
    from .inventory import count_item as inventory_count_item

    return inventory_count_item(client, item_id)


def _screen_data(client: Any) -> tuple[list[Mapping[str, Any]], int, Any] | None:
    screen = client.transport.dispatch("get_screen", {})
    data = screen.get("data", screen)
    slots = list(data.get("slots", ()) or ())
    total = int(data.get("total_slots") or len(slots))
    container_slots = total - 36
    if container_slots not in (27, 54):
        return None
    return slots, container_slots, data.get("sync_id", screen.get("sync_id"))


def _player_target_slot(
    slots: Sequence[Mapping[str, Any]],
    container_slots: int,
    item_id: str,
    needed: int,
) -> int | None:
    """Find a player slot that can receive ``needed`` right-click deposits."""
    empty: int | None = None
    for item in slots:
        slot = int(item.get("slot", -1))
        if slot < container_slots:
            continue
        current_id = str(item.get("id", "minecraft:air"))
        count = int(item.get("count", 0) or 0)
        if current_id == item_id:
            capacity = int(item.get("max_count", 64) or 64) - count
            if capacity >= needed:
                return slot
        if empty is None and (not current_id or current_id == "minecraft:air" or count <= 0):
            empty = slot
    return empty


def _click(client: Any, slot: int, click_type: str, button: int, sync_id: Any) -> None:
    payload = {"slot": int(slot), "type": click_type, "button": int(button)}
    if sync_id is not None:
        payload["sync_id"] = sync_id
    client.transport.dispatch("inventory_click", payload)


def verified_quick_move(
    client: Any,
    *,
    slot: int,
    item_id: str,
    before_count: int,
    sync_id: Any,
) -> int:
    """Quick-move one stack and return only its observed count delta."""
    _click(client, slot, "QUICK_MOVE", 0, sync_id)
    time.sleep(0.05)
    opened = _screen_data(client)
    if opened is None:
        return 0
    updated_entry = next(
        (item for item in opened[0] if int(item.get("slot", -1)) == slot),
        None,
    )
    updated_id = updated_entry.get("id") if updated_entry else None
    updated_count = int(updated_entry.get("count", 0) or 0) if updated_entry else 0
    return before_count if updated_id != item_id else before_count - updated_count


def withdraw_bounded_items(
    client: Any,
    chest: Position,
    accepted_items: Iterable[str],
    *,
    target_total: int,
    open_attempts: int = 3,
    allow_recovery_access: bool = True,
) -> int:
    """Withdraw only enough accepted items to reach ``target_total``.

    Shift-click is retained for stacks no larger than the shortfall. Larger
    stacks are split through normal left/right inventory clicks and the
    remainder is returned to the source slot for the next bot.
    """
    accepted = tuple(dict.fromkeys(str(item_id) for item_id in accepted_items))
    target_total = max(1, int(target_total))
    before = sum(count_item(client, item_id) for item_id in accepted)
    if before >= target_total:
        return 0
    try:
        client.transport.dispatch("close_screen", {})
        if not harness_ops.open_container(
            client,
            chest,
            timeout=4.0,
            attempts=open_attempts,
            allow_recovery_access=allow_recovery_access,
        ):
            return -1
        carried = before
        moved = 0
        while carried < target_total:
            opened = _screen_data(client)
            if opened is None:
                return -1
            slots, container_slots, sync_id = opened
            item = next(
                (
                    candidate
                    for candidate in slots
                    if 0 <= int(candidate.get("slot", -1)) < container_slots
                    and str(candidate.get("id", "")) in accepted
                    and int(candidate.get("count", 0) or 0) > 0
                ),
                None,
            )
            if item is None:
                break
            source_slot = int(item.get("slot", -1))
            item_id = str(item.get("id", ""))
            stack_count = int(item.get("count", 0) or 0)
            shortfall = target_total - carried
            transfer = min(shortfall, stack_count)
            if transfer == stack_count:
                _click(client, source_slot, "QUICK_MOVE", 0, sync_id)
            else:
                target_slot = _player_target_slot(
                    slots, container_slots, item_id, transfer
                )
                if target_slot is None:
                    break
                _click(client, source_slot, "PICKUP", 0, sync_id)
                for _ in range(transfer):
                    _click(client, target_slot, "PICKUP", 1, sync_id)
                _click(client, source_slot, "PICKUP", 0, sync_id)
            time.sleep(0.05)
            updated = _screen_data(client)
            if updated is None:
                break
            updated_item = next(
                (
                    candidate
                    for candidate in updated[0]
                    if int(candidate.get("slot", -1)) == source_slot
                ),
                None,
            )
            updated_id = str(updated_item.get("id", "")) if updated_item else ""
            updated_count = int(updated_item.get("count", 0) or 0) if updated_item else 0
            actual = stack_count if updated_id != item_id else stack_count - updated_count
            if actual <= 0:
                break
            carried += actual
            moved += actual
        return moved
    finally:
        try:
            client.transport.dispatch("close_screen", {})
        except Exception:
            pass


def withdraw_bounded_food(
    client: Any,
    chest: Position,
    accepted_items: Iterable[str],
    *,
    target_total: int,
    open_attempts: int = 3,
    allow_recovery_access: bool = True,
) -> int:
    """Backward-compatible food-specific wrapper."""
    return withdraw_bounded_items(
        client,
        chest,
        accepted_items,
        target_total=target_total,
        open_attempts=open_attempts,
        allow_recovery_access=allow_recovery_access,
    )


__all__ = [
    "verified_quick_move",
    "withdraw_bounded_food",
    "withdraw_bounded_items",
]
