"""
Navigation utilities - Movement, exploration, and hazard avoidance.
"""

import math
import time
from typing import Callable, Iterable, Optional, Tuple

from .tasks import TaskResult


def goto(
    client,
    x: int,
    y: int,
    z: int,
    timeout: int = 120,
    check_interval: float = 2.0,
    tolerance: float = 3.0,
    on_tick: Optional[Callable[[], None]] = None,
) -> bool:
    """
    Navigate to specific coordinates.
    
    Args:
        client: Baritone client
        x, y, z: Target coordinates
        timeout: Maximum seconds to wait
        check_interval: Seconds between status checks
        tolerance: Distance considered "arrived"
        on_tick: Optional callback for each iteration
        
    Returns:
        True if reached destination
    """
    try:
        client.transport.dispatch("goto", {"x": x, "y": y, "z": z})
        
        start = time.time()
        while time.time() - start < timeout:
            if on_tick:
                on_tick()
                
            state = client.transport.dispatch("get_state", {})
            position = state.get("block_position", state.get("position", {}))
            px = position.get("x", state.get("x", 0))
            py = position.get("y", state.get("y", 0))
            pz = position.get("z", state.get("z", 0))
            
            distance = ((px - x)**2 + (py - y)**2 + (pz - z)**2) ** 0.5
            if distance <= tolerance:
                client.transport.dispatch("cancel", {})
                return True
            
            time.sleep(check_interval)
        
        client.transport.dispatch("cancel", {})
        return False
        
    except Exception as e:
        print(f"Navigation error: {e}")
        return False


def explore_until(
    client,
    condition: Callable[[], bool],
    max_distance: int = 1000,
    timeout: int = 300,
    on_tick: Optional[Callable[[], None]] = None,
) -> bool:
    """
    Explore until a condition is met.
    
    Args:
        client: Baritone client
        condition: Callable that returns True when exploration should stop
        max_distance: Maximum exploration distance
        timeout: Maximum seconds to explore
        on_tick: Optional callable to run every loop iteration
        
    Returns:
        True if condition was met
    """
    try:
        # Get current position as origin
        state = client.transport.dispatch("get_state", {})
        position = state.get("block_position", state.get("position", {}))
        origin_x = int(position.get("x", state.get("x", 0)))
        origin_z = int(position.get("z", state.get("z", 0)))
        
        # Start exploration
        client.transport.dispatch("explore", {"x": origin_x, "z": origin_z})
        
        start = time.time()
        while time.time() - start < timeout:
            if condition():
                client.transport.dispatch("cancel", {})
                return True
            
            if on_tick:
                on_tick()
            
            # Check distance from origin
            state = client.transport.dispatch("get_state", {})
            position = state.get("block_position", state.get("position", {}))
            px = position.get("x", state.get("x", 0))
            pz = position.get("z", state.get("z", 0))
            distance = ((px - origin_x)**2 + (pz - origin_z)**2) ** 0.5
            
            if distance > max_distance:
                client.transport.dispatch("cancel", {})
                return False
            
            # Fail fast if Baritone stops exploring (e.g. finished the area)
            state = client.transport.dispatch("get_state", {})
            is_pathing = state.get("is_pathing", True)
            if not is_pathing:
                 # Check again to be sure (brief pause?)
                 idle_checks = locals().get('idle_checks', 0) + 1
                 if idle_checks >= 3: # 6 seconds of idle
                     print("Exploration finished early (pathing stopped). Moving to next layer.")
                     return False
            else:
                 idle_checks = 0
            
            print(f"Exploring... d={distance:.1f}/{max_distance} t={time.time()-start:.1f}/{timeout}")
            time.sleep(0.5)
        
        client.transport.dispatch("cancel", {})
        return False
        
    except Exception as e:
        print(f"Exploration error: {e}")
        return False


def spiral_explore(
    client,
    condition: Optional[Callable[[], bool]] = None,
    max_layers: int = 4,
    step: int = 48,
    dwell: float = 5.0,
    on_tick: Optional[Callable[[], None]] = None,
) -> TaskResult:
    """
    Fan out from the current position using concentric square rings.

    Args:
        client: Baritone client
        condition: Optional callable evaluated between layers to break early
        max_layers: Number of rings to traverse
        step: How far apart each ring is in blocks
        dwell: Seconds to linger between each ring expansion
        on_tick: Optional callback for each tick
    """
    try:
        state = client.transport.dispatch("get_state", {})
        position = state.get("block_position", state.get("position", {}))
        origin_x = int(position.get("x", state.get("x", 0)))
        origin_z = int(position.get("z", state.get("z", 0)))
    except Exception as exc:
        return TaskResult.fail(f"Unable to determine spawn location: {exc}")

    visited = 0
    for layer in range(1, max_layers + 1):
        radius = step * layer
        timeout = max(120, radius * 4)
        reached = explore_until(
            client,
            condition or (lambda: False),
            max_distance=radius,
            timeout=timeout,
            on_tick=on_tick,
        )
        visited += 1
        if reached:
            return TaskResult.ok(
                f"Condition satisfied during layer {layer}",
                layer=layer,
                visited=visited,
                radius=radius,
            )
        if condition and condition():
            return TaskResult.ok(
                "Condition satisfied locally",
                layer=layer,
                visited=visited,
                radius=radius,
            )
        time.sleep(dwell)

    return TaskResult.fail(
        "Exploration finished without satisfying condition",
        visited=visited,
        radius=step * max_layers,
    )


