"""Wheat-farm establishment and harvesting - a renewable food/breeding supply.

Breeding a herd (see husbandry.py) needs a standing wheat supply, and an
animal-sparse biome means hunting alone can't provide it. This module
establishes a small irrigated wheat patch once, then repeatedly harvests it
via Baritone's own farm process (the bridge "farm" route) rather than
reimplementing plant/harvest looping -- the bridge only tills/plants what it
finds, so the one-time bootstrap here (water + tilled, seeded soil) is what
makes that route worth calling at all.
"""

import time
from typing import Optional, Tuple

from .inventory import count_item, craft, select_item
from .navigation import find_nearby_block, goto


def _block_id(client, x: int, y: int, z: int) -> str:
    try:
        return client.transport.dispatch(
            "get_block", {"x": int(x), "y": int(y), "z": int(z)}
        ).get("id", "")
    except Exception:
        return ""


def ensure_farm_water(client, x: int, y: int, z: int) -> bool:
    """Ensure a water source sits at the farm center, placing one if needed.

    Farmland only stays hydrated (and crops grow at a reasonable speed)
    within 4 blocks of water. Skipping this leaves a farm that "plants"
    successfully but barely produces anything -- not a real food source.
    """
    # The bucket empties onto the tile directly above solid ground: the
    # bridge's place_block requires the target coordinate itself to be
    # replaceable (air), then finds the solid neighbor face to click against.
    if "water" in _block_id(client, x, y, z) or "water" in _block_id(client, x, y + 1, z):
        return True

    if count_item(client, "minecraft:water_bucket") < 1:
        source = find_nearby_block(client, ["minecraft:water"], radius=48)
        if source is None:
            print("  No water source nearby to fill a bucket for the farm.")
            return False
        if count_item(client, "minecraft:bucket") < 1 and not craft(
            client, "minecraft:bucket", 1
        ):
            print("  No bucket available/craftable to carry water to the farm.")
            return False
        if not goto(client, source[0], source[1], source[2], timeout=120, tolerance=2):
            print("  Could not reach a water source to fill a bucket.")
            return False
        if not select_item(client, "minecraft:bucket"):
            return False
        client.transport.dispatch(
            "look_at",
            {"x": source[0] + 0.5, "y": source[1] + 0.5, "z": source[2] + 0.5},
        )
        time.sleep(0.3)
        client.transport.dispatch("use_item", {"duration_ms": 0})
        time.sleep(0.5)
        if count_item(client, "minecraft:water_bucket") < 1:
            print("  Could not fill a water bucket for the farm.")
            return False

    if not goto(client, x, y, z, timeout=120, tolerance=3):
        print("  Could not reach the farm center to place water.")
        return False
    if not select_item(client, "minecraft:water_bucket"):
        return False
    try:
        result = client.transport.dispatch(
            "place_block",
            {"x": x, "y": y + 1, "z": z, "item": "minecraft:water_bucket"},
        )
    except Exception as exc:
        print(f"  Placing farm water failed: {exc}")
        return False
    if isinstance(result, dict) and result.get("error"):
        print(f"  Placing farm water failed: {result.get('error')}")
        return False
    return "water" in _block_id(client, x, y + 1, z)


