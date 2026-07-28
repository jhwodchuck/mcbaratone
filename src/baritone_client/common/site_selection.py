"""Surface discovery and dry, flat build-site selection."""

import math
from typing import Dict, Iterable, Optional, Tuple


_NATURAL_GROUND = {
    "minecraft:grass_block",
    "minecraft:dirt",
    "minecraft:coarse_dirt",
    "minecraft:rooted_dirt",
    "minecraft:podzol",
    "minecraft:mycelium",
    "minecraft:stone",
    "minecraft:deepslate",
    "minecraft:sand",
    "minecraft:red_sand",
    "minecraft:gravel",
    "minecraft:clay",
    "minecraft:snow_block",
    "minecraft:mud",
    "minecraft:mud_bricks",
}


def surface_y_at(
    client,
    x: int,
    z: int,
    top: int = 120,
    bottom: int = 40,
) -> Optional[int]:
    """Scan down from the sky for the first solid block to stand on."""
    try:
        for y in range(top, bottom, -1):
            block_id = client.transport.dispatch(
                "get_block", {"x": x, "y": y, "z": z}
            ).get("id", "")
            if (
                block_id
                and "air" not in block_id
                and "water" not in block_id
                and "lava" not in block_id
            ):
                if any(token in block_id for token in ("leaves", "log")):
                    continue
                return y + 1
    except Exception:
        pass
    return None


def find_flat_site_in_view(
    voxels: Iterable[Dict],
    center: Tuple[int, int, int],
    radius: int,
    footprint: int,
) -> Optional[Tuple[int, int, int]]:
    """Choose the closest dry, clear footprint from one view snapshot."""
    px, _py, pz = center
    blocks = {
        (int(voxel["x"]), int(voxel["y"]), int(voxel["z"])): voxel.get("id", "")
        for voxel in voxels
        if all(axis in voxel for axis in ("x", "y", "z"))
    }
    ground_by_column = {}
    liquid_by_column = {}
    for (x, y, z), block_id in blocks.items():
        if block_id in _NATURAL_GROUND or block_id.endswith("_terracotta"):
            ground_by_column[(x, z)] = max(y, ground_by_column.get((x, z), -64))
        if "water" in block_id or "lava" in block_id:
            liquid_by_column[(x, z)] = max(
                y, liquid_by_column.get((x, z), -64)
            )

    max_origin_x = px + radius - footprint + 1
    max_origin_z = pz + radius - footprint + 1
    candidates = []
    for origin_x in range(px - radius, max_origin_x + 1):
        for origin_z in range(pz - radius, max_origin_z + 1):
            columns = [
                (origin_x + dx, origin_z + dz)
                for dx in range(footprint)
                for dz in range(footprint)
            ]
            if any(column not in ground_by_column for column in columns):
                continue
            standing_heights = [
                ground_by_column[column] + 1 for column in columns
            ]
            spread = max(standing_heights) - min(standing_heights)
            if spread > 1:
                continue
            build_y = max(standing_heights)
            if any(
                liquid_by_column.get(column, -64) >= build_y - 1
                for column in columns
            ):
                continue

            obstruction_count = 0
            hard_obstruction = False
            for column_x, column_z in columns:
                for column_y in range(build_y + 1, build_y + 5):
                    block_id = blocks.get(
                        (column_x, column_y, column_z), ""
                    )
                    if not block_id:
                        continue
                    if "_log" in block_id or "_leaves" in block_id:
                        obstruction_count += 1
                    elif not any(
                        token in block_id
                        for token in ("grass", "flower", "fern", "snow", "vine")
                    ):
                        hard_obstruction = True
                        break
                if hard_obstruction:
                    break
            if hard_obstruction:
                continue

            center_x = origin_x + (footprint - 1) / 2
            center_z = origin_z + (footprint - 1) / 2
            distance = math.hypot(center_x - px, center_z - pz)
            candidates.append(
                (
                    spread,
                    obstruction_count,
                    distance,
                    origin_x,
                    build_y,
                    origin_z,
                )
            )

    if not candidates:
        return None
    _spread, _obstructions, _distance, x, y, z = min(candidates)
    return (x, y, z)


