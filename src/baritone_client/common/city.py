"""
City / megabase construction utilities.

Turns flat terrain (see terraform.py) into a functional, ever-expanding
megabase city: a grid of role-based districts connected by roads and rail,
built with a mix of procedural `#sel` fills and (optional) schematic files.

Like terraform.py, these helpers are decoupled from the automator/Phase
machinery: they only need a `client` and an optional plain `progress` dict, so
they can be driven either from a phase handler or from a standalone script
(city_builder_forever.py) without touching any shared/hot files.

See plans/CITY_BUILD_PLAN.md for the full design.
"""

import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from .tasks import TaskResult
from .terraform import (
    CHUNK_SIZE,
    WORLD_MAX_Y,
    WORLD_MIN_Y,
    select_region,
    fill_selection,
    clear_selection,
    await_builder_idle,
    terraform_chunk,
    chunk_ring_offsets,
)

# --- Layout defaults -------------------------------------------------------

DISTRICT_CHUNKS = 4                       # district = 4x4 chunks = 64x64 blocks
DISTRICT_SIZE = DISTRICT_CHUNKS * CHUNK_SIZE
ROAD_WIDTH = 4                            # blocks reserved for the road margin
BUILD_INSET = ROAD_WIDTH                  # keep structures off the road

# District roles cycle in this order as rings expand (see plans/CITY_BUILD_PLAN.md).
DISTRICT_ROLES = ["storage", "farm", "industrial", "grinder", "depot"]

# Default palette (all vanilla, craftable/gatherable in survival).
PALETTE = {
    "road": "minecraft:stone_bricks",
    "foundation": "minecraft:smooth_stone",
    "wall": "minecraft:stone_bricks",
    "floor": "minecraft:smooth_stone",
    "roof": "minecraft:stone_brick_slab",
    "farmland": "minecraft:farmland",
    "water": "minecraft:water",
    "plaza": "minecraft:polished_andesite",
    "beacon_base": "minecraft:iron_block",
}

# Optional schematic overrides per role: {role: schematic_id}. If a role has an
# entry here (and the file has been uploaded), the builder process is used
# instead of the procedural fallback.
SCHEMATIC_REGISTRY: Dict[str, str] = {}


# --- Low-level build primitives -------------------------------------------

def build_box(
    client,
    x1: int, y1: int, z1: int,
    x2: int, y2: int, z2: int,
    block: str,
    timeout: float = 600.0,
) -> bool:
    """Fill an axis-aligned box (inclusive corners) with `block` via `#sel`."""
    lo_y = max(min(y1, y2), WORLD_MIN_Y)
    hi_y = min(max(y1, y2), WORLD_MAX_Y)
    select_region(client, x1, lo_y, z1, x2, hi_y, z2)
    ok = fill_selection(client, block, timeout=timeout)
    clear_selection(client)
    return ok


def build_hollow_building(
    client,
    x: int, y: int, z: int,
    width: int, depth: int, height: int,
    wall_block: str,
    floor_block: str,
    roof_block: Optional[str] = None,
    door_side: str = "south",
    timeout: float = 600.0,
) -> TaskResult:
    """
    Build a hollow rectangular building: solid shell, hollowed interior, floor,
    optional distinct roof, and a 1x2 doorway on one side.

    Origin (x, y, z) is the minimum corner; the building extends +width in X,
    +depth in Z, +height in Y.
    """
    x2, y2, z2 = x + width - 1, y + height - 1, z + depth - 1

    # 1. Solid shell.
    if not build_box(client, x, y, z, x2, y2, z2, wall_block, timeout=timeout):
        return TaskResult.fail("Building shell fill timed out", origin=(x, y, z))

    # 2. Hollow the interior (leave a 1-block shell) with air.
    if width > 2 and depth > 2 and height > 2:
        if not build_box(client, x + 1, y + 1, z + 1, x2 - 1, y2 - 1, z2 - 1, "air", timeout=timeout):
            return TaskResult.fail("Building hollow pass timed out", origin=(x, y, z))

    # 3. Floor.
    if not build_box(client, x, y, z, x2, y, z2, floor_block, timeout=timeout):
        return TaskResult.fail("Building floor fill timed out", origin=(x, y, z))

    # 4. Optional distinct roof layer.
    if roof_block:
        build_box(client, x, y2, z, x2, y2, z2, roof_block, timeout=timeout)

    # 5. Doorway: clear a 1x2 opening at the middle of the chosen wall.
    _cut_doorway(client, x, y, z, x2, z2, door_side, timeout=timeout)

    return TaskResult.ok("Building constructed", origin=(x, y, z), size=(width, depth, height))


