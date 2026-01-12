"""
Unified Movement Operations Module

This module contains consolidated functions for handling player movement and position-related operations
in Minecraft tests. Movement operations primarily delegate to block operations for position finding
and validation.

All functions include proper type hints and comprehensive docstrings for clarity and maintainability.
"""

from typing import Optional, Set, Tuple, Union

from tests.functional.shared.block_ops import (
    block_id_at,
    find_place_pos_near,
    find_stand_pos,
    in_range,
    is_liquid,
    move_near,
)


def find_position_near(ctx, x: float, y: float, z: float, radius: int = 4,
                       avoid: Optional[Set[Tuple[int, int, int]]] = None) -> Tuple[int, int, int]:
    """
    Find a suitable standing position near coordinates.

    This function finds a safe position to stand near the given coordinates,
    avoiding liquids and ensuring solid ground. It serves as an alias to
    find_place_pos_near for backward compatibility in movement operations.

    Args:
        ctx: Test context containing client and position information.
        x: Target X coordinate as a float.
        y: Target Y coordinate as a float.
        z: Target Z coordinate as a float.
        radius: Search radius around the target position (default: 4).
        avoid: Optional set of (x, y, z) positions to avoid during search.

    Returns:
        Tuple of (x, y, z) integer coordinates for a suitable standing position.

    Note:
        This is functionally equivalent to find_place_pos_near but maintains
        the original naming for compatibility.
    """
    return find_place_pos_near(ctx, x, y, z, radius, avoid)