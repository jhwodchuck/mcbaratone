"""Stone-gathering recovery for high or isolated surface positions."""

from __future__ import annotations

import time
from typing import Any, Callable, Collection, Optional

from .movement_recovery import block_position
from .navigation import goto
from .surface_egress import try_lower_surface_egress


_UNSAFE_BLOCKS = ("lava", "water")
_OPEN_BLOCKS = ("air",)
_INSET_SUPPORT_BLOCKS = ("mud",)
_MAX_SAFE_FALL_BLOCKS = 6
_CARDINAL_OFFSETS = ((1, 0), (-1, 0), (0, 1), (0, -1))
_WATER_SEAL_BLOCKS = (
    "minecraft:cobblestone",
    "minecraft:cobbled_deepslate",
    "minecraft:stone",
    "minecraft:dirt",
)


def read_block_optional(
    client: Any,
    x: int,
    y: int,
    z: int,
    *,
    retries: int = 5,
) -> Optional[str]:
    """Read a block ID through bounded transient bridge failures."""
    for attempt in range(max(1, retries)):
        try:
            block_id = client.transport.dispatch(
                "get_block", {"x": x, "y": y, "z": z}
            ).get("id")
            if block_id:
                return block_id
        except Exception:
            pass
        if attempt + 1 < retries:
            time.sleep(0.5)
    print(
        "DEBUG: Block probe unavailable at "
        f"({x}, {y}, {z}) after {max(1, retries)} attempts"
    )
    return None


def read_block_snapshot_optional(
    client: Any,
    x: int,
    y: int,
    z: int,
    *,
    retries: int = 5,
) -> Optional[dict[str, Any]]:
    """Read a block ID and state through bounded transient failures."""
    for attempt in range(max(1, retries)):
        try:
            response = client.transport.dispatch(
                "get_block", {"x": x, "y": y, "z": z}
            )
            data = response.get("data", response)
            if isinstance(data, dict) and data.get("id"):
                return data
        except Exception:
            pass
        if attempt + 1 < retries:
            time.sleep(0.5)
    print(
        "DEBUG: Block snapshot unavailable at "
        f"({x}, {y}, {z}) after {max(1, retries)} attempts"
    )
    return None


def drain_breathable_water_pocket(client: Any, state: dict) -> Optional[bool]:
    """Seal and bucket a source block occupying the player's feet.

    Deep staircases can intersect a one-block aquifer while leaving the
    player's head in air. Baritone will not mine a route out of that source
    cell, and an exact break immediately refills from an adjacent source. Seal
    cardinal inflows first, then collect the occupied source with an empty
    bucket. ``None`` means this recovery does not apply; ``False`` means the
    pocket was recognized but could not be drained safely.
    """
    from . import resources as api
    from .inventory import count_item, select_item

    px, py, pz = block_position(state)
    current = read_block_snapshot_optional(client, px, py, pz, retries=2)
    head = read_block_snapshot_optional(client, px, py + 1, pz, retries=2)
    if current is None or head is None:
        return None
    if "water" not in str(current.get("id", "")):
        return None
    if "air" not in str(head.get("id", "")):
        print("Y navigation: water pocket reaches the player's head; refusing drain")
        return False

    level = str((current.get("state") or {}).get("level", "0"))
    if level != "0":
        print(
            "Y navigation: occupied water is flowing, not a collectible source; "
            "refusing an unverified bucket action"
        )
        return False
    if count_item(client, "minecraft:bucket") < 1:
        print("Y navigation: source-water pocket found but no empty bucket is carried")
        return False

    water_neighbors: list[tuple[int, int]] = []
    for dx, dz in _CARDINAL_OFFSETS:
        nx, nz = px + dx, pz + dz
        adjacent = read_block_snapshot_optional(client, nx, py, nz, retries=2)
        if adjacent is None:
            return False
        if "water" in str(adjacent.get("id", "")):
            water_neighbors.append((nx, nz))

    filler = next(
        (
            item_id
            for item_id in _WATER_SEAL_BLOCKS
            if count_item(client, item_id) >= len(water_neighbors)
        ),
        None,
    )
    if water_neighbors and filler is None:
        print(
            "Y navigation: source-water pocket found but not enough sealing "
            "blocks are carried"
        )
        return False

    for nx, nz in water_neighbors:
        assert filler is not None
        if not select_item(client, filler, allow_swap=True):
            return False
        response = client.transport.dispatch(
            "place_block",
            {"x": nx, "y": py, "z": nz, "block": filler},
        )
        data = response.get("data", response)
        if response.get("error") or not bool(data.get("placed", False)):
            print(
                "Y navigation: could not seal adjacent water at "
                f"({nx}, {py}, {nz})"
            )
            return False
        for _ in range(8):
            sealed = read_block_optional(client, nx, py, nz, retries=1)
            if sealed == filler:
                break
            time.sleep(0.1)
        else:
            print(
                "Y navigation: water seal acknowledgement lacked a world "
                f"postcondition at ({nx}, {py}, {nz})"
            )
            return False

    water_buckets_before = count_item(client, "minecraft:water_bucket")
    if not select_item(client, "minecraft:bucket", allow_swap=True):
        return False
    client.transport.dispatch(
        "look_at",
        {"x": px + 0.5, "y": py + 0.25, "z": pz + 0.5},
    )
    time.sleep(0.2)
    client.transport.dispatch("use_item", {"duration_ms": 0})
    for _ in range(12):
        after = read_block_optional(client, px, py, pz, retries=1)
        if (
            after is not None
            and "water" not in after
            and count_item(client, "minecraft:water_bucket")
            > water_buckets_before
        ):
            api.equip_best_pickaxe(client)
            print(
                "Y navigation: sealed adjacent inflow and drained the "
                "breathable source-water pocket"
            )
            return True
        time.sleep(0.1)

    api.equip_best_pickaxe(client)
    print("Y navigation: bucket action did not drain the occupied water source")
    return False


