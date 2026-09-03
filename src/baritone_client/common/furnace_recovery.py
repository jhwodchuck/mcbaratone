"""Recovery for smelting batches left inside a loaded furnace."""

from __future__ import annotations

import time
from typing import Optional

from .inventory import count_item


def collect_finished_furnace_output(
    client,
    output_item: str,
    minimum_output: int,
    furnace_pos=None,
) -> bool:
    """Find and collect durable furnace output before starting new work."""
    if count_item(client, output_item) >= minimum_output:
        return True

    from . import harness_ops
    candidates = []
    if furnace_pos is not None:
        try:
            provided = tuple(int(value) for value in furnace_pos)
        except (TypeError, ValueError):
            provided = None
        if provided is not None:
            block = client.transport.dispatch(
                "get_block",
                {"x": provided[0], "y": provided[1], "z": provided[2]},
            ).get("id", "")
            if "furnace" in block:
                candidates.append(provided)
    if not candidates:
        try:
            response = client.transport.dispatch(
                "find_blocks",
                {
                    "blocks": ["minecraft:furnace", "minecraft:blast_furnace"],
                    "radius": 32,
                    "limit": 4096,
                },
            )
            for found in response.get("found", []):
                candidate = (
                    int(found["x"]),
                    int(found["y"]),
                    int(found["z"]),
                )
                if candidate not in candidates:
                    candidates.append(candidate)
        except (KeyError, TypeError, ValueError):
            return False
    if not candidates or not harness_ops.available():
        return False

    state = client.transport.dispatch("get_state", {})
    position = state.get("block_position", state.get("position", {}))
    def distance_to(candidate):
        return sum(
            (float(position[axis]) - float(coordinate)) ** 2
            for axis, coordinate in zip(("x", "y", "z"), candidate)
        ) ** 0.5

    try:
        candidates.sort(key=distance_to)
    except (KeyError, TypeError, ValueError):
        return False

    input_item = (
        "minecraft:raw_gold"
        if output_item == "minecraft:gold_ingot"
        else "minecraft:raw_iron"
    )
    for candidate in candidates:
        if distance_to(candidate) > 4.5 and not harness_ops.move_near(
            client,
            int(candidate[0]),
            int(candidate[1]),
            int(candidate[2]),
            timeout=45.0,
        ):
            continue
        if resume_active_furnace(
            client,
            candidate,
            input_item,
            output_item,
            timeout=60.0,
            minimum_output=minimum_output,
        ):
            return True
    return False


def _load_carried_fuel(client, data) -> bool:
    """Shift a carried fuel stack into a stalled furnace.

    A furnace holding raw ore and no fuel used to end the attempt outright,
    even while the bot carried planks and logs that burn perfectly well. Live
    on A1 2026-09-03: "Resuming loaded furnace at (-7, 160, 7) (1
    minecraft:raw_iron pending)... Loaded furnace stalled without fuel", on a
    ~24 second cycle for hours, with 9 oak planks and 2 oak logs in the bag and
    NETHER_AND_BLAZE waiting on six iron ingots.

    QUICK_MOVE from the player rows routes fuel to the fuel slot in vanilla,
    which is the same mechanism the input loader above already relies on.
    """
    from .resources import FURNACE_FUEL_SMELTS

    carried = next(
        (
            slot
            for slot in data.get("slots", [])
            if int(slot.get("slot", -1)) >= 3
            and slot.get("id") in FURNACE_FUEL_SMELTS
            and int(slot.get("count", 0)) > 0
        ),
        None,
    )
    if carried is None:
        return False
    payload = {"slot": int(carried["slot"]), "type": "QUICK_MOVE", "button": 0}
    sync_id = data.get("sync_id")
    if sync_id is not None:
        payload["sync_id"] = sync_id
    try:
        client.transport.dispatch("inventory_click", payload)
    except Exception:
        return False
    return True


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
    fuel_slot = slots.get(1, {})
    output_slot = slots.get(2, {})
    input_empty = (
        input_slot.get("id") in (None, "minecraft:air")
        or int(input_slot.get("count", 0)) <= 0
    )
    output_empty = (
        output_slot.get("id") in (None, "minecraft:air")
        or int(output_slot.get("count", 0)) <= 0
    )
    fuel_loaded = (
        fuel_slot.get("id") not in (None, "minecraft:air")
        and int(fuel_slot.get("count", 0)) > 0
    )
    if input_empty and output_empty and fuel_loaded:
        carried_input = next(
            (
                slot
                for slot in data.get("slots", [])
                if int(slot.get("slot", -1)) >= 3
                and slot.get("id") == input_item
                and int(slot.get("count", 0)) > 0
            ),
            None,
        )
        if carried_input is not None:
            payload = {
                "slot": int(carried_input["slot"]),
                "type": "QUICK_MOVE",
                "button": 0,
            }
            sync_id = data.get("sync_id")
            if sync_id is not None:
                payload["sync_id"] = sync_id
            client.transport.dispatch("inventory_click", payload)
            time.sleep(0.2)
            data, slots = furnace_slots()
            input_slot = slots.get(0, {})
            output_slot = slots.get(2, {})
            print(f"  Loaded carried {input_item} into fueled furnace.")
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
        # A preceding harness/native attempt can complete its QUICK_MOVE after
        # our initial inventory read but before this recovery loop observes an
        # output slot.  In that race the requested ingots are already carried
        # while the furnace still contains a large input batch.  Waiting for
        # that whole batch wedges every caller even though its bounded target
        # is satisfied, so re-check carried output on every poll.
        if (
            minimum_output is not None
            and count_item(client, output_item) >= minimum_output
        ):
            client.transport.dispatch("close_screen", {})
            print(f"  Carried furnace output target reached ({minimum_output}).")
            return True

        data, slots = furnace_slots()
        output_slot = slots.get(2, {})
        output_id = output_slot.get("id")
        output_count = int(output_slot.get("count", 0))
        if output_id not in (None, "minecraft:air") and output_count > 0:
            payload = {"slot": 2, "type": "QUICK_MOVE", "button": 0}
            sync_id = data.get("sync_id")
            if sync_id is not None:
                payload["sync_id"] = sync_id
            client.transport.dispatch("inventory_click", payload)
            time.sleep(0.2)
            if output_id != output_item:
                # A furnace can retain output from an older recipe. Vanilla
                # will not smelt the loaded input while that incompatible
                # stack occupies slot 2, so collect it before waiting for the
                # requested batch. The item remains in player inventory.
                print(
                    f"  Cleared obstructing furnace output "
                    f"({output_count} {output_id})."
                )
                empty_polls = 0
                continue
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
                if _load_carried_fuel(client, data):
                    print("  Refuelled the stalled furnace from carried stock.")
                    time.sleep(0.3)
                    continue
                client.transport.dispatch("close_screen", {})
                print("  Loaded furnace stalled without fuel.")
                return False
        time.sleep(0.5)

    client.transport.dispatch("close_screen", {})
    completed = (
        minimum_output is not None
        and count_item(client, output_item) >= minimum_output
    )
    print(
        "  Furnace output target reached at timeout boundary."
        if completed
        else "  Timed out while resuming loaded furnace."
    )
    return completed
