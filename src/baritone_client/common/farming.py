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

from .base import robust_place
from .inventory import count_item, craft, select_item
from .navigation import find_nearby_block, goto

_UNSUPPORTIVE_GROUND = {"minecraft:air", "minecraft:cave_air", "minecraft:water", "minecraft:lava"}
_FARM_SOIL_ITEMS = ("minecraft:dirt", "minecraft:coarse_dirt", "minecraft:grass_block")
_FARM_REPLACEABLE = {
    "minecraft:air",
    "minecraft:cave_air",
    "minecraft:short_grass",
    "minecraft:tall_grass",
    "minecraft:fern",
    "minecraft:large_fern",
    "minecraft:dead_bush",
    "minecraft:snow",
    "minecraft:wildflowers",
    "minecraft:leaf_litter",
}


def surface_soil(block_id, candidate, max_rise: int = 6):
    """Promote a buried soil search hit to tillable soil with air above it."""
    if candidate is None:
        return None
    x, y, z = (int(value) for value in candidate)
    tillable = {"minecraft:farmland", "minecraft:dirt", "minecraft:grass_block"}
    for rise in range(max_rise + 1):
        surface_y = y + rise
        if (
            block_id(x, surface_y, z) in tillable
            and block_id(x, surface_y + 1, z) in _FARM_REPLACEABLE
        ):
            return (x, surface_y, z)
    return None


#: Horizontal half-width of the homestead envelope, mirroring
#: actions.homestead.SAFE_RADIUS. Imported lazily at the call site would be a
#: cycle (homestead imports common.*), so the value is duplicated here and the
#: two must stay in step.
_HOMESTEAD_SAFE_RADIUS = 24.0


def within_homestead(custom_data, position) -> bool:
    """Is *position* inside the current homestead envelope?

    True when no anchor is known, so a fresh run is never blocked by a check
    it has no data for.
    """
    anchor = None
    homestead = custom_data.get("homestead") if hasattr(custom_data, "get") else None
    if isinstance(homestead, dict):
        anchor = homestead.get("anchor")
    if anchor is None and hasattr(custom_data, "get"):
        anchor = custom_data.get("homestead_anchor")
    if not isinstance(anchor, (list, tuple)) or len(anchor) < 3:
        return True
    try:
        dx = float(position[0]) - float(anchor[0])
        dz = float(position[2]) - float(anchor[2])
    except (TypeError, ValueError, IndexError):
        return True
    return (dx * dx + dz * dz) ** 0.5 <= _HOMESTEAD_SAFE_RADIUS


def local_saved_farm(saved_farm, custom_data, block_id):
    """Resolve a checkpointed farm location, ignoring one left at an old site.

    The homestead relocates on barren ground, so after several moves the stored
    location can be far outside the local envelope. Walking back to it is both
    a trip the caller's 45s goto cannot make and a plot outside the base it is
    meant to feed -- live 2026-08-10, 90 blocks away after 22 relocations.
    Returns (center, irrigated); center is None when there is nothing usable.
    """
    if not isinstance(saved_farm, (list, tuple)) or len(saved_farm) != 3:
        return None, False
    try:
        center = tuple(int(value) for value in saved_farm)
    except (TypeError, ValueError):
        return None, False
    if not within_homestead(custom_data, center):
        print(f"  Ignoring farm at {center}: outside the homestead envelope")
        return None, False
    return center, block_id(*center) == "minecraft:water"


def find_natural_crop_center(client, block_id):
    """Search for existing tillable soil, falling back to a water source."""
    # Search exposed grass before generic dirt. A combined nearest-block query
    # returns buried foundation dirt under an industrial platform and masks a
    # usable grass surface only a few blocks farther away.
    for block_ids in (
        ["minecraft:farmland", "minecraft:grass_block"],
        ["minecraft:dirt"],
    ):
        soil = find_nearby_block(client, block_ids, radius=20)
        if (soil := surface_soil(block_id, soil)) is not None:
            return tuple(int(value) for value in soil), False
    water = find_nearby_block(client, ["minecraft:water"], radius=16)
    if water is not None:
        return tuple(int(value) for value in water), True
    return None, False