def return_to_base(
    client,
    base_coords: Tuple[int, int, int],
    timeout: int = 300,
) -> bool:
    """
    Navigate back to base location.
    
    Args:
        client: Baritone client
        base_coords: (x, y, z) of base
        timeout: Maximum seconds to return
        
    Returns:
        True if returned to base
    """
    return goto(client, *base_coords, timeout=timeout)


def find_nearby_block(
    client,
    block_types: list,
    radius: int = 50,
) -> Optional[Tuple[int, int, int]]:
    """
    Find nearest block of specified type.
    
    Args:
        client: Baritone client
        block_types: List of block IDs to search for
        radius: Search radius
        
    Returns:
        (x, y, z) of nearest block or None
    """
    try:
        data = client.transport.dispatch("find_blocks", {
            "blocks": block_types,
            "radius": radius,
            "limit": 1,
        })
        
        found = data.get("found", [])
        if found:
            block = found[0]
            return (block["x"], block["y"], block["z"])
        
        return None
        
    except Exception:
        return None


def goto_block(
    client,
    block_types: list,
    radius: int = 50,
    timeout: int = 120,
) -> bool:
    """
    Navigate to nearest block of specified type.
    
    Args:
        client: Baritone client
        block_types: List of block IDs to find
        radius: Search radius
        timeout: Navigation timeout
        
    Returns:
        True if found and reached block
    """
    coords = find_nearby_block(client, block_types, radius)
    if coords:
        return goto(client, *coords, timeout=timeout)
    return False


def column_clear(
    client,
    center: Optional[Tuple[int, int, int]] = None,
    radius: int = 3,
    height: int = 6,
    block_whitelist: Optional[Iterable[str]] = None,
) -> TaskResult:
    """
    Clear a rectangular prism around the provided coordinates.

    Args:
        client: Baritone client
        center: Center of the column. Defaults to the player's location.
        radius: Horizontal radius (number of blocks from center)
        height: Vertical height in blocks above center.y
        block_whitelist: Optional block IDs to focus on when mining
    """
    try:
        if center is None:
            state = client.transport.dispatch("get_state", {})
            position = state.get("block_position", state.get("position", {}))
            center = (
                int(position.get("x", state.get("x", 0))),
                int(position.get("y", state.get("y", 64))),
                int(position.get("z", state.get("z", 0))),
            )
    except Exception as exc:
        return TaskResult.fail(f"Unable to determine player position: {exc}")

    cx, cy, cz = center
    block_whitelist = tuple(block_whitelist or (
        "minecraft:oak_log",
        "minecraft:birch_log",
        "minecraft:spruce_log",
        "minecraft:stone",
        "minecraft:cobblestone",
        "minecraft:dirt",
        "minecraft:grass_block",
    ))

    mined = 0
    total_targets = (2 * radius + 1) ** 2 * height

    for dy in range(height):
        y = cy + dy
        for dx in range(-radius, radius + 1):
            for dz in range(-radius, radius + 1):
                if math.sqrt(dx * dx + dz * dz) > radius + 0.25:
                    continue
                if not goto(client, cx + dx, y, cz + dz, tolerance=1.5, timeout=20):
                    continue
                client.transport.dispatch(
                    "mine",
                    {"blocks": list(block_whitelist), "quantity": 1},
                )
                mined += 1

    return TaskResult.ok(
        f"Cleared {mined}/{total_targets} blocks",
        mined=mined,
        target=total_targets,
        center=center,
        radius=radius,
        height=height,
    )


def safe_return(
    client,
    base_coords: Tuple[int, int, int],
    ascent_margin: int = 6,
    max_wait: int = 240,
) -> TaskResult:
    """
    Return to the base coordinates by first ascending above potential caves.

    Args:
        client: Baritone client
        base_coords: (x, y, z) tuple for the base
        ascent_margin: Number of blocks above current Y level to ascend before heading home
        max_wait: Maximum seconds allotted for the full return trip
    """
    try:
        state = client.transport.dispatch("get_state", {})
        position = state.get("block_position", state.get("position", {}))
        current = (
            int(position.get("x", state.get("x", 0))),
            int(position.get("y", state.get("y", 64))),
            int(position.get("z", state.get("z", 0))),
        )
    except Exception as exc:
        return TaskResult.fail(f"Unable to read current position: {exc}")

    ascend_target = (current[0], current[1] + max(ascent_margin, 1), current[2])
    ascent_success = goto(client, *ascend_target, tolerance=2.0, timeout=max_wait // 3)
    arrival_success = False
    if ascent_success:
        arrival_success = return_to_base(client, base_coords, timeout=max_wait)

    if ascent_success and arrival_success:
        return TaskResult.ok(
            "Returned to base safely",
            start=current,
            ascent_target=ascend_target,
            destination=base_coords,
        )
    return TaskResult.fail(
        "Unable to return safely",
        start=current,
        ascent_target=ascend_target,
        destination=base_coords,
        ascent_success=ascent_success,
        arrival_success=arrival_success,
    )