def mine_step_block(
    client: Any,
    x: int,
    y: int,
    z: int,
    *,
    read_block: Callable[[int, int, int], str],
    air_blocks: Collection[str],
    unsafe_blocks: Collection[str],
    baritone_fallback_blocks: Collection[str],
) -> bool:
    """Mine one verified staircase cell with the best available primitive."""
    from . import resources as api
    from ..core.exceptions import CommandError, TransportError

    current = read_block(x, y, z)
    if current in air_blocks:
        return True
    if current in unsafe_blocks or not api._ensure_mining_pickaxe(client):
        return False

    # Bridge 1.0.27+ exposes a tick-driven exact mining primitive that works
    # underfoot and on deepslate. The older Baritone builder can acknowledge a
    # break without changing the block, which caused A1's Y=4 deadlock.
    try:
        response = api._serialized_dispatch(
            client,
            "dig_block",
            {"x": x, "y": y, "z": z, "face": "UP", "max_ticks": 240},
            post_delay_seconds=api._BARITONE_MINE_SETUP_SECONDS,
        )
    except (CommandError, TransportError):
        response = {}

    dig_acknowledged = (
        response.get("started")
        and not response.get("error")
        and response.get("x") == x
        and response.get("y") == y
        and response.get("z") == z
    )
    if dig_acknowledged:
        deadline = time.monotonic() + 12.0
        while time.monotonic() < deadline:
            if read_block(x, y, z) in air_blocks:
                api._serialized_dispatch(
                    client,
                    "cancel",
                    {},
                    post_delay_seconds=api._BARITONE_CANCEL_GRACE_SECONDS,
                )
                return True
            state, _ = api._read_state_with_retry(
                client,
                retries=2,
                label="Y navigation wait for progressive block break",
            )
            if state is None:
                time.sleep(0.25)
                continue
            if state.get("is_dead", False) or float(
                state.get("health", 20) or 0
            ) <= 0:
                return False
            time.sleep(0.25)
        api._serialized_dispatch(
            client,
            "cancel",
            {},
            post_delay_seconds=api._BARITONE_CANCEL_GRACE_SECONDS,
        )

    # Compatibility fallback for older bridges. Dense deepslate is delegated
    # to the guarded one-block Baritone goal because the legacy builder is
    # known to false-acknowledge it.
    if current in baritone_fallback_blocks:
        return False
    response = api._serialized_dispatch(
        client,
        "break_block",
        {"x": x, "y": y, "z": z},
        post_delay_seconds=api._BARITONE_MINE_SETUP_SECONDS,
    )
    if response.get("error"):
        return False
    deadline = time.monotonic() + 12.0
    while time.monotonic() < deadline:
        if read_block(x, y, z) in air_blocks:
            api._serialized_dispatch(
                client,
                "cancel",
                {},
                post_delay_seconds=api._BARITONE_BUILD_CLEANUP_SECONDS,
            )
            return True
        time.sleep(0.25)
    api._serialized_dispatch(
        client,
        "cancel",
        {},
        post_delay_seconds=api._BARITONE_BUILD_CLEANUP_SECONDS,
    )
    return False


