"""Verified inventory disposal helpers."""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional


def drop_items(
    client: Any,
    item_ids: List[str],
    max_stacks: Optional[int] = None,
    retain_counts: Optional[Dict[str, int]] = None,
) -> int:
    """Drop verified whole stacks while preserving requested reserves."""
    from . import inventory as api

    dropped = 0
    reserves = {
        item_id: max(0, int(count))
        for item_id, count in (retain_counts or {}).items()
    }
    try:
        client.transport.dispatch("close_screen", {})
        time.sleep(0.1)
        raw = client.transport.dispatch("get_inventory", {})
        items = raw.get("data", raw).get("inventory", [])
        totals: Dict[str, int] = {}
        for carried in items:
            item_id = carried.get("id")
            if item_id:
                totals[item_id] = totals.get(item_id, 0) + int(
                    carried.get("count", 0)
                )

        # Every rejection below used to be a silent `continue`, so a caller
        # that freed nothing could not tell "no candidates" from "the throw
        # itself is not working". Live A1 2026-08-31 logged 47 cleanup
        # failures in 20 minutes with four unprotected, unfloored stacks
        # sitting in slots 6/9/12/20 and no indication of which step refused.
        skips = {"reserved": 0, "not_emptied": 0, "no_slot_gain": 0}
        candidates = 0
        for item in items:
            if max_stacks is not None and dropped >= max_stacks:
                break
            item_id = item.get("id")
            slot = int(item.get("slot", -1))
            count = int(item.get("count", 0))
            if item_id not in item_ids or not 0 <= slot < 36:
                continue
            candidates += 1
            if totals.get(item_id, 0) - count < reserves.get(item_id, 0):
                skips["reserved"] += 1
                continue
            free_before = api.free_inventory_slots(client)
            client.transport.dispatch(
                "inventory_click",
                {
                    "slot": api._player_handler_slot(slot),
                    "type": "THROW",
                    "button": 1,
                },
            )
            if not _wait_for_empty_slot(client, slot):
                skips["not_emptied"] += 1
                continue
            time.sleep(2.25)
            if api.free_inventory_slots(client) > free_before:
                dropped += 1
                totals[item_id] = max(0, totals.get(item_id, 0) - count)
            else:
                skips["no_slot_gain"] += 1
        if not dropped and candidates:
            detail = ", ".join(f"{name}={n}" for name, n in skips.items() if n)
            print(
                f"  Drop items: none of {candidates} candidate stack(s) left "
                f"the inventory ({detail or 'no reason recorded'})"
            )
    except Exception as exc:
        print(f"Drop items error: {exc}")
    return dropped


def _wait_for_empty_slot(client: Any, inventory_slot: int) -> bool:
    deadline = time.time() + 2.0
    while time.time() < deadline:
        raw = client.transport.dispatch("get_inventory", {})
        items = raw.get("data", raw).get("inventory", [])
        entry = next(
            (
                value
                for value in items
                if int(value.get("slot", -1)) == inventory_slot
            ),
            None,
        )
        if (
            entry is None
            or entry.get("id") in (None, "", "minecraft:air")
            or int(entry.get("count", 0)) <= 0
        ):
            return True
        time.sleep(0.1)
    return False