def _cut_doorway(client, x, y, z, x2, z2, side, timeout=120.0) -> None:
    mid_x = (x + x2) // 2
    mid_z = (z + z2) // 2
    if side == "south":
        dx1, dz1, dx2, dz2 = mid_x, z2, mid_x, z2
    elif side == "north":
        dx1, dz1, dx2, dz2 = mid_x, z, mid_x, z
    elif side == "east":
        dx1, dz1, dx2, dz2 = x2, mid_z, x2, mid_z
    else:  # west
        dx1, dz1, dx2, dz2 = x, mid_z, x, mid_z
    build_box(client, dx1, y + 1, dz1, dx2, y + 2, dz2, "air", timeout=timeout)


def pave_road(
    client,
    x1: int, z1: int,
    x2: int, z2: int,
    y: int,
    block: str = None,
    timeout: float = 600.0,
) -> bool:
    """Pave a one-block-thick road slab at height `y` over the given footprint."""
    block = block or PALETTE["road"]
    return build_box(client, x1, y, z1, x2, y, z2, block, timeout=timeout)


def lay_rail(
    client,
    x1: int, z1: int,
    x2: int, z2: int,
    y: int,
    rail_item: str = "minecraft:rail",
    settle: float = 0.05,
) -> int:
    """
    Best-effort: place rail along a straight centerline at height `y+1`.

    NOTE: rail placement/orientation depends on the bridge's `place_block`
    behavior and on having rails in inventory. This is intentionally a
    best-effort convenience — if it fails, the paved road still connects the
    districts and rail can be added later. Returns the count of placement
    attempts that did not raise.
    """
    placed = 0
    if z1 == z2:  # east-west line
        lo, hi = sorted((x1, x2))
        for bx in range(lo, hi + 1):
            if _try_place(client, bx, y + 1, z1, rail_item):
                placed += 1
            time.sleep(settle)
    elif x1 == x2:  # north-south line
        lo, hi = sorted((z1, z2))
        for bz in range(lo, hi + 1):
            if _try_place(client, x1, y + 1, bz, rail_item):
                placed += 1
            time.sleep(settle)
    return placed


def _try_place(client, x, y, z, item) -> bool:
    try:
        client.transport.dispatch("place_block", {"x": x, "y": y, "z": z, "block": item})
        return True
    except Exception:
        return False


def build_from_schematic(client, role: str, x: int, y: int, z: int, timeout: float = 600.0) -> Optional[bool]:
    """
    If a schematic is registered for `role`, place it via the builder process
    at (x, y, z) and wait for completion. Returns True/False on attempt, or
    None if no schematic is registered (caller should use procedural fallback).
    """
    schematic_id = SCHEMATIC_REGISTRY.get(role)
    if not schematic_id:
        return None
    try:
        client.process.builder.start(schematic_id, x=x, y=y, z=z)
        return await_builder_idle(client, timeout=timeout)
    except Exception as exc:
        print(f"  [city] schematic build for role '{role}' failed ({exc}); using procedural fallback.")
        return None


# --- District geometry -----------------------------------------------------

def district_origin(center_x: int, center_z: int, dcx: int, dcz: int) -> Tuple[int, int]:
    """World-space min corner (x, z) of the district at district-offset (dcx, dcz)."""
    ox = center_x + dcx * DISTRICT_SIZE - DISTRICT_SIZE // 2
    oz = center_z + dcz * DISTRICT_SIZE - DISTRICT_SIZE // 2
    return ox, oz


def role_for(dcx: int, dcz: int) -> str:
    """Deterministic role assignment for a district cell (stable across runs)."""
    if dcx == 0 and dcz == 0:
        return "plaza"
    # Cheap stable hash over the offset so the same cell always gets the same role.
    idx = (abs(dcx) * 73856093 ^ abs(dcz) * 19349663) % len(DISTRICT_ROLES)
    return DISTRICT_ROLES[idx]


def district_ring_offsets(ring: int):
    """District-offsets forming the square ring at Chebyshev distance `ring`."""
    yield from chunk_ring_offsets(ring)  # same square-ring math, different unit