def choose_descent_offset(
    client: Any,
    px: int,
    py: int,
    pz: int,
    *,
    probe_distance: int = 3,
) -> Optional[tuple[int, int]]:
    """Find an open cardinal direction for a lateral staircase."""
    for dx, dz in _CARDINAL_OFFSETS:
        probe = read_block_optional(
            client,
            px + dx * probe_distance,
            py,
            pz + dz * probe_distance,
        )
        if probe and "air" in probe:
            return (dx, dz)
    return None


def _landing_below(
    client: Any, x: int, y: int, z: int
) -> tuple[Optional[str], Optional[int]]:
    """Return the first bounded landing below an underfoot block."""
    for gap in range(2, 2 + _MAX_SAFE_FALL_BLOCKS):
        block = str(read_block_optional(client, x, y - gap, z) or "")
        if any(name in block for name in _UNSAFE_BLOCKS):
            return block, gap
        if block and not any(name in block for name in _OPEN_BLOCKS):
            return block, gap
    return None, None


def _navigate_to_verified_landing(
    client: Any,
    api: Any,
    *,
    x: int,
    y: int,
    z: int,
    gap: int,
) -> Optional[tuple[int, int, int]]:
    """Enter a previously verified shaft and confirm downward displacement."""
    landing_y = y - gap + 1
    if not goto(
        client,
        x,
        landing_y,
        z,
        timeout=12.0,
        tolerance=1.0,
    ):
        return None
    after = api._read_state_optional(
        client,
        retries=1,
        label="Manual descend landing",
    )
    if after is None:
        return None
    ax, ay, az = block_position(after)
    if ay > y - 1:
        return None
    return ax, ay, az


def manual_column_descend(
    client: Any,
    *,
    target_y: int,
    max_steps: int = 40,
    require_pickaxe: bool = True,
) -> bool:
    """Break an underfoot column only when a bounded landing is proven."""
    from . import resources as api

    state = api._read_state_optional(
        client, retries=3, label="Manual descend state"
    )
    if state is None or (
        require_pickaxe and not api._ensure_mining_pickaxe(client)
    ):
        return False
    px, py, pz = block_position(state)
    start_y = py

    for _ in range(max_steps):
        if py <= target_y:
            break
        floor_y = py - 1
        below = str(read_block_optional(client, px, floor_y, pz) or "")
        if not below or any(name in below for name in _OPEN_BLOCKS):
            inset_support = ""
            if not require_pickaxe:
                current_block = str(
                    read_block_optional(client, px, py, pz) or ""
                )
                if any(
                    name in current_block for name in _INSET_SUPPORT_BLOCKS
                ):
                    inset_support = current_block
            if inset_support:
                below = inset_support
                floor_y = py
            elif not require_pickaxe:
                landing, gap = _landing_below(client, px, py, pz)
                if landing and gap and not any(
                    name in landing for name in _UNSAFE_BLOCKS
                ):
                    descended = _navigate_to_verified_landing(
                        client,
                        api,
                        x=px,
                        y=py,
                        z=pz,
                        gap=gap,
                    )
                    if descended is not None:
                        px, py, pz = descended
                        continue
                print(
                    f"DEBUG: Manual descend: no floor at Y={py - 1}; stopping"
                )
                break
            else:
                print(
                    f"DEBUG: Manual descend: no floor at Y={py - 1}; stopping"
                )
                break
        if any(name in below for name in _UNSAFE_BLOCKS):
            print(
                f"DEBUG: Manual descend: unsafe block ({below}) "
                f"at Y={py - 1}; stopping"
            )
            break

        landing, gap = _landing_below(client, px, py, pz)
        if landing is None:
            print(
                "DEBUG: Manual descend: no solid landing within "
                f"{_MAX_SAFE_FALL_BLOCKS} blocks below Y={py - 1}; "
                "refusing an unsafe fall. Stopping."
            )
            break
        if any(name in landing for name in _UNSAFE_BLOCKS):
            print(
                f"DEBUG: Manual descend: unsafe landing ({landing}) "
                f"at Y={py - int(gap or 0)}; stopping"
            )
            break
        if require_pickaxe and not any(
            api.select_item(client, item_id, allow_swap=True)
            for item_id in reversed(api.PICKAXE_ITEMS)
        ):
            break

        client.transport.dispatch(
            "dig_block",
            {"x": px, "y": floor_y, "z": pz, "face": "UP", "max_ticks": 160},
        )
        deadline = time.time() + 8
        while time.time() < deadline:
            time.sleep(0.3)
            block = str(read_block_optional(client, px, floor_y, pz) or "")
            if not block or "air" in block:
                break
        else:
            print("DEBUG: Manual descend: dig_block did not verify a break; stopping")
            break

        previous_y = py
        settle_deadline = time.time() + 5
        while time.time() < settle_deadline:
            time.sleep(0.5)
            after = api._read_state_optional(
                client, retries=1, label="Manual descend settle"
            )
            if after is None:
                continue
            ax, ay, az = block_position(after)
            if ay <= previous_y - 1:
                px, py, pz = ax, ay, az
                break
        else:
            print(
                "DEBUG: Manual descend: player did not drop after breaking; "
                "navigating to verified landing"
            )
            descended = _navigate_to_verified_landing(
                client,
                api,
                x=px,
                y=previous_y,
                z=pz,
                gap=int(gap or 0),
            )
            if descended is not None:
                px, py, pz = descended
                continue
            print("DEBUG: Manual descend: verified landing remained unreachable")
            break

    api._serialized_dispatch(client, "cancel", {}, post_delay_seconds=0.0)
    return py <= start_y - 1


