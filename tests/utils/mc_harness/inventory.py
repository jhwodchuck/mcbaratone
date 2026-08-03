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
            response = safe_dispatch(ctx, "inventory_click", payload)
            data = response.get("data", response) if isinstance(response, dict) else {}
            if isinstance(response, dict) and response.get("error"):
                time.sleep(0.1)
                continue
            if isinstance(data, dict) and data.get("clicked") is False:
                time.sleep(0.1)
                continue
            return True
        except Exception:
            time.sleep(0.1)
    return False


def player_inventory_menu_slot(inventory_slot: int) -> int:
    """Translate a logical player-inventory slot to ``InventoryMenu``.

    ``get_inventory`` exposes the player's logical slots (hotbar 0-8, main
    inventory 9-35).  ``inventory_click`` accepts the active container-menu
    slot instead.  In the vanilla player menu those numberings only coincide
    for 9-35; hotbar 0-8 is exposed as menu slots 36-44.

    Treating hotbar slot 0 as menu slot 0 clicks the crafting result.  Live
    this made a swap report success without moving cobblestone, after which
    house repair selected slot 0 and repeatedly tried to place an iron sword.
    """
    slot = int(inventory_slot)
    if 0 <= slot <= 8:
        return 36 + slot
    if 9 <= slot <= 35:
        return slot
    raise ValueError(f"Not a main player-inventory slot: {slot}")


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
        # Inventory-click slot ids depend on the open menu.  Close any chest,
        # furnace, or crafting table first so the mapping below is stable and
        # explicitly target the vanilla player InventoryMenu slot ids.
        safe_dispatch(ctx, "close_screen", {})
        time.sleep(0.1)
        source_menu_slot = player_inventory_menu_slot(item_slot)
        # SWAP is atomic in the vanilla menu: button is the logical hotbar
        # index and the clicked slot is the source menu slot. Unlike a
        # three-click PICKUP sequence, it cannot strand an item on the cursor
        # if a later dispatch fails.
        if not safe_inventory_click(
            ctx, source_menu_slot, "SWAP", button=target_slot, retry=1
        ):
            return None
        time.sleep(0.1)
        refreshed = get_inventory(ctx, timeout=2.0)
        target = next(
            (
                entry
                for entry in refreshed.get("inventory", [])
                if int(entry.get("slot", -1)) == target_slot
            ),
            {},
        )
        if target.get("id") != item_id or int(target.get("count", 0)) <= 0:
            ctx.log_event(
                f"Hotbar swap was not observed for {item_id} in slot {target_slot}"
            )
            return None
    return target_slot


def select_hotbar_item(ctx, item_id: str) -> bool:
    slot = ensure_item_in_hotbar(ctx, item_id)
    if slot is None:
        return False
    safe_dispatch(ctx, "select_slot", {"slot": slot})
    refreshed = get_inventory(ctx, timeout=2.0)
    selected = refreshed.get("selected_slot")
    held = next(
        (
            entry
            for entry in refreshed.get("inventory", [])
            if int(entry.get("slot", -1)) == slot
        ),
        {},
    )
    if selected != slot or held.get("id") != item_id or int(held.get("count", 0)) <= 0:
        ctx.log_event(
            f"Hotbar selection was not observed for {item_id} in slot {slot}"
        )
        return False
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
