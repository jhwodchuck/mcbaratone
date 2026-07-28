"""Stone-gathering recovery for high or isolated surface positions."""

from __future__ import annotations

import time
from typing import Any, Optional

from .movement_recovery import block_position
from .surface_egress import try_lower_surface_egress


_UNSAFE_BLOCKS = ("lava", "water")
_OPEN_BLOCKS = ("air",)
_MAX_SAFE_FALL_BLOCKS = 6
_CARDINAL_OFFSETS = ((1, 0), (-1, 0), (0, 1), (0, -1))


def read_block_optional(client: Any, x: int, y: int, z: int) -> Optional[str]:
    """Best-effort single block-id read."""
    try:
        return client.transport.dispatch(
            "get_block", {"x": x, "y": y, "z": z}
        ).get("id")
    except Exception:
        return None


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


def manual_column_descend(
    client: Any,
    *,
    target_y: int,
    max_steps: int = 40,
) -> bool:
    """Break an underfoot column only when a bounded landing is proven."""
    from . import resources as api

    state = api._read_state_optional(
        client, retries=3, label="Manual descend state"
    )
    if state is None or not api._ensure_mining_pickaxe(client):
        return False
    px, py, pz = block_position(state)
    start_y = py

    for _ in range(max_steps):
        if py <= target_y:
            break
        below = str(read_block_optional(client, px, py - 1, pz) or "")
        if not below or any(name in below for name in _OPEN_BLOCKS):
            print(f"DEBUG: Manual descend: no floor at Y={py - 1}; stopping")
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
        if not any(
            api.select_item(client, item_id, allow_swap=True)
            for item_id in reversed(api.PICKAXE_ITEMS)
        ):
            break

        client.transport.dispatch(
            "dig_block",
            {"x": px, "y": py - 1, "z": pz, "face": "UP", "max_ticks": 160},
        )
        deadline = time.time() + 8
        while time.time() < deadline:
            time.sleep(0.3)
            block = str(read_block_optional(client, px, py - 1, pz) or "")
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
            print("DEBUG: Manual descend: player did not drop after breaking; stopping")
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
    egress = try_lower_surface_egress(client, state)
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
            if distance_sq >= 12**2 and destination_safe(client, *candidate):
                candidates.append((distance_sq, candidate))
        for _distance, candidate in sorted(candidates)[:attempt_limit]:
            print(f"DEBUG: Relocating stone gatherer to dry terrain at {candidate}")
            if not api.goto(client, *candidate, timeout=90.0, tolerance=2.0):
                continue
            current = block_position(client.transport.dispatch("get_state", {}))
            moved_sq = (
                (current[0] - origin[0]) ** 2
                + (current[2] - origin[2]) ** 2
            )
            if moved_sq >= 8**2 and destination_safe(client, *current):
                return True
        return False
    except api.PlayerDeathDetected:
        raise
    except Exception as exc:
        print(f"DEBUG: Dry-terrain stone relocation failed: {exc}")
        return False
