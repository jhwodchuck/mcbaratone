"""Inventory helpers for Minecraft integration tests."""

import time
from typing import Optional

from .common import safe_dispatch


def get_inventory(ctx, timeout: float = 2.0):
    return safe_dispatch(ctx, "get_inventory", {}, timeout=timeout)


def count_item(ctx, item_id: str) -> int:
    return ctx.count_item(item_id)


def has_item(ctx, item_id: str, count: int = 1) -> bool:
    return ctx.has_item(item_id, count=count)


def safe_inventory_click(ctx, slot: int, click_type: str, button: int = 0, retry: int = 1,
                         sync_id: Optional[int] = None) -> bool:
    payload = {"slot": slot, "type": click_type, "button": button}
    if sync_id is not None:
        payload["sync_id"] = sync_id
    for _ in range(retry + 1):
        try:
            safe_dispatch(ctx, "inventory_click", payload)
            return True
        except Exception:
            time.sleep(0.1)
    return False


def ensure_item_in_hotbar(ctx, item_id: str) -> Optional[int]:
    inv = get_inventory(ctx, timeout=2.0)
    hotbar_slot = None
    item_slot = None
    for slot in inv.get("inventory", []):
        if slot.get("id") == item_id and slot.get("count", 0) > 0:
            slot_idx = slot.get("slot")
            if slot_idx is None:
                continue
            if 0 <= slot_idx <= 8:
                hotbar_slot = slot_idx
                break
            if item_slot is None:
                item_slot = slot_idx
    if hotbar_slot is not None:
        return hotbar_slot
    if item_slot is None:
        return None
    empty_hotbar = None
    for slot in inv.get("inventory", []):
        slot_idx = slot.get("slot")
        if slot_idx is None or not (0 <= slot_idx <= 8):
            continue
        if slot.get("id") in ("minecraft:air", None) or slot.get("count", 0) == 0:
            empty_hotbar = slot_idx
            break
    target_slot = empty_hotbar if empty_hotbar is not None else 0
    if item_slot != target_slot:
        safe_inventory_click(ctx, item_slot, "PICKUP", button=0, retry=1)
        time.sleep(0.1)
        safe_inventory_click(ctx, target_slot, "PICKUP", button=0, retry=1)
        time.sleep(0.1)
        if empty_hotbar is None:
            safe_inventory_click(ctx, item_slot, "PICKUP", button=0, retry=1)
            time.sleep(0.1)
    return target_slot


def select_hotbar_item(ctx, item_id: str) -> bool:
    slot = ensure_item_in_hotbar(ctx, item_id)
    if slot is None:
        return False
    safe_dispatch(ctx, "select_slot", {"slot": slot})
    return True


def quick_move_slot(ctx, slot: int, sync_id: Optional[int] = None) -> bool:
    return safe_inventory_click(ctx, slot, "QUICK_MOVE", button=0, retry=1, sync_id=sync_id)


def furnace_slot_map(inv_slot: int) -> int:
    if 0 <= inv_slot <= 8:
        return 30 + inv_slot
    if 9 <= inv_slot <= 35:
        return 3 + (inv_slot - 9)
    return inv_slot


def player_slot_map(inv_slot: int) -> int:
    if 0 <= inv_slot <= 8:
        return 36 + inv_slot
    if 9 <= inv_slot <= 35:
        return inv_slot
    return inv_slot