# --- District builders -----------------------------------------------------

def _flatten_district(client, ox: int, oz: int, target_y: int, on_chunk=None) -> None:
    """Terraform every chunk of the district footprint to target_y."""
    for cx in range(ox, ox + DISTRICT_SIZE, CHUNK_SIZE):
        for cz in range(oz, oz + DISTRICT_SIZE, CHUNK_SIZE):
            terraform_chunk(client, cx, cz, target_y)
            if on_chunk:
                on_chunk((cx, cz))


def build_plaza(client, ox: int, oz: int, target_y: int, timeout: float = 600.0) -> TaskResult:
    """Central civic core: paved plaza + beacon foundation at the middle."""
    x2, z2 = ox + DISTRICT_SIZE - 1, oz + DISTRICT_SIZE - 1
    if not build_box(client, ox, target_y, oz, x2, target_y, z2, PALETTE["plaza"], timeout=timeout):
        return TaskResult.fail("Plaza paving timed out", origin=(ox, oz))
    # 3x3 beacon base at plaza center, one layer above the pavement.
    mx, mz = (ox + x2) // 2, (oz + z2) // 2
    build_box(client, mx - 1, target_y + 1, mz - 1, mx + 1, target_y + 1, mz + 1,
              PALETTE["beacon_base"], timeout=timeout)
    return TaskResult.ok("Plaza built", origin=(ox, oz))


def build_storage_hall(client, ox, oz, target_y, timeout=600.0) -> TaskResult:
    inner = DISTRICT_SIZE - 2 * BUILD_INSET
    return build_hollow_building(
        client, ox + BUILD_INSET, target_y, oz + BUILD_INSET,
        width=inner, depth=inner, height=6,
        wall_block=PALETTE["wall"], floor_block=PALETTE["floor"],
        roof_block=PALETTE["roof"], timeout=timeout,
    )


def build_industrial(client, ox, oz, target_y, timeout=600.0) -> TaskResult:
    inner = DISTRICT_SIZE - 2 * BUILD_INSET
    return build_hollow_building(
        client, ox + BUILD_INSET, target_y, oz + BUILD_INSET,
        width=inner, depth=inner, height=8,
        wall_block=PALETTE["wall"], floor_block=PALETTE["floor"],
        roof_block=PALETTE["roof"], timeout=timeout,
    )


def build_grinder(client, ox, oz, target_y, timeout=600.0) -> TaskResult:
    # Tall, narrow tower footprint centered in the district.
    inner = (DISTRICT_SIZE - 2 * BUILD_INSET) // 2
    cx = ox + DISTRICT_SIZE // 2 - inner // 2
    cz = oz + DISTRICT_SIZE // 2 - inner // 2
    return build_hollow_building(
        client, cx, target_y, cz,
        width=inner, depth=inner, height=16,
        wall_block=PALETTE["wall"], floor_block=PALETTE["floor"],
        roof_block=PALETTE["roof"], timeout=timeout,
    )


def build_depot(client, ox, oz, target_y, timeout=600.0) -> TaskResult:
    inner = DISTRICT_SIZE - 2 * BUILD_INSET
    return build_hollow_building(
        client, ox + BUILD_INSET, target_y, oz + BUILD_INSET,
        width=inner, depth=inner, height=5,
        wall_block=PALETTE["wall"], floor_block=PALETTE["floor"],
        roof_block=PALETTE["roof"], timeout=timeout,
    )


def build_farm(client, ox, oz, target_y, timeout=600.0) -> TaskResult:
    """
    Walled crop plot: low wall perimeter, farmland floor, and a water strip
    down the middle to hydrate the field.
    """
    x2, z2 = ox + DISTRICT_SIZE - 1, oz + DISTRICT_SIZE - 1
    ix1, iz1 = ox + BUILD_INSET, oz + BUILD_INSET
    ix2, iz2 = x2 - BUILD_INSET, z2 - BUILD_INSET

    # Low perimeter wall (2 high) around the plot.
    if not build_box(client, ix1, target_y + 1, iz1, ix2, target_y + 2, iz2, PALETTE["wall"], timeout=timeout):
        return TaskResult.fail("Farm wall timed out", origin=(ox, oz))
    # Hollow the interior of the wall back out.
    build_box(client, ix1 + 1, target_y + 1, iz1 + 1, ix2 - 1, target_y + 2, iz2 - 1, "air", timeout=timeout)
    # Farmland floor.
    build_box(client, ix1, target_y, iz1, ix2, target_y, iz2, PALETTE["farmland"], timeout=timeout)
    # Central water hydration strip (1-wide) at floor level.
    mz = (iz1 + iz2) // 2
    build_box(client, ix1 + 1, target_y, mz, ix2 - 1, target_y, mz, PALETTE["water"], timeout=timeout)
    return TaskResult.ok("Farm plot built", origin=(ox, oz))


