"""Bounded escape from thin high-altitude shelves and spawn platforms."""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from .movement_recovery import (
    MovementWatchdog,
    block_position,
    nearest_lower_surface_candidates,
)
from .navigation import goto
from .shelf_escape import harvest_shelf_dirt, supported_column_descent


_SURFACE_BLOCKS = [
    "minecraft:grass_block",
    "minecraft:dirt",
    "minecraft:coarse_dirt",
    "minecraft:podzol",
    "minecraft:stone",
]

# None of the Overworld ground blocks exist in the Nether, so the candidate
# search found nothing and the escape silently no-op'd for a bot that was
# genuinely marooned. Live 2026-08-03: Bot16 and Bot18 both sat on the same
# glowstone blob at (-132, 78, -7) in a basalt delta, air underneath, and
# refused all eight search headings for over an hour.
_NETHER_SURFACE_BLOCKS = [
    "minecraft:netherrack",
    "minecraft:basalt",
    "minecraft:smooth_basalt",
    "minecraft:blackstone",
    "minecraft:soul_sand",
    "minecraft:soul_soil",
    "minecraft:nether_bricks",
    "minecraft:warped_nylium",
    "minecraft:crimson_nylium",
]


def _surface_blocks_for(state: Dict[str, Any]) -> list:
    """Pick landing blocks that actually occur in the bot's dimension."""
    if "nether" in str(state.get("dimension", "")).lower():
        return list(_NETHER_SURFACE_BLOCKS)
    return list(_SURFACE_BLOCKS)

# Never drop onto these, whatever the arithmetic says.
_FATAL_LANDINGS = ("lava", "magma", "fire", "campfire", "cactus", "void")
# Vanilla: no damage for the first three blocks, then half a heart per block.
_FREE_FALL_BLOCKS = 3
# Leave a real buffer -- the landing can still have mobs, and health may be
# mis-sampled by a tick.
_POST_FALL_HEALTH_MARGIN = 8.0


def try_survivable_drop(
    client: Any,
    state: Dict[str, Any],
    *,
    max_scan_depth: int = 24,
) -> Optional[Tuple[int, int, int]]:
    """Step off a pillar when there is no path down and nothing to build with.

    Absolute last resort, and only when the arithmetic clearly says the fall
    is survivable. Live: Bot07 and Bot08 stood on a 3-block cobblestone pillar
    they had built themselves at (-9, 85, -7), inventory completely empty --
    no blocks to bridge with and no pickaxe to mine down. Every other escape
    needs materials, so all of them no-op'd and both bots burned ~4000
    cancelled actions at wood=0/64 across a day and a half. An 11-block drop
    onto the stone below costs about four hearts at full health, which is
    strictly better than being stranded forever.
    """
    x, y, z = block_position(state)
    health = float(state.get("health", 20) or 0)

    def block_at(bx: int, by: int, bz: int) -> Optional[str]:
        try:
            return str(
                client.transport.dispatch(
                    "get_block", {"x": bx, "y": by, "z": bz}
                ).get("id", "")
            )
        except Exception:
            return None

    # Stepping off lands in an ADJACENT column, not the one the bot is
    # standing on -- scanning straight down just finds the pillar underfoot.
    best = None
    for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        cx, cz = x + dx, z + dz
        landing_y = None
        for probe_y in range(y - 1, y - max_scan_depth, -1):
            block = block_at(cx, probe_y, cz)
            if block is None:
                break
            if not block or "air" in block:
                continue
            if any(bad in block for bad in _FATAL_LANDINGS):
                print(f"DEBUG: Refusing drop onto {block} at ({cx}, {probe_y}, {cz})")
                landing_y = None
                break
            landing_y = probe_y
            break
        if landing_y is None:
            continue
        fall = y - (landing_y + 1)
        if fall <= 1:
            continue
        if best is None or fall < best[0]:
            best = (fall, cx, landing_y, cz)

    if best is None:
        return None
    fall, cx, landing_y, cz = best
    damage = max(0, fall - _FREE_FALL_BLOCKS)
    if health - damage < _POST_FALL_HEALTH_MARGIN:
        print(
            f"DEBUG: Drop of {fall} blocks would cost ~{damage:.0f} health "
            f"from {health:.0f}; refusing"
        )
        return None

    print(
        f"DEBUG: No path off and nothing to build with; dropping {fall} "
        f"blocks to ({cx}, {landing_y + 1}, {cz}) "
        f"(~{damage:.0f} damage from {health:.0f})"
    )
    previous_fall_limit = None
    try:
        previous_fall_limit = fall + 2
        client.transport.dispatch(
            "chat", {"message": f"#set maxFallHeightNoWater {previous_fall_limit}"}
        )
        reached = goto(
            client,
            cx,
            landing_y + 1,
            cz,
            timeout=40,
            tolerance=2.0,
        )
    finally:
        client.transport.dispatch(
            "chat", {"message": "#set maxFallHeightNoWater 3"}
        )
    if not reached:
        return None
    after = block_position(client.transport.dispatch("get_state", {}))
    if after[1] <= y - 2:
        print(f"DEBUG: Drop landed at {after}")
        return after
    return None


