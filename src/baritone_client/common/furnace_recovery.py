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


#: Reconcile the furnace between bounded refuel attempts.
MAX_REFUEL_ATTEMPTS = 3


def _load_carried_fuel(client, data) -> bool:
    """Load carried fuel only when the bridge observes its transfer.

    Pass the observed menu ID so a changed container fails before clicks.
    The bridge targets the fuel slot explicitly; a log must not be routed to
    the input slot by a generic shift-click. A verified transfer starts work,
    while collected output remains the smelting completion predicate.
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
    # Report what the bridge said. Two rounds of refuel work assumed a
    # dispatched move had landed and re-entered the loop on that assumption;
    # smelt_items returns an explicit error ("Not in a furnace screen") that was
    # being discarded, so the real reason never reached the log.
    try:
        response = client.transport.dispatch(
            "smelt_items", {"fuel_slot": int(carried["slot"]), **({"sync_id": data["sync_id"]} if "sync_id" in data else {})}
        )
    except Exception as exc:
        print(f"  Refuel dispatch failed: {exc}")
        return False
    result = response.get("data", response) if isinstance(response, dict) else {}
    verified = result.get("moved") is True and result.get("postcondition_verified") is True
    if not verified:
        print("  Refuel refused by the bridge or unverified; reconcile the furnace before another transfer")
    return verified



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

    starting_output = count_item(client, output_item)
    menu_id = [None]

    def furnace_slots():
        return _verified_furnace_slots(client, menu_id)

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
    refuels = 0
    last_output = -1  # any real output resets the refuel bound
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
                    count_item(client, output_item) > starting_output
                    if minimum_output is None
                    else count_item(client, output_item) >= minimum_output
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
                produced = count_item(client, output_item)
                if produced > last_output:  # a burnt load is progress, not failure
                    refuels, last_output = 0, produced
                if refuels < MAX_REFUEL_ATTEMPTS and _load_carried_fuel(client, data):
                    refuels += 1
                    print(f"  Refuelling the stalled furnace ({refuels}).")
                    time.sleep(0.5)
                    continue
                client.transport.dispatch("close_screen", {})
                print(f"  Furnace unfuelled after {refuels} fruitless refuels.")
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


def _verified_furnace_slots(client, menu_id):
    screen = client.transport.dispatch("get_screen", {})
    data = screen.get("data", screen)
    slots = {int(slot.get("slot", -1)): slot for slot in data.get("slots", [])}
    if (not {0, 1, 2}.issubset(slots) or data.get("sync_id") is None
            or data.get("type") not in ("FurnaceMenu", "BlastFurnaceMenu", "SmokerMenu")):
        raise RuntimeError("Incomplete furnace screen; not completion evidence")
    if menu_id[0] is None:
        menu_id[0] = data["sync_id"]
    if data["sync_id"] != menu_id[0]:
        raise RuntimeError("Furnace menu changed during recovery")
    return data, slots