def ensure_tunnel_lighting(client: Any, state: dict, last_torch_pos):
    """Place a torch after ten underground blocks of movement."""
    from .base import place_torch

    position = state.get("block_position", state.get("position", {}))
    current = (
        float(position.get("x", 0)),
        float(position.get("y", 0)),
        float(position.get("z", 0)),
    )
    if current[1] >= 60:
        return False, last_torch_pos
    if not last_torch_pos:
        return False, current
    distance = sum(
        (axis - previous) ** 2
        for axis, previous in zip(current, last_torch_pos)
    ) ** 0.5
    if distance <= 10:
        return False, last_torch_pos
    client.transport.dispatch("cancel", {})
    time.sleep(0.5)
    place_torch(client, *(int(axis) for axis in current))
    return True, current


def descend_to_stone_layer(
    client: Any,
    *,
    target_depth: int = 25,
    floor_y: int = 50,
    timeout: int = 180,
    lateral_reach: int = 8,
) -> bool:
    """Reach stone using local surface egress, tunnel, then safe column dig."""
    from . import resources as api

    state = api._read_state_optional(
        client, retries=3, label="Descend-to-stone state"
    )
    if state is None:
        return False
    px, py, pz = block_position(state)
    # Reaching this helper means broad mining and exact nearby targets have
    # already proven the bot is stranded. Do not retain the generic y>=96
    # shelf gate for low terrain: mangrove mud shelves can have the same
    # unsupported underfoot geometry as a mountain ledge. High-altitude
    # callers retain the normal shelf behavior.
    egress_kwargs = {"minimum_altitude": 0} if py < 96 else {}
    egress = try_lower_surface_egress(client, state, **egress_kwargs)
    if egress is not None:
        px, py, pz = egress

    target_y = max(int(floor_y), py - int(target_depth))
    if target_y >= py - 2:
        return False
    offset = api._choose_descent_offset(client, px, py, pz)
    if offset is None:
        target_x, target_z = px, pz
    else:
        target_x = px + offset[0] * lateral_reach
        target_z = pz + offset[1] * lateral_reach
    print(
        f"DEBUG: Descending to stone layer from Y={py} to Y={target_y} "
        f"at ({target_x}, {target_z})"
        + (" via lateral offset off narrow pillar" if offset else "")
    )
    client.transport.dispatch("cancel", {})
    if not api._ensure_mining_pickaxe(client):
        return False
    tunnel = {"x": target_x, "y": target_y, "z": target_z, "radius": 2}
    client.transport.dispatch("tunnel", tunnel)

    start = time.time()
    checks = 0
    last_torch_pos = None
    while time.time() - start < timeout:
        time.sleep(3)
        checks += 1
        after = api._read_state_optional(
            client, retries=1, label="Descent progress"
        )
        if after is None:
            continue
        placed, last_torch_pos = api.ensure_tunnel_lighting(
            client, after, last_torch_pos
        )
        if placed:
            client.transport.dispatch("tunnel", tunnel)
        _, ay, _ = block_position(after)
        if ay <= target_y + 3 or ay <= py - 10:
            print(f"DEBUG: Reached stone-mining depth at Y={ay}")
            return True
        if checks >= 2 and not after.get("is_pathing", True) and ay >= py - 1:
            print(
                "DEBUG: Descent tunnel has no walkable path "
                "(is_pathing=False, no floor within the target's reach)"
            )
            break

    print("DEBUG: Descent tunnel made no downward progress; trying manual column descend")
    client.transport.dispatch("cancel", {})
    if api._manual_column_descend(client, target_y=target_y):
        return True
    print("DEBUG: Manual column descend also made no progress")
    return False


