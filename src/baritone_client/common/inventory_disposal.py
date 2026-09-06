"""Verified inventory disposal helpers."""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional


# Observe beyond vanilla's pickup delay; early empty slots are not durable.
PICKUP_SAFE_SETTLE_SECONDS = 2.5
DISPOSAL_RETRY_COOLDOWN_SECONDS = 300.0

def drop_items(
    client: Any,
    item_ids: List[str],
    max_stacks: Optional[int] = None,
    retain_counts: Optional[Dict[str, int]] = None,
) -> int:
    """Drop verified whole stacks while preserving requested reserves."""
    from . import inventory as api

    if time.monotonic() < getattr(client, "_disposal_retry_after", 0.0):
        return 0
    dropped = 0
    reserves = {
        item_id: max(0, int(count))
        for item_id, count in (retain_counts or {}).items()
    }
    try:
        client.transport.dispatch("close_screen", {})
        time.sleep(0.1)
        raw = client.transport.dispatch("get_inventory", {})
        from ..inventory_evidence import valid_inventory
        if not valid_inventory(raw.get("data", raw)):
            raise RuntimeError("Inventory unavailable; disposal refused")
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
                client._disposal_retry_after = time.monotonic() + DISPOSAL_RETRY_COOLDOWN_SECONDS
                break
            # Allow a safe retreat if one is observable; never blindly path
            # toward an edge just to avoid pickup. Durable verification still
            # decides whether disposal worked when retreat is unavailable.
            _retreat_from_drop(client)
            time.sleep(PICKUP_SAFE_SETTLE_SECONDS)
            if api.free_inventory_slots(client) > free_before:
                dropped += 1
                totals[item_id] = max(0, totals.get(item_id, 0) - count)
            else:
                skips["no_slot_gain"] += 1
                client._disposal_retry_after = time.monotonic() + DISPOSAL_RETRY_COOLDOWN_SECONDS
                from ..observability import emit_event
                emit_event("disposal_repickup", item_id=item_id, slot=slot,
                           postcondition_verified=False, retry_after_seconds=DISPOSAL_RETRY_COOLDOWN_SECONDS)
                break
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
        from ..inventory_evidence import valid_inventory
        if not valid_inventory(raw.get("data", raw)):
            return False
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


def _retreat_from_drop(client):
    """Step away far enough that a just-thrown item is not re-picked up.

    Vanilla gives a player-dropped item a ~2-second self-pickup delay
    specifically so the dropper can step away; PICKUP_SAFE_SETTLE_SECONDS
    waits just past that window. But the previous version required a full
    3-block clear line in one direction or did nothing at all, so a bot in a
    mined tunnel -- clear for 1 block but blocked or open-floored beyond that
    in every direction -- never moved, stood over its own drop for the whole
    delay, and auto-picked it back up the instant the delay lapsed.

    Live A1 2026-09-06 at (-300, 68, 191): every cardinal direction had a
    valid first step but failed at step 2 or 3, so disposal reported
    "no_slot_gain" and cooled down for 5 minutes, repeatedly, with 12 slots
    of duplicate gear sitting there unshed. Take the longest safe
    straight-line retreat available, even if that is only 1 block --
    reaching pickup range is what matters, not reaching 3 blocks.
    """
    from .automation_utils import _block_at, _is_solid
    from .navigation import goto
    try:
        state = client.transport.dispatch("get_state", {})
        pos = state.get("block_position", {})
        x, y, z = (int(pos[k]) for k in ("x", "y", "z"))
        air = {"minecraft:air", "minecraft:cave_air"}
        best_direction, best_n = None, 0
        for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            reached = 0
            for n in (1, 2, 3):
                if (
                    _is_solid(client, x + dx * n, y - 1, z + dz * n)
                    and _block_at(client, x + dx * n, y, z + dz * n) in air
                    and _block_at(client, x + dx * n, y + 1, z + dz * n) in air
                ):
                    reached = n
                else:
                    break
            if reached > best_n:
                best_direction, best_n = (dx, dz), reached
        if best_direction is None:
            return False
        dx, dz = best_direction
        return goto(
            client, x + dx * best_n, y, z + dz * best_n, timeout=6, tolerance=0.8
        )
    except Exception:
        return False
