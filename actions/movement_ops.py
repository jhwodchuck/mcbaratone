"""
Movement operations and utilities for Minecraft automation tests.

This module contains utility functions for movement and positioning operations.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tests.utils.mc_harness.context import TestContext

def _find_place_pos_near(ctx: TestContext, x: int, y: int, z: int, radius: int = 4, avoid=None) -> tuple[int, int, int]:
    """Find a suitable block placement position near coordinates."""
    if avoid is None:
        px, py, pz = ctx.get_position()
        avoid = {(int(px), int(py), int(pz))}
    else:
        avoid = set(avoid)
    offsets = []
    for dy in (0, 1, -1):
        for dx in range(-radius, radius + 1):
            for dz in range(-radius, radius + 1):
                offsets.append((dx, dy, dz))
    offsets.sort(key=lambda o: (o[0] * o[0] + o[1] * o[1] + o[2] * o[2], abs(o[1])))
    for dx, dy, dz in offsets:
        px, py, pz = int(x + dx), int(y + dy), int(z + dz)
        if (px, py, pz) in avoid:
            continue
        block_at = _block_id_at(ctx, px, py, pz)
        block_below = _block_id_at(ctx, px, py - 1, pz)
        if _is_liquid(block_at) or _is_liquid(block_below):
            continue
        if "air" not in block_at:
            continue
        if "air" in block_below:
            continue
        return (px, py, pz)
    return (int(x), int(y), int(z))

# These need to be defined or imported
def _block_id_at(ctx, x, y, z):
    """Get block ID at coordinates."""
    block = ctx.get_block(x, y, z)
    data = block.get("data", block)
    return data.get("id", "")

def _is_liquid(block_id: str):
    """Check if block is a liquid."""
    return "water" in block_id or "lava" in block_id or "bubble_column" in block_id