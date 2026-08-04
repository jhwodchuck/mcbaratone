"""Recover throwaway blocks from a thin shelf without mining underfoot."""

from __future__ import annotations

import time
from collections import deque
from math import floor
from typing import Any, Dict, Iterable, Optional

from .movement_recovery import block_position


_PLACEABLE_ITEMS = (
    "minecraft:dirt",
    "minecraft:cobblestone",
    "minecraft:spruce_planks",
    "minecraft:oak_planks",
    "minecraft:birch_planks",
    "minecraft:spruce_log",
    "minecraft:oak_log",
    "minecraft:birch_log",
)


def carried_dirt(client: Any) -> int:
    """Read carried dirt directly, avoiding stale inventory caches."""
    response = client.transport.dispatch("get_inventory", {})
    data = response.get("data", response)
    return sum(
        int(item.get("count", 0))
        for item in data.get("inventory", [])
        if item.get("id") == "minecraft:dirt"
    )


def _first_placeable_item(client: Any) -> Optional[str]:
    """Return a carried full block suitable for a temporary support."""
    response = client.transport.dispatch("get_inventory", {})
    data = response.get("data", response)
    counts = {
        item.get("id"): int(item.get("count", 0))
        for item in data.get("inventory", [])
    }
    return next((item for item in _PLACEABLE_ITEMS if counts.get(item, 0) > 0), None)


def _supporting_column(
    client: Any, state: Dict, block_y: int
) -> tuple[int, int]:
    """Resolve the solid floor under the player's collision footprint."""
    position = state.get("position", {})
    precise_x = float(position.get("x", block_position(state)[0]))
    precise_z = float(position.get("z", block_position(state)[2]))
    candidates = {
        (floor(precise_x + dx), floor(precise_z + dz))
        for dx in (-0.3, 0.3)
        for dz in (-0.3, 0.3)
    }
    solid = []
    for x, z in candidates:
        block = client.transport.dispatch(
            "get_block", {"x": x, "y": block_y, "z": z}
        ).get("id", "")
        if block and "air" not in str(block):
            distance = (precise_x - (x + 0.5)) ** 2 + (
                precise_z - (z + 0.5)
            ) ** 2
            solid.append((distance, x, z))
    if solid:
        _, x, z = min(solid)
        return x, z
    x, _, z = block_position(state)
    return x, z


def _surface_component(
    found: Iterable[Dict], start: tuple[int, int], floor_y: int
) -> set[tuple[int, int]]:
    layer = {
        (int(item["x"]), int(item["z"]))
        for item in found
        if int(item["y"]) == floor_y
    }
    component = set()
    queue = deque([start])
    while queue:
        position = queue.popleft()
        if position in component or position not in layer:
            continue
        component.add(position)
        x, z = position
        queue.extend(((x + 1, z), (x - 1, z), (x, z + 1), (x, z - 1)))
    return component


def _longest_component_path(
    component: set[tuple[int, int]], start: tuple[int, int]
) -> list[tuple[int, int]]:
    parents: Dict[tuple[int, int], Optional[tuple[int, int]]] = {start: None}
    queue = deque([start])
    while queue:
        x, z = queue.popleft()
        for neighbor in ((x + 1, z), (x - 1, z), (x, z + 1), (x, z - 1)):
            if neighbor in component and neighbor not in parents:
                parents[neighbor] = (x, z)
                queue.append(neighbor)
    if len(parents) < 2:
        return []
    target = max(
        parents,
        key=lambda value: abs(value[0] - start[0]) + abs(value[1] - start[1]),
    )
    path = []
    while target is not None:
        path.append(target)
        target = parents[target]
    return list(reversed(path))


def _move_to_adjacent_block(
    client: Any,
    previous: tuple[int, int],
    destination: tuple[int, int],
    player_y: int,
    *,
    timeout: float = 20.0,
) -> bool:
    """Confirm local movement from observed coordinates, not pathing flags."""
    payload = {"x": destination[0], "y": player_y, "z": destination[1]}
    client.transport.dispatch("goto", payload)
    deadline = time.monotonic() + timeout
    reissue_at = time.monotonic() + 6.0
    while time.monotonic() < deadline:
        state = client.transport.dispatch("get_state", {})
        if float(state.get("health", 20) or 0) <= 0:
            return False
        x, y, z = block_position(state)
        reached_destination = (x, z) == destination and abs(y - player_y) <= 1
        reached_safe_neighbor = (x, z) != previous and max(
            abs(x - destination[0]),
            abs(z - destination[1]),
        ) <= 1
        if reached_destination or reached_safe_neighbor:
            client.transport.dispatch("cancel", {})
            return True
        if time.monotonic() >= reissue_at:
            client.transport.dispatch("goto", payload)
            reissue_at = time.monotonic() + 6.0
        time.sleep(0.5)
    client.transport.dispatch("cancel", {})
    return False


