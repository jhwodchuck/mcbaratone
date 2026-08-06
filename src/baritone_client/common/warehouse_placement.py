"""Exact, non-destructive placement for planned warehouse chest pairs."""

from __future__ import annotations

from typing import Any, Callable, Sequence, Tuple

from . import harness_ops
from .inventory import count_item


Position = Tuple[int, int, int]


def create_double_chest_at(
    client: Any,
    first: Position,
    second: Position,
    *,
    allowed_existing: Sequence[Position] = (),
    on_placed: Callable[[Position], None] | None = None,
) -> tuple[Position, Position] | None:
    """Place or resume one exact supported horizontal chest pair."""
    if not harness_ops.available():
        return None
    first = tuple(int(value) for value in first)
    second = tuple(int(value) for value in second)
    if (
        len(first) != 3
        or len(second) != 3
        or first[1] != second[1]
        or abs(first[0] - second[0]) + abs(first[2] - second[2]) != 1
    ):
        print("  STORAGE: double-chest coordinates are not horizontally adjacent")
        return None
    block_ids = [harness_ops._block_at(client, *position) for position in (first, second)]
    allowed = {tuple(position) for position in allowed_existing}
    if any(
        "chest" in block_id
        and (block_id != "minecraft:chest" or position not in allowed)
        for position, block_id in zip((first, second), block_ids)
    ):
        print("  STORAGE: planned warehouse coordinates already contain a chest")
        return None
    required = sum(block_id != "minecraft:chest" for block_id in block_ids)
    if count_item(client, "minecraft:chest") < required:
        return None
    for position, block_id in zip((first, second), block_ids):
        if block_id == "minecraft:chest" and position in allowed:
            continue
        if not harness_ops._is_placeable_target(block_id):
            print(f"  STORAGE: planned chest coordinate is obstructed at {position}")
            return None
        support = harness_ops._block_at(
            client, position[0], position[1] - 1, position[2]
        )
        if not harness_ops._is_solid_support_block(support):
            print(f"  STORAGE: planned chest coordinate is unsupported at {position}")
            return None

    if not harness_ops.move_near(client, *first, timeout=20.0):
        return None
    if block_ids[0] != "minecraft:chest":
        if not harness_ops.place_block_exact(
            client, *first, "minecraft:chest", allow_break=False
        ):
            print(f"  STORAGE: failed to place first chest at {first}")
            return None
        if on_placed is not None:
            on_placed(first)

    dx = second[0] - first[0]
    perpendicular = (
        (first[0], first[1], first[2] + 1)
        if dx
        else (first[0] + 1, first[1], first[2])
    )
    harness_ops.move_near(client, *perpendicular, timeout=10.0)
    if block_ids[1] != "minecraft:chest":
        if not harness_ops.place_block_exact(
            client, *second, "minecraft:chest", allow_break=False
        ):
            print(f"  STORAGE: failed to place second chest at {second}")
            return None
        if on_placed is not None:
            on_placed(second)
    print(f"  STORAGE: built double chest at {first}/{second}")
    return first, second


__all__ = ["create_double_chest_at"]