def _till_and_plant_tile(client, x: int, y: int, z: int) -> bool:
    """Till one ground tile and plant a wheat seed on it, if not already done."""
    surface = _block_id(client, x, y, z)
    if surface == "minecraft:farmland":
        pass
    elif surface in ("minecraft:dirt", "minecraft:grass_block"):
        if count_item(client, "minecraft:wooden_hoe") == 0 and not craft(
            client, "minecraft:wooden_hoe", 1
        ):
            return False
        if not select_item(client, "minecraft:wooden_hoe"):
            return False
        client.transport.dispatch("look_at", {"x": x + 0.5, "y": y + 1.0, "z": z + 0.5})
        time.sleep(0.15)
        client.transport.dispatch("interact_block", {"x": x, "y": y, "z": z})
        time.sleep(0.2)
    else:
        # Not tillable ground (stone, water, path, etc.) -- skip this tile.
        return False

    above = _block_id(client, x, y + 1, z)
    if "wheat" in above:
        return True  # already planted
    if count_item(client, "minecraft:wheat_seeds") < 1:
        return False
    if not select_item(client, "minecraft:wheat_seeds"):
        return False
    client.transport.dispatch("look_at", {"x": x + 0.5, "y": y + 1.0, "z": z + 0.5})
    time.sleep(0.15)
    client.transport.dispatch("interact_block", {"x": x, "y": y, "z": z})
    time.sleep(0.2)
    return "wheat" in _block_id(client, x, y + 1, z)


def _gather_seeds(client, needed: int, timeout: int = 180) -> bool:
    """Gather wheat seeds by breaking grass, until at least ``needed`` are held."""
    if count_item(client, "minecraft:wheat_seeds") >= needed:
        return True
    try:
        client.transport.dispatch(
            "mine",
            {"blocks": ["minecraft:short_grass", "minecraft:tall_grass"], "quantity": needed * 3},
        )
    except Exception:
        pass
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if count_item(client, "minecraft:wheat_seeds") >= needed:
            client.transport.dispatch("cancel", {})
            return True
        time.sleep(2)
    client.transport.dispatch("cancel", {})
    return count_item(client, "minecraft:wheat_seeds") >= needed


def establish_wheat_farm(
    client, x: int, y: int, z: int, size: int = 5
) -> Optional[Tuple[int, int, int]]:
    """Till, irrigate, and plant a size x size wheat patch centered at (x,y,z).

    Returns the farm center on success (to persist as a checkpoint waypoint),
    or None if it could not be established. Safe to call repeatedly -- tiles
    that are already farmland/planted are left alone.
    """
    if not ensure_farm_water(client, x, y, z):
        return None

    half = size // 2
    tiles = [
        (x + dx, y, z + dz)
        for dx in range(-half, half + 1)
        for dz in range(-half, half + 1)
        if not (dx == 0 and dz == 0)  # center tile holds the water
    ]

    if not _gather_seeds(client, len(tiles)):
        print("  Could not gather enough wheat seeds to plant the farm.")
        # Continue anyway -- partial planting with whatever seeds exist is
        # still better than nothing, and count_item is rechecked per tile.

    planted = 0
    for tx, ty, tz in tiles:
        if not goto(client, tx, ty, tz, timeout=30, tolerance=1.5):
            continue
        if _till_and_plant_tile(client, tx, ty, tz):
            planted += 1

    if planted == 0:
        print(f"  Wheat farm at {(x, y, z)} could not be planted (0/{len(tiles)} tiles).")
        return None
    print(f"  Wheat farm at {(x, y, z)} planted {planted}/{len(tiles)} tiles.")
    return (x, y, z)


def harvest_wheat_farm(client, x: int, y: int, z: int, range_: int = 8) -> bool:
    """Run Baritone's own farm process over the established patch.

    Baritone's farm process harvests mature crops and replants from carried
    seeds within range -- verified here by wheat count actually increasing,
    since the bridge command reports "started", not "produced results".
    """
    if not goto(client, x, y, z, timeout=120, tolerance=4):
        return False

    before = count_item(client, "minecraft:wheat")
    try:
        client.transport.dispatch("farm", {"range": range_})
    except Exception as exc:
        print(f"  Farm harvest dispatch failed: {exc}")
        return False

    deadline = time.monotonic() + 60.0
    while time.monotonic() < deadline:
        time.sleep(2)
        if count_item(client, "minecraft:wheat") > before:
            client.transport.dispatch("cancel", {})
            return True
    client.transport.dispatch("cancel", {})
    return count_item(client, "minecraft:wheat") > before