def _restore_surface_navigation_policy(client) -> None:
    """Restore travel settings that may survive a stopped controller."""
    for command in (
        "#set allowBreak true",
        "#set allowPlace true",
        "#set allowDownward false",
    ):
        client.transport.dispatch("chat", {"message": command})


def find_flat_ground(
    client,
    radius: int = 20,
    footprint: int = 1,
) -> Optional[Tuple[int, int, int]]:
    """Find a reachable surface location suitable for a base footprint."""
    try:
        state = client.transport.dispatch("get_state", {})
        if state.get("error"):
            return None

        position = state.get("block_position", state.get("position", {}))
        if not position:
            return None
        player_x = int(position.get("x", 0))
        player_y = int(position.get("y", 64))
        player_z = int(position.get("z", 0))

        surface_y = surface_y_at(client, player_x, player_z)
        if surface_y is not None and player_y < surface_y - 3:
            print(
                f"  Underground at y={player_y} (surface ~y={surface_y}). "
                "Pathing to the surface..."
            )
            from .automation_utils import safe_goto

            _restore_surface_navigation_policy(client)
            # Try the player's own column first, then nearby ones. Insisting on
            # this exact column is what stranded Bot09: it stood in shallow
            # water at y=62 under a vine canopy, surface_y_at returned the top
            # of the vines (y=68), and Baritone cannot climb a vine column --
            # so the only offered goal was unreachable and BASE_CONSTRUCTION
            # failed on repeat. A couple of blocks sideways is usually ordinary
            # walkable ground.
            ascended = safe_goto(
                client,
                player_x,
                surface_y,
                player_z,
                timeout=180.0,
            )
            if not ascended:
                for dx, dz in ((4, 0), (-4, 0), (0, 4), (0, -4), (6, 6), (-6, -6)):
                    near_x, near_z = player_x + dx, player_z + dz
                    near_surface = surface_y_at(client, near_x, near_z)
                    if near_surface is None or near_surface < surface_y - 6:
                        continue
                    print(
                        f"  Own column is unclimbable; trying surface at "
                        f"({near_x}, {near_surface}, {near_z})..."
                    )
                    if safe_goto(
                        client,
                        near_x,
                        near_surface,
                        near_z,
                        timeout=90.0,
                    ):
                        ascended = True
                        surface_y = near_surface
                        break
            if not ascended:
                from .surface_recovery import reach_dry_surface

                recovered = reach_dry_surface(
                    client,
                    origin=(player_x, player_y, player_z),
                    expected_y=surface_y,
                    goto=safe_goto,
                )
                if recovered is None:
                    from .build_site_recovery import excavate_surface_egress

                    recovered = excavate_surface_egress(
                        client,
                        origin=(player_x, player_y, player_z),
                        expected_y=surface_y,
                    )
                if recovered is not None:
                    player_x, player_y, player_z = recovered
                    surface_y = player_y
                    ascended = True
            if not ascended:
                print(
                    "  Warning: could not reach the surface; refusing an "
                    "unreachable build site."
                )
                return None

            state = client.transport.dispatch("get_state", {})
            position = state.get("block_position", state.get("position", {}))
            player_x = int(position.get("x", player_x))
            player_y = int(position.get("y", surface_y))
            player_z = int(position.get("z", player_z))
            if player_y < surface_y - 3:
                print(
                    "  Surface path reported arrival without gaining "
                    f"elevation (y={player_y}, expected ~{surface_y}); "
                    "refusing an unreachable build site."
                )
                return None

        if footprint <= 1:
            return (player_x, player_y, player_z)

        view_radius = max(footprint + 2, min(int(radius), 32))
        view = client.transport.dispatch("get_view", {"radius": view_radius})
        site = find_flat_site_in_view(
            view.get("voxels", []),
            (player_x, player_y, player_z),
            view_radius,
            footprint,
        )
        if site is not None:
            print(f"  Found {footprint}x{footprint} building site at {site}")
            return site

        print(
            f"  No dry {footprint}x{footprint} site found within "
            f"{view_radius} blocks"
        )
        return None
    except Exception:
        return None
