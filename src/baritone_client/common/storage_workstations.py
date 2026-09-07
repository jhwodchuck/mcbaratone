"""Bank a portable furnace only beside a freshly verified installed one."""

from . import harness_ops
from .storage_safety import storage_distance, storage_travel_safe


def bank_installed_furnace_spare(client, chest, required: int) -> bool:
    """Recover one working slot without discarding a reusable workstation.

    Keeping one furnace is useful on an expedition, but locks its player slot
    even at a usable installed furnace. Restrict this exception to adjacent
    storage and an actually opened FurnaceMenu. Never relax other reserves.
    """
    from .inventory import count_item, deposit_excess_to_chest, free_inventory_slots

    if count_item(client, "minecraft:furnace") < 1:
        return False
    try:
        live = client.transport.dispatch("get_state", {})
        if not storage_travel_safe(live) or storage_distance(live, chest) > 4.5:
            return False
        found = client.transport.dispatch("find_blocks", {
            "blocks": ["minecraft:furnace"], "radius": 4, "limit": 4,
        }).get("found", [])
        positions = [tuple(int(row[k]) for k in ("x", "y", "z")) for row in found]
        positions = sorted(positions, key=lambda pos: storage_distance(live, pos))
        if not positions or storage_distance(live, positions[0]) > 4.5:
            return False
        position = positions[0]
        if client.transport.dispatch("get_block", dict(zip(("x", "y", "z"), position))).get("id") != "minecraft:furnace":
            return False
        try:
            if not harness_ops.open_container(client, position, attempts=1, timeout=2.0):
                return False
            screen = client.transport.dispatch("get_screen", {})
            screen = screen.get("data", screen)
            if screen.get("type") not in {"FurnaceMenu", "FurnaceScreenHandler"} or screen.get("total_slots") != 39:
                return False
        finally:
            harness_ops.close_container(client)
        moved = deposit_excess_to_chest(
            client, tuple(chest), deposit_items={"minecraft:furnace"},
            retain_counts={"minecraft:furnace": 0},
        )
        gained = moved > 0 and free_inventory_slots(client) >= required
        if gained:
            print("STORAGE: banked portable furnace beside verified installed furnace; working slot recovered")
        return gained
    except Exception as exc:
        print(f"STORAGE: installed-furnace verification deferred ({exc})")
        return False
