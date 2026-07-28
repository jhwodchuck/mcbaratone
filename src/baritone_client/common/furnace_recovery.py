"""Recovery for smelting batches left inside a loaded furnace."""

from __future__ import annotations

import time
from typing import Optional

from .inventory import count_item


def resume_active_furnace(
    client,
    furnace_pos,
    input_item: str,
    output_item: str,
    timeout: float = 600.0,
    minimum_output: Optional[int] = None,
) -> bool:
    """Collect an interrupted furnace batch.

    When ``minimum_output`` is provided, return as soon as that many output
    items are carried. This lets a starter-kit phase use its first ingots while
    the remaining batch continues safely in the furnace.
    """
    from . import harness_ops

    if minimum_output is not None and count_item(client, output_item) >= minimum_output:
        return True
    try:
        opened = harness_ops.available() and harness_ops.open_container(
            client, tuple(furnace_pos), timeout=4.0
        )
    except Exception as exc:
        print(f"  Could not inspect loaded furnace: {exc}")
        opened = False
    if not opened:
        return False

    def furnace_slots():
        screen = client.transport.dispatch("get_screen", {})
        data = screen.get("data", screen)
        by_slot = {
            int(slot.get("slot", -1)): slot for slot in data.get("slots", [])
        }
        return data, by_slot

    data, slots = furnace_slots()
    input_slot = slots.get(0, {})
    output_slot = slots.get(2, {})
    had_work = (
        input_slot.get("id") == input_item
        or output_slot.get("id") == output_item
    )
    if not had_work:
        client.transport.dispatch("close_screen", {})
        return False

    initial_input = int(input_slot.get("count", 0))
    deadline = time.monotonic() + min(
        timeout, max(20.0, initial_input * 10.5 + 30.0)
    )
    empty_polls = 0
    print(
        f"  Resuming loaded furnace at {tuple(furnace_pos)} "
        f"({initial_input} {input_item} pending)..."
    )

    while time.monotonic() < deadline:
        data, slots = furnace_slots()
        output_slot = slots.get(2, {})
        if (
            output_slot.get("id") == output_item
            and int(output_slot.get("count", 0)) > 0
        ):
            payload = {"slot": 2, "type": "QUICK_MOVE", "button": 0}
            sync_id = data.get("sync_id")
            if sync_id is not None:
                payload["sync_id"] = sync_id
            client.transport.dispatch("inventory_click", payload)
            time.sleep(0.2)
            if (
                minimum_output is not None
                and count_item(client, output_item) >= minimum_output
            ):
                client.transport.dispatch("close_screen", {})
                print(f"  Collected starter furnace output ({minimum_output}).")
                return True
            empty_polls = 0
            continue

        input_slot = slots.get(0, {})
        input_empty = (
            input_slot.get("id") in (None, "minecraft:air")
            or int(input_slot.get("count", 0)) <= 0
        )
        if input_empty:
            empty_polls += 1
            if empty_polls >= 2:
                client.transport.dispatch("close_screen", {})
                print("  Loaded furnace batch is fully collected.")
                return (
                    minimum_output is None
                    or count_item(client, output_item) >= minimum_output
                )
        else:
            empty_polls = 0
            fuel_slot = slots.get(1, {})
            block = client.transport.dispatch(
                "get_block",
                {
                    "x": int(furnace_pos[0]),
                    "y": int(furnace_pos[1]),
                    "z": int(furnace_pos[2]),
                },
            )
            lit = str(block.get("state", {}).get("lit", "false")).lower() == "true"
            fuel_empty = (
                fuel_slot.get("id") in (None, "minecraft:air")
                or int(fuel_slot.get("count", 0)) <= 0
            )
            if not lit and fuel_empty:
                client.transport.dispatch("close_screen", {})
                print("  Loaded furnace stalled without fuel.")
                return False
        time.sleep(0.5)

    client.transport.dispatch("close_screen", {})
    print("  Timed out while resuming loaded furnace.")
    return False
