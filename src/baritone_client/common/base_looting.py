"""Container-looting workflow kept separate from base construction."""

from __future__ import annotations

import time
from typing import Any


def loot_nearby_chests(client: Any, radius: int = 16) -> bool:
    """Loot only container slots and update the durable storage catalog."""
    from . import base as api

    print(f"Searching for nearby chests (radius {radius})...")
    response = client.transport.dispatch(
        "find_blocks",
        {
            "blocks": [
                "minecraft:chest",
                "minecraft:trapped_chest",
                "minecraft:barrel",
            ],
            "radius": radius,
            "limit": 5,
        },
    )
    found = response.get("found", [])
    if not found:
        print("  No chests found nearby.")
        return False
    print(f"  Found {len(found)} chests. Looting...")

    for container in found:
        location = (
            int(container["x"]),
            int(container["y"]),
            int(container["z"]),
        )
        print(f"  Walking to chest at {location}...")
        api.goto(client, *location, tolerance=2)
        if not api.open_chest(client, *location):
            continue
        screen = client.transport.dispatch("get_screen", {})
        data = screen.get("data", screen)
        slots = data.get("slots", [])
        total_slots = int(data.get("total_slots") or len(slots) or 0)
        container_slots = max(0, total_slots - 36)
        if container_slots not in (27, 54):
            print(
                f"  Unexpected chest screen size {total_slots}; "
                "skipping loot rather than moving player inventory"
            )
            client.transport.dispatch("close_screen", {})
            continue

        container_type = str(container.get("id") or "minecraft:chest")
        _observe_container(
            client, location, screen, container_type, "discovered_loot"
        )
        for slot in range(container_slots):
            client.transport.dispatch(
                "inventory_click",
                {"slot": slot, "button": 0, "type": "QUICK_MOVE"},
            )
            if slot % 9 == 0:
                time.sleep(0.1)
        _record_loot(client, location, container_type, container_slots)
        client.transport.dispatch("close_screen", {})
        time.sleep(0.3)
    return True


def _observe_container(
    client: Any,
    location: tuple[int, int, int],
    screen: dict,
    container_type: str,
    purpose: str,
) -> None:
    try:
        from .storage_catalog import observe_open_container

        observe_open_container(
            client,
            location,
            screen,
            container_type=container_type,
            purpose=purpose,
        )
    except Exception as exc:
        print(f"  Loot catalog snapshot deferred: {exc}")


def _record_loot(
    client: Any,
    location: tuple[int, int, int],
    container_type: str,
    container_slots: int,
) -> None:
    try:
        from .storage_catalog import catalog_for

        final_screen = client.transport.dispatch("get_screen", {})
        _observe_container(
            client,
            location,
            final_screen,
            container_type,
            "discovered_loot",
        )
        dimension = client.transport.dispatch("get_state", {}).get(
            "dimension", "minecraft:overworld"
        )
        catalog_for(client).record_event(
            location,
            "loot",
            dimension=str(dimension),
            details={"container_slots": container_slots},
        )
    except Exception as exc:
        print(f"  Post-loot catalog snapshot deferred: {exc}")
