"""Conservative, read-only screening before committing to a new homestead.

This is a starter-site screen, not proof of a built home or renewable food.
Unknown terrain is rejected; exploration must not overwrite the old home.
"""

from __future__ import annotations

from typing import Any

from .escape_recovery import _passable, destination_safe

SOIL = ["minecraft:grass_block", "minecraft:dirt", "minecraft:farmland"]
LOGS = [f"minecraft:{wood}_log" for wood in (
    "oak", "birch", "spruce", "acacia", "dark_oak", "jungle", "mangrove", "cherry",
)]


def supported_home_ground(block_id: str) -> bool:
    """Reject unknown, liquid, unsupported and damaging anchor floors."""
    from .terraform_verify import AIR, LIQUIDS, NON_SOLID, REGROWTH, classify_block

    name = classify_block(block_id)
    return name not in AIR | LIQUIDS | NON_SOLID | REGROWTH | {
        "unknown", "powder_snow", "magma_block", "cactus", "campfire",
        "soul_campfire", "pointed_dripstone", "sweet_berry_bush",
    } and not name.endswith(("_leaves", "_sapling"))


def suitable_home_site(client: Any, position: tuple[int, int, int]) -> bool:
    """Require a supported work pad and nearby soil, water and wood.

    A 3x3 pad is only room for initial workstations. Actual house construction
    still needs its own footprint verification. Resource scans are centered on
    the candidate, with vertical bounds so a cave below a mountain is not
    mistaken for nearby surface resources.
    """
    x, y, z = map(int, position)
    try:
        for dx in (-1, 0, 1):
            for dz in (-1, 0, 1):
                column = [client.transport.dispatch("get_block", {
                    "x": x + dx, "y": level, "z": z + dz,
                }).get("id", "") for level in (y - 1, y, y + 1)]
                if not supported_home_ground(column[0]):
                    return False
                if any(not block or block in {"minecraft:void_air", "void_air"}
                       or not _passable(block) or "wither_rose" in block
                       for block in column[1:]):
                    return False
        for blocks, radius in ((SOIL, 20), (["minecraft:water"], 24), (LOGS, 32)):
            result = client.transport.dispatch("find_blocks", {
                "blocks": blocks, "radius": radius, "limit": 128,
                "center": {"x": x, "y": y, "z": z},
            })
            found = False
            for block in result.get("found", []):
                bx, by, bz = (int(block[key]) for key in ("x", "y", "z"))
                if abs(by - y) > 4 or max(abs(bx - x), abs(bz - z)) > radius:
                    continue
                # Soil must have standing room; buried dirt is not farmland.
                if blocks == SOIL and not destination_safe(client, bx, by + 1, bz):
                    continue
                found = True
                break
            if not found:
                return False
    except (AttributeError, KeyError, TypeError, ValueError, RuntimeError, OSError):
        return False
    return True
