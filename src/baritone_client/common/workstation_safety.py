"""Small terrain adaptations for survival crafting workstations."""

from __future__ import annotations

from typing import Optional, Tuple

from . import harness_ops
from .inventory import count_item
from .navigation import goto


def _block_id(client, x: int, y: int, z: int) -> str:
    """Read one block id, treating bridge failures as unusable terrain."""
    try:
        return str(
            client.transport.dispatch(
                "get_block",
                {"x": x, "y": y, "z": z},
            ).get("id", "")
        )
    except Exception:
        return ""


def _is_air(block_id: str) -> bool:
    return bool(block_id) and "air" in block_id


def place_ledge_supported_workstation(
    client,
) -> Optional[Tuple[int, int, int]]:
    """Extend a narrow ledge by one support block and place a table on it."""
    state = client.transport.dispatch("get_state", {})
    position = state.get("block_position", state.get("position", {}))
    if not all(axis in position for axis in ("x", "y", "z")):
        return None
    x, y, z = (int(position[axis]) for axis in ("x", "y", "z"))

    floor = _block_id(client, x, y - 1, z)
    if _is_air(floor) or any(liquid in floor for liquid in ("water", "lava")):
        return None
    support = next(
        (
            item_id
            for item_id in ("minecraft:dirt", "minecraft:cobblestone")
            if count_item(client, item_id) >= 1
        ),
        None,
    )
    if support is None or count_item(client, "minecraft:crafting_table") < 1:
        return None

    for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        support_pos = (x + dx, y - 1, z + dz)
        table_pos = (x + dx, y, z + dz)
        if not _is_air(_block_id(client, *support_pos)):
            continue
        if not _is_air(_block_id(client, *table_pos)):
            continue
        if not harness_ops.place_block_exact(
            client,
            *support_pos,
            support,
            allow_break=False,
        ):
            continue
        if harness_ops.place_block_exact(
            client,
            *table_pos,
            "minecraft:crafting_table",
            allow_break=False,
        ):
            return table_pos
    return None


def move_to_supported_descent_column(
    client,
    x: int,
    y: int,
    z: int,
    *,
    max_landing_gap: int = 6,
) -> Optional[Tuple[int, int, int]]:
    """Step from a temporary ledge onto an adjacent safely-backed column."""
    for target_y in (y, y - 1):
        for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            target = (x + dx, target_y, z + dz)
            if not _is_air(_block_id(client, *target)):
                continue
            if not _is_air(
                _block_id(client, target[0], target_y + 1, target[2])
            ):
                continue
            floor = _block_id(client, target[0], target_y - 1, target[2])
            if _is_air(floor) or any(
                token in floor for token in ("water", "lava")
            ):
                continue

            landing_found = False
            for gap in range(2, 2 + max_landing_gap):
                block = _block_id(
                    client,
                    target[0],
                    target_y - gap,
                    target[2],
                )
                if not block or any(
                    token in block for token in ("water", "lava")
                ):
                    break
                if not _is_air(block):
                    landing_found = True
                    break
            if landing_found and goto(
                client,
                *target,
                timeout=20,
                check_interval=0.25,
                tolerance=0.35,
            ):
                return target
    return None


def place_liquid_supported_workstation(
    client,
) -> Optional[Tuple[int, int, int]]:
    """Build a two-block pad when a player is stranded in shallow liquid."""
    state = client.transport.dispatch("get_state", {})
    position = state.get("block_position", state.get("position", {}))
    if not all(axis in position for axis in ("x", "y", "z")):
        return None
    x, y, z = (int(position[axis]) for axis in ("x", "y", "z"))
    feet = client.transport.dispatch(
        "get_block",
        {"x": x, "y": y, "z": z},
    ).get("id", "")
    if not any(liquid in str(feet) for liquid in ("water", "lava")):
        return None

    support = next(
        (
            item_id
            for item_id in ("minecraft:cobblestone", "minecraft:dirt")
            if count_item(client, item_id) >= 2
        ),
        None,
    )
    if support is None or count_item(client, "minecraft:crafting_table") < 1:
        return None

    for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        stand = (x + dx, y, z + dz)
        table_support = (x + 2 * dx, y, z + 2 * dz)
        table = (table_support[0], y + 1, table_support[2])
        if not harness_ops.place_block_exact(
            client,
            *stand,
            support,
            allow_break=False,
        ):
            continue
        if not goto(
            client,
            stand[0],
            stand[1] + 1,
            stand[2],
            timeout=20,
            check_interval=0.25,
            tolerance=0.5,
        ):
            continue
        if not harness_ops.place_block_exact(
            client,
            *table_support,
            support,
            allow_break=False,
        ):
            continue
        if harness_ops.place_block_exact(
            client,
            *table,
            "minecraft:crafting_table",
            allow_break=False,
        ):
            return table
    return None