def relocate_to_checkpointed_stone_source(
    client: Any, *, minimum_y: int = 96
) -> bool:
    """Return a stranded gatherer to its verified storage/base terrain."""
    from . import resources as api
    from .inventory import resolve_storage_location

    try:
        state = client.transport.dispatch("get_state", {})
        current = block_position(state)
        if current[1] < minimum_y:
            return False
        automation_state = getattr(client, "_automation_state", None)
        if automation_state is None:
            return False
        target = resolve_storage_location(
            client, state=automation_state, verify=False
        )
        if target is None:
            value = automation_state.custom_data.get("base_location")
            if isinstance(value, (list, tuple)) and len(value) == 3:
                target = tuple(int(axis) for axis in value)
        if target is None:
            return False
        print(
            f"DEBUG: Stone gathering stranded at Y={current[1]}; "
            f"returning to checkpointed terrain at {target}"
        )
        client.transport.dispatch("cancel", {})
        return api.goto(
            client,
            *target,
            timeout=240,
            tolerance=3.0,
        )
    except api.PlayerDeathDetected:
        raise
    except Exception as exc:
        print(f"DEBUG: Checkpointed stone relocation failed: {exc}")
        return False


def relocate_to_dry_stone_terrain(
    client: Any,
    *,
    search_radius: int = 48,
    attempt_limit: int = 6,
) -> bool:
    """Move a water-stranded gatherer to verified dry natural terrain."""
    from . import resources as api
    from .escape_recovery import destination_safe

    try:
        state = client.transport.dispatch("get_state", {})
        origin = block_position(state)
        response = client.transport.dispatch(
            "find_blocks",
            {
                "blocks": [
                    "minecraft:grass_block",
                    "minecraft:dirt",
                    "minecraft:coarse_dirt",
                    "minecraft:podzol",
                    "minecraft:stone",
                ],
                "radius": int(search_radius),
                "limit": 4096,
            },
        )
        candidates = []
        for block in response.get("found", []):
            candidate = (
                int(block["x"]),
                int(block["y"]) + 1,
                int(block["z"]),
            )
            distance_sq = (
                (candidate[0] - origin[0]) ** 2
                + (candidate[2] - origin[2]) ** 2
            )
            if distance_sq >= 12**2:
                candidates.append((distance_sq, candidate))
        # ``find_blocks`` can return thousands of dirt/grass positions. A
        # destination safety query for every result blocked the controller
        # for minutes in a mangrove swamp. Sort cheaply first, then bound the
        # expensive world probes while still allowing several unsafe nearby
        # candidates to be skipped.
        safe_attempts = 0
        probe_limit = max(int(attempt_limit), int(attempt_limit) * 4)
        for _distance, candidate in sorted(candidates)[:probe_limit]:
            if not destination_safe(client, *candidate):
                continue
            safe_attempts += 1
            print(f"DEBUG: Relocating stone gatherer to dry terrain at {candidate}")
            if not api.goto(client, *candidate, timeout=90.0, tolerance=2.0):
                if safe_attempts >= attempt_limit:
                    break
                continue
            current = block_position(client.transport.dispatch("get_state", {}))
            moved_sq = (
                (current[0] - origin[0]) ** 2
                + (current[2] - origin[2]) ** 2
            )
            if moved_sq >= 8**2 and destination_safe(client, *current):
                return True
            if safe_attempts >= attempt_limit:
                break
        return False
    except api.PlayerDeathDetected:
        raise
    except Exception as exc:
        print(f"DEBUG: Dry-terrain stone relocation failed: {exc}")
        return False