ROLE_BUILDERS: Dict[str, Callable] = {
    "plaza": build_plaza,
    "storage": build_storage_hall,
    "industrial": build_industrial,
    "grinder": build_grinder,
    "depot": build_depot,
    "farm": build_farm,
}


def build_district(
    client,
    center_x: int, center_z: int,
    dcx: int, dcz: int,
    target_y: int,
    flatten: bool = True,
    with_roads: bool = True,
    with_rail: bool = False,
    timeout: float = 600.0,
) -> TaskResult:
    """
    Build a single district end to end: flatten -> roads/rail -> structure.

    Args:
        dcx, dcz: district offset from center (in districts)
        flatten: terraform the footprint first (idempotent)
        with_roads: pave the district's road margins
        with_rail: best-effort rail down the road centerlines
    """
    ox, oz = district_origin(center_x, center_z, dcx, dcz)
    x2, z2 = ox + DISTRICT_SIZE - 1, oz + DISTRICT_SIZE - 1
    role = role_for(dcx, dcz)

    if flatten:
        _flatten_district(client, ox, oz, target_y)

    if with_roads:
        # Pave the two lower-edge road margins; neighbors pave their own, so the
        # whole grid ends up connected without double-paving every seam.
        pave_road(client, ox, oz, x2, oz + ROAD_WIDTH - 1, target_y, timeout=timeout)          # south edge
        pave_road(client, ox, oz, ox + ROAD_WIDTH - 1, z2, target_y, timeout=timeout)          # west edge
        if with_rail:
            mid = oz + ROAD_WIDTH // 2
            lay_rail(client, ox, mid, x2, mid, target_y)

    # Structure: schematic if registered, else procedural role builder.
    sx, sy, sz = ox + BUILD_INSET, target_y, oz + BUILD_INSET
    schem = build_from_schematic(client, role, sx, sy, sz, timeout=timeout)
    if schem is None:
        builder = ROLE_BUILDERS.get(role, build_depot)
        result = builder(client, ox, oz, target_y, timeout=timeout)
    else:
        result = TaskResult.ok("Built from schematic", role=role) if schem else \
            TaskResult.fail("Schematic build failed", role=role)

    result.data.setdefault("role", role)
    result.data.setdefault("origin", (ox, oz))
    result.data.setdefault("district", (dcx, dcz))
    return result


def build_ring(
    client,
    center_x: int, center_z: int,
    target_y: int,
    ring: int,
    flatten: bool = True,
    with_roads: bool = True,
    with_rail: bool = False,
    progress: Optional[Dict[str, Any]] = None,
    on_district_done: Optional[Callable[[int, int, Dict[str, Any]], None]] = None,
    timeout: float = 600.0,
) -> TaskResult:
    """
    Build every district in the square ring at distance `ring`. Resumable via a
    `progress` dict (`progress["next_index"]`), mirroring terraform.build_ring.
    """
    offsets = list(district_ring_offsets(ring))
    total = len(offsets)
    start_index = (progress or {}).get("next_index", 0)

    built = 0
    for i in range(start_index, total):
        dcx, dcz = offsets[i]
        result = build_district(
            client, center_x, center_z, dcx, dcz, target_y,
            flatten=flatten, with_roads=with_roads, with_rail=with_rail, timeout=timeout,
        )
        built += 1

        if progress is not None:
            progress["next_index"] = i + 1
            progress["total"] = total

        if on_district_done:
            on_district_done(i + 1, total, result.data)

        if not result.success:
            print(f"  [city] district {result.data.get('district')} issue: {result.reason}")

    if progress is not None:
        progress["next_index"] = total

    return TaskResult.ok(f"Built ring {ring} ({built} districts)", ring=ring, districts=built)