def harvest_shelf_dirt(
    client: Any,
    initial_state: Dict,
    *,
    target_count: int = 10,
) -> int:
    """Walk inward while removing the vacated shelf blocks behind the bot."""
    px, py, pz = block_position(initial_state)
    if py < 96 or carried_dirt(client) >= target_count:
        return carried_dirt(client)
    below = client.transport.dispatch(
        "get_block", {"x": px, "y": py - 2, "z": pz}
    ).get("id", "")
    if "air" not in str(below):
        return carried_dirt(client)
    response = client.transport.dispatch(
        "find_blocks",
        {
            "blocks": ["minecraft:grass_block", "minecraft:dirt"],
            "radius": 16,
            "limit": 4096,
        },
    )
    component = _surface_component(
        response.get("found", []), (px, pz), py - 1
    )
    path = _longest_component_path(component, (px, pz))
    if len(path) < 3:
        return carried_dirt(client)

    client.transport.dispatch("chat", {"message": "#set allowBreak true"})
    break_attempts = 0
    for previous, destination in zip(path, path[1:]):
        if carried_dirt(client) >= target_count or break_attempts >= target_count:
            break
        if not _move_to_adjacent_block(
            client,
            previous,
            destination,
            py,
        ):
            break
        client.transport.dispatch(
            "break_block",
            {"x": previous[0], "y": py - 1, "z": previous[1]},
        )
        break_attempts += 1
        deadline = time.time() + 4
        while time.time() < deadline:
            block = client.transport.dispatch(
                "get_block",
                {"x": previous[0], "y": py - 1, "z": previous[1]},
            ).get("id", "")
            if not block or "air" in block:
                break
            time.sleep(0.2)
        time.sleep(0.6)
    count = carried_dirt(client)
    print(f"DEBUG: Shelf escape recovered {count}/{target_count} dirt blocks")
    return count


def supported_column_descent(
    client: Any,
    initial_state: Dict,
    *,
    target_y: int = 94,
    max_steps: int = 24,
    minimum_altitude: int = 96,
) -> Optional[tuple[int, int, int]]:
    """Descend a void column one supported block at a time.

    Each step places a carried block beneath the floor before removing the
    floor. The removed block lands on the new support and becomes the support
    material for a later step, avoiding both an unsafe fall and item loss.

    ``minimum_altitude`` guards the original Overworld high-shelf use so
    routine low-altitude work never starts digging downwards. A caller that
    has *already proven* the bot cannot move should pass 0: whether a column
    descent is the right escape depends on there being a void underfoot, not
    on the absolute height of the block being stood on. Live 2026-08-03:
    Bot16 stood on a single glowstone block at y=78 in the Nether with 28
    blocks of pure air beneath it and 82 cobblestone in its inventory -- the
    exact situation this function exists for -- and the y<96 gate returned
    None before it looked at anything.
    """
    from .automation_utils import place_block

    client.transport.dispatch("cancel", {})
    time.sleep(0.5)
    current_state = client.transport.dispatch("get_state", {})
    px, py, pz = block_position(current_state)
    start_y = py
    if py < minimum_altitude:
        return None
    for _ in range(max_steps):
        client.transport.dispatch("cancel", {})
        current_state = client.transport.dispatch("get_state", {})
        px, py, pz = block_position(current_state)
        if py <= target_y:
            return (px, py, pz)
        floor_y = py - 1
        current_block = client.transport.dispatch(
            "get_block", {"x": px, "y": py, "z": pz}
        ).get("id", "")
        # Mud's lowered collision surface leaves the player's integer block
        # position inside the mud itself. Looking only at py-1 can select a
        # neighboring footprint block that the player is not standing on;
        # breaking it cannot cause a descent. Treat the inset block as the
        # floor so the verified support two levels down receives the player.
        if "mud" not in str(current_block):
            px, pz = _supporting_column(client, current_state, floor_y)
        else:
            floor_y = py
        support_y = py - 2
        support = client.transport.dispatch(
            "get_block", {"x": px, "y": support_y, "z": pz}
        ).get("id", "")
        if not support or "air" in str(support):
            item_id = _first_placeable_item(client)
            if item_id is None:
                return None
            try:
                placed = place_block(client, px, support_y, pz, item_id)
            except Exception as exc:
                print(f"DEBUG: Supported descent placement failed: {exc}")
                return None
            if not placed:
                return None
        client.transport.dispatch(
            "break_block", {"x": px, "y": floor_y, "z": pz}
        )
        deadline = time.monotonic() + 8.0
        descended = False
        while time.monotonic() < deadline:
            time.sleep(0.3)
            state = client.transport.dispatch("get_state", {})
            if float(state.get("health", 20) or 0) <= 0:
                return None
            nx, ny, nz = block_position(state)
            if ny <= py - 1:
                px, py, pz = nx, ny, nz
                descended = True
                break
        if not descended:
            return None
        time.sleep(0.6)
    return (px, py, pz) if py < start_y else None