def place_farm_soil(client, block_id) -> Optional[Tuple[int, int, int]]:
    """Place carried dirt to create a plot center when no natural soil exists.

    A bare, rocky homestead site (e.g. a re-homed mountaintop anchor) has
    nothing for find_nearby_block to match, even though the bot may be
    carrying dirt from its own reserves -- 2026-08-08, Y=140, "no reachable
    soil or water found" on every cycle with 31 dirt in the inventory the
    whole time. Converts one open column adjacent to the bot, sitting over
    solid ground, into farmable soil instead of giving up.
    """
    soil_item = next(
        (item for item in _FARM_SOIL_ITEMS if count_item(client, item) > 0),
        None,
    )
    if soil_item is None:
        return None

    position = client.transport.dispatch("get_state", {}).get("block_position", {})
    bx, by, bz = int(position.get("x", 0)), int(position.get("y", 64)), int(position.get("z", 0))
    # The bot's own level first, then the ground plane one block lower -- where
    # the plot actually belongs when the bot is standing on open ground rather
    # than in a hollow. Checking only one plane misses the common case.
    for level in (by, by - 1):
        for dx in (-1, 0, 1):
            for dz in (-1, 0, 1):
                if dx == 0 and dz == 0:
                    continue
                cx, cz = bx + dx, bz + dz
                if block_id(cx, level - 1, cz) in _UNSUPPORTIVE_GROUND:
                    continue
                if block_id(cx, level, cz) not in {"minecraft:air", "minecraft:cave_air"}:
                    continue
                if robust_place(client, cx, level, cz, soil_item):
                    return (cx, level, cz)
    return None


def _block_id(client, x: int, y: int, z: int) -> str:
    try:
        return client.transport.dispatch(
            "get_block", {"x": int(x), "y": int(y), "z": int(z)}
        ).get("id", "")
    except Exception:
        return ""


def find_farm_surface_near(
    client,
    x: int,
    y: int,
    z: int,
    *,
    horizontal_radius: int = 2,
    max_rise: int = 6,
    max_drop: int = 20,
) -> Optional[Tuple[int, int, int]]:
    """Resolve a planned X/Z site onto nearby exposed tillable terrain."""
    tillable = {"minecraft:dirt", "minecraft:grass_block", "minecraft:farmland"}
    radius = max(0, int(horizontal_radius))
    try:
        view = client.transport.dispatch("get_view", {"radius": max(8, radius)})
        voxels = view.get("voxels", [])
        blocks = {
            (int(block["x"]), int(block["y"]), int(block["z"])): block.get(
                "id", ""
            )
            for block in voxels
        }
        surfaces = [
            position
            for position, block_id in blocks.items()
            if block_id in tillable
            and blocks.get((position[0], position[1] + 1, position[2]))
            in _FARM_REPLACEABLE
            and (position[0] - x) ** 2 + (position[2] - z) ** 2 <= radius ** 2
            and y - max_drop <= position[1] <= y + max_rise
        ]
        if surfaces:
            def usable_neighbors(position):
                px, py, pz = position
                return sum(
                    1
                    for dx in range(-2, 3)
                    for dz in range(-2, 3)
                    if (dx or dz)
                    and blocks.get((px + dx, py, pz + dz)) in tillable
                    and blocks.get((px + dx, py + 1, pz + dz))
                    in _FARM_REPLACEABLE
                )

            return min(
                surfaces,
                key=lambda position: (
                    -usable_neighbors(position),
                    (position[0] - x) ** 2 + (position[2] - z) ** 2,
                    abs(position[1] - y),
                ),
            )
    except Exception:
        pass
    offsets = [(0, 0)] + [
        (dx, dz)
        for radius in range(1, max(0, int(horizontal_radius)) + 1)
        for dx in range(-radius, radius + 1)
        for dz in range(-radius, radius + 1)
        if max(abs(dx), abs(dz)) == radius
    ]
    for dx, dz in offsets:
        for surface_y in range(y + max_rise, y - max_drop - 1, -1):
            if (
                _block_id(client, x + dx, surface_y, z + dz) in tillable
                and _block_id(client, x + dx, surface_y + 1, z + dz)
                in _FARM_REPLACEABLE
            ):
                return (x + dx, surface_y, z + dz)
    return None


def _ensure_farm_bucket(client, state=None) -> bool:
    """Recover or craft the empty bucket needed to irrigate a new farm."""
    if count_item(client, "minecraft:bucket") >= 1:
        return True

    if state is not None and getattr(state, "checkpoint_dir", None):
        from .inventory import withdraw_required_from_catalog

        withdraw_required_from_catalog(
            client,
            {"minecraft:bucket": 1},
            state=state,
            max_containers=12,
            max_travel_distance=96.0,
            allow_recovery_access=True,
        )
        if count_item(client, "minecraft:bucket") >= 1:
            return True

        # A catalog may have no finished bucket while still holding the three
        # ingots needed for one. Recover only that bounded prerequisite; the
        # crafting helper prepares a table and converts carried logs to planks.
        withdraw_required_from_catalog(
            client,
            {"minecraft:iron_ingot": 3},
            state=state,
            max_containers=12,
            max_travel_distance=96.0,
            allow_recovery_access=True,
        )
        from .resources import _craft_with_table

        return bool(_craft_with_table(client, "minecraft:bucket", 1))

    return bool(craft(client, "minecraft:bucket", 1))