def try_lower_surface_egress(
    client: Any,
    initial_state: Dict[str, Any],
    *,
    search_radius: int = 40,
    attempt_limit: int = 8,
    timeout_per_candidate: float = 24.0,
    minimum_altitude: int = 96,
    allow_upward_excavation: bool = False,
) -> Optional[Tuple[int, int, int]]:
    """Walk to nearby lower terrain before attempting a vertical tunnel.

    A one-block-thick shelf has no safe block to mine underfoot, but it may
    connect horizontally to the mountain.  Faraway base/storage goals often
    fail path calculation from that shelf; short local surface candidates give
    Baritone a solvable first leg.

    ``minimum_altitude`` guards the original high-shelf/spawn-platform use so
    routine low-altitude work never triggers a descent. Callers that have
    *already proven* the bot is marooned (idle, not pathing, and not moving)
    should lower it: being stranded is a property of the local terrain, not of
    absolute height. Live: Bot07 sat motionless for hours on a single block at
    y=85 with air on every side -- the default gate refused to help precisely
    because it was not high enough.
    """
    origin = block_position(initial_state)
    if origin[1] < minimum_altitude:
        return None
    try:
        below_floor = client.transport.dispatch(
            "get_block",
            {"x": origin[0], "y": origin[1] - 2, "z": origin[2]},
        ).get("id", "")
    except Exception:
        below_floor = ""
    if not below_floor or "air" in str(below_floor):
        # Forward the caller's altitude intent. A caller that passed 0 has
        # already proven the bot is marooned, and the descent's own y<96 gate
        # would otherwise refuse before looking at the void underfoot.
        descent_kwargs = {}
        if minimum_altitude < 96:
            descent_kwargs = {
                "minimum_altitude": minimum_altitude,
                # Descend as far as one pass can carry us rather than aiming
                # at an Overworld shelf height that means nothing here.
                "target_y": max(minimum_altitude, origin[1] - 24),
            }
        descended = supported_column_descent(
            client, initial_state, **descent_kwargs
        )
        if descended is not None:
            print(f"DEBUG: Supported shelf descent reached {descended}")
            return descended
    found = []
    try:
        radii = sorted({min(16, search_radius), min(24, search_radius), search_radius})
        for radius in radii:
            response = client.transport.dispatch(
                "find_blocks",
                {
                    "blocks": _surface_blocks_for(initial_state),
                    "radius": int(radius),
                    "limit": 4096,
                },
            )
            found.extend(response.get("found", []))
    except Exception as exc:
        print(f"DEBUG: Lower-surface egress search failed: {exc}")
        return None

    watchdog = MovementWatchdog(last_position=origin)
    candidates = list(
        nearest_lower_surface_candidates(
            {"found": found},
            origin,
            maximum_distance=float(search_radius),
            limit=attempt_limit,
        )
    )

    def attempt(candidate_limit: int, timeout: float):
        for x, y, z in candidates[:candidate_limit]:
            print(f"DEBUG: Trying local lower-surface egress via ({x}, {y}, {z})")
            if not goto(
                client,
                x,
                y,
                z,
                timeout=timeout,
                tolerance=2.0,
            ):
                continue
            try:
                state = client.transport.dispatch("get_state", {})
            except Exception:
                continue
            watchdog.observe(state)
            current = block_position(state)
            if watchdog.stationary_checks == 0 and current[1] <= origin[1] - 2:
                print(
                    "DEBUG: Reached lower connected terrain "
                    f"at ({current[0]}, {current[1]}, {current[2]})"
                )
                return current
        return None

    reached = attempt(min(3, attempt_limit), timeout_per_candidate)
    if reached is not None:
        return reached

    # Prefer a supported one-block-at-a-time descent when any full block is
    # already carried. This conserves material and avoids dropping shelf items
    # into the void.
    shelf_state = client.transport.dispatch("get_state", {})
    descended = supported_column_descent(client, shelf_state)
    if descended is not None:
        print(f"DEBUG: Supported shelf descent reached {descended}")
        return descended

    # With no usable support, make one bounded attempt to recover throwaway
    # blocks from behind the bot and retry the nearby targets.
    if harvest_shelf_dirt(client, shelf_state, target_count=10) >= 6:
        client.transport.dispatch("chat", {"message": "#set allowPlace true"})
        refreshed = client.transport.dispatch("get_state", {})
        watchdog.reset(refreshed)
        reached = attempt(attempt_limit, max(timeout_per_candidate, 30.0))
        if reached is not None:
            return reached

    # Everything above needs either a walkable route or blocks to build with.
    # A bot stranded on a self-built pillar with an empty inventory has
    # neither, so without this it stays there forever.
    latest_state = client.transport.dispatch("get_state", {})
    dropped = try_survivable_drop(client, latest_state)
    if dropped is not None or not allow_upward_excavation:
        return dropped

    from .build_site_recovery import excavate_surface_egress

    print("DEBUG: Lower routes failed; excavating a bounded upward egress")
    excavated = excavate_surface_egress(
        client,
        origin=block_position(latest_state),
        expected_y=max(63, origin[1] + 6),
        timeout_per_attempt=30.0,
    )
    if excavated is not None:
        return excavated

    from .stone_descent import manual_column_descend

    print(
        "DEBUG: Upward routes failed; trying a safe hand-dug column descent"
    )
    if manual_column_descend(
        client,
        target_y=origin[1] - 6,
        max_steps=6,
        require_pickaxe=False,
    ):
        return block_position(client.transport.dispatch("get_state", {}))
    return None