def ensure_farm_water(client, x: int, y: int, z: int, state=None) -> bool:
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
        if count_item(client, "minecraft:bucket") < 1 and not _ensure_farm_bucket(
            client, state
        ):
            print("  No bucket available/craftable to carry water to the farm.")
            return False
        if not goto(client, source[0], source[1], source[2], timeout=120, tolerance=2):
            print("  Could not reach a water source to fill a bucket.")
            return False
        if not select_item(client, "minecraft:bucket", allow_swap=True):
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
    above = _block_id(client, x, y + 1, z)
    if above not in {"minecraft:air", "minecraft:cave_air"}:
        if above not in _FARM_REPLACEABLE:
            print(f"  Farm water center is blocked by {above}.")
            return False
        client.transport.dispatch("attack_block", {"x": x, "y": y + 1, "z": z})
        time.sleep(0.15)
        if _block_id(client, x, y + 1, z) not in {
            "minecraft:air",
            "minecraft:cave_air",
        }:
            print(f"  Could not clear {above} from the farm water center.")
            return False
    if not select_item(client, "minecraft:water_bucket", allow_swap=True):
        return False
    try:
        client.transport.dispatch(
            "look_at",
            {"x": x + 0.5, "y": y + 0.5, "z": z + 0.5},
        )
        time.sleep(0.2)
        client.transport.dispatch("use_item", {"duration_ms": 0})
        time.sleep(0.5)
    except Exception as exc:
        print(f"  Placing farm water failed: {exc}")
        return False
    return "water" in _block_id(client, x, y + 1, z)


def _till_and_plant_tile(client, x: int, y: int, z: int) -> bool:
    """Till one ground tile and plant a wheat seed on it, if not already done."""
    above = _block_id(client, x, y + 1, z)
    if above not in {"minecraft:air", "minecraft:cave_air"}:
        if above not in _FARM_REPLACEABLE:
            return False
        client.transport.dispatch("attack_block", {"x": x, "y": y + 1, "z": z})
        time.sleep(0.15)
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
    except Exception as exc:
        # Waiting out the full timeout for a mine that never started is pure
        # dead time, and this runs on every food cycle now.
        print(f"  Seed gathering could not start: {exc}")
        return False
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if count_item(client, "minecraft:wheat_seeds") >= needed:
            client.transport.dispatch("cancel", {})
            return True
        time.sleep(2)
    client.transport.dispatch("cancel", {})
    return count_item(client, "minecraft:wheat_seeds") >= needed


def establish_wheat_farm(
    client, x: int, y: int, z: int, size: int = 5, state=None
) -> Optional[Tuple[int, int, int]]:
    """Till, irrigate, and plant a size x size wheat patch centered at (x,y,z).

    Returns the farm center on success (to persist as a checkpoint waypoint),
    or None if it could not be established. Safe to call repeatedly -- tiles
    that are already farmland/planted are left alone.
    """
    if not ensure_farm_water(client, x, y, z, state=state):
        return None

    half = size // 2
    tiles = [
        (x + dx, y, z + dz)
        for dx in range(-half, half + 1)
        for dz in range(-half, half + 1)
        if not (dx == 0 and dz == 0)  # center tile holds the water
    ]

    carried_seeds = count_item(client, "minecraft:wheat_seeds")
    # A partial first planting is intentionally productive: those crops yield
    # more seeds on the next harvest. Once a useful starter batch is carried,
    # do not spend the full gathering timeout chasing a perfect 24/24 plot.
    seed_target = (
        len(tiles)
        if carried_seeds < min(8, len(tiles))
        else min(carried_seeds, len(tiles))
    )
    if not _gather_seeds(client, seed_target):
        print("  Could not gather enough wheat seeds to plant the farm.")
        # Continue anyway -- partial planting with whatever seeds exist is
        # still better than nothing, and count_item is rechecked per tile.

    planted = 0
    for tx, ty, tz in tiles:
        # Every tile in this five-wide patch is within normal interaction
        # reach from its center. Requiring the pathfinder to stand almost on
        # each individual tile makes uneven ground consume the full timeout
        # repeatedly even though tilling and planting are already reachable.
        if not goto(client, tx, ty, tz, timeout=20, tolerance=3.5):
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
