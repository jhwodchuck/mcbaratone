"""
Navigation utilities - Movement, exploration, and hazard avoidance.
"""

import math
import time
from functools import wraps
from typing import Callable, Iterable, Optional, Tuple

from .tasks import PlayerDeathDetected, TaskResult


DefenseCallback = Callable[[], bool]
_DEFENSE_GUARD = "_navigation_defense_callback_active"
_RECOVERY_DEPTH = "_safe_recovery_navigation_depth"

# Below food 18 Minecraft grants no natural regeneration, so a critically
# injured bot cannot heal a single point no matter how long it walks. Bot07
# died crossing ~300 blocks pinned at 2.1 health with food 5: every block was
# unrecoverable damage exposure. Long journeys are refused in that state;
# recovery movement is exempt because that is the path that fetches the food.
_CRITICAL_TRAVEL_HEALTH = 6.0
_REGEN_FOOD_FLOOR = 18
_MAX_CRITICAL_TRAVEL_DISTANCE = 48.0


def _refuse_critical_long_travel(client, x: int, y: int, z: int) -> bool:
    """Return True when a long route must not start at critical health."""
    if int(getattr(client, _RECOVERY_DEPTH, 0) or 0) > 0:
        return False
    try:
        state = client.transport.dispatch("get_state", {})
    except Exception:
        return False
    if not isinstance(state, dict):
        return False
    # Missing telemetry must not block navigation; assume healthy.
    health_value = state.get("health", 20)
    food_value = state.get("food_level", state.get("food", 20))
    health = float(20 if health_value is None else health_value)
    food = int(20 if food_value is None else food_value)
    if health >= _CRITICAL_TRAVEL_HEALTH or food >= _REGEN_FOOD_FLOOR:
        return False

    position = state.get("block_position", state.get("position", {}))
    if not isinstance(position, dict):
        return False
    try:
        distance = math.dist(
            (float(position.get("x", x)), float(position.get("z", z))),
            (float(x), float(z)),
        )
    except (TypeError, ValueError):
        return False
    if distance <= _MAX_CRITICAL_TRAVEL_DISTANCE:
        return False

    print(
        f"NAVIGATION: refusing {distance:.0f}-block route at health "
        f"{health:.1f} food {food}; below food {_REGEN_FOOD_FLOOR} there is no "
        "regeneration, so recover locally before travelling"
    )
    return True


def allow_recovery_navigation(function):
    """Mark nested routes as intentional food/health recovery movement."""

    @wraps(function)
    def wrapped(client, *args, **kwargs):
        depth = int(getattr(client, _RECOVERY_DEPTH, 0) or 0)
        setattr(client, _RECOVERY_DEPTH, depth + 1)
        try:
            return function(client, *args, **kwargs)
        finally:
            setattr(client, _RECOVERY_DEPTH, depth)

    return wrapped


def run_navigation_defense(
    client,
    on_defense: Optional[DefenseCallback] = None,
) -> bool:
    """Run one guarded defense tick during a long navigation operation.

    The lazy import avoids the combat/navigation module cycle. Nested
    navigation started by the defense policy still performs survival checks,
    but it does not recursively start another defense policy invocation.
    """
    if getattr(client, _DEFENSE_GUARD, False):
        return False
    if on_defense is None:
        from .combat import defend_or_flee

        on_defense = lambda: defend_or_flee(
            client,
            allow_safe_recovery_movement=bool(
                getattr(client, _RECOVERY_DEPTH, 0)
            ),
        )

    setattr(client, _DEFENSE_GUARD, True)
    try:
        return bool(on_defense())
    finally:
        setattr(client, _DEFENSE_GUARD, False)


def recovery_navigation_defense(client) -> bool:
    """Defend recovery travel without canceling clear-area movement."""
    from .combat import defend_or_flee

    return defend_or_flee(
        client,
        allow_safe_recovery_movement=True,
    )


def goto(
    client,
    x: int,
    y: int,
    z: int,
    timeout: int = 120,
    check_interval: float = 2.0,
    tolerance: float = 3.0,
    on_tick: Optional[Callable[[], None]] = None,
    on_defense: Optional[DefenseCallback] = None,
    defense_check_interval: float = 0.5,
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
    defense_check_interval = max(0.1, float(defense_check_interval))

    try:
        client._last_navigation_survival_abort = False
        if _refuse_critical_long_travel(client, x, y, z):
            return False
        client.transport.dispatch("goto", {"x": x, "y": y, "z": z})
        
        start = time.time()
        last_position = None
        idle_unpathing_checks = 0
        while time.time() - start < timeout:
            if on_tick:
                on_tick()
            state = client.transport.dispatch("get_state", {})

            # Central survival reflex: surface before drowning. Surfacing
            # cancels the path, and the target may itself be underwater. Do not
            # immediately replay that same goal: live grave recovery repeatedly
            # dived back to a submerged death point until the bot drowned.
            from .combat import survival_tick

            if survival_tick(client, state):
                client._last_navigation_survival_abort = True
                client.transport.dispatch("cancel", {})
                return False

            if run_navigation_defense(client, on_defense):
                client.transport.dispatch("cancel", {})
                return False

            position = state.get("block_position", state.get("position", {}))
            px = position.get("x", state.get("x", 0))
            py = position.get("y", state.get("y", 0))
            pz = position.get("z", state.get("z", 0))

            distance = ((px - x)**2 + (py - y)**2 + (pz - z)**2) ** 0.5
            if distance <= tolerance:
                client.transport.dispatch("cancel", {})
                return True

            current_position = (float(px), float(py), float(pz))
            is_pathing = state.get("is_pathing")
            if is_pathing is False and current_position == last_position:
                idle_unpathing_checks += 1
            elif is_pathing is False:
                idle_unpathing_checks = 1
            else:
                idle_unpathing_checks = 0
            last_position = current_position

            # A rejected Baritone goal reports is_pathing=False immediately.
            # Waiting the full caller timeout cannot make that route start and
            # hides the distinction between an expensive path and no path at
            # all. Three unchanged observations tolerate a brief transition
            # while returning control soon enough for staged recovery.
            if idle_unpathing_checks >= 3:
                client.transport.dispatch("cancel", {})
                return False

            time.sleep(min(float(check_interval), defense_check_interval))
        
        client.transport.dispatch("cancel", {})
        return False
        
    except PlayerDeathDetected:
        try:
            client.transport.dispatch("cancel", {})
        except Exception:
            pass
        raise
    except Exception as e:
        print(f"Navigation error: {e}")
        return False


def recovery_goto(client, x: int, y: int, z: int, **kwargs) -> bool:
    """Navigate for food/health recovery while retaining hostile defense."""
    kwargs.setdefault(
        "on_defense",
        lambda: recovery_navigation_defense(client),
    )
    return goto(client, x, y, z, **kwargs)


def goto_xz(
    client,
    x: int,
    z: int,
    timeout: int = 120,
    check_interval: float = 1.0,
    tolerance: float = 6.0,
    on_defense: Optional[DefenseCallback] = None,
    defense_check_interval: float = 0.5,
) -> bool:
    """Navigate to a horizontal column while Baritone chooses loaded terrain Y."""
    defense_check_interval = max(0.1, float(defense_check_interval))

    try:
        client._last_navigation_survival_abort = False
        try:
            client.transport.dispatch("chat", {"message": f"#goto {x} {z}"})
        except Exception as exc:
            # `#goto` is fire-and-forget: Baritone begins pathing when the
            # message arrives, and the reply carries no information. Treating a
            # slow reply as a navigation failure cost real work -- on
            # 2026-08-07 Bot17's farmer crash-looped on it, because a single
            # "Timeout waiting for bridge response (route: chat)" became
            # goto_xz -> False -> RuntimeError("crop farm is unreachable") ->
            # worker exit, twice in a row, on a farm 14 blocks away that was
            # perfectly reachable.
            #
            # Poll for movement instead. If the command really was lost the
            # idle-unpathing check below still returns False a few seconds
            # later, which callers already handle -- but a command that landed
            # now completes normally.
            print(
                f"Navigation: '#goto {x} {z}' reply timed out ({exc}); "
                "polling for movement instead of failing"
            )
        start = time.time()
        last_position = None
        idle_unpathing_checks = 0
        while time.time() - start < timeout:
            state = client.transport.dispatch("get_state", {})
            from .combat import survival_tick

            if survival_tick(client, state):
                client._last_navigation_survival_abort = True
                client.transport.dispatch("cancel", {})
                return False
            if run_navigation_defense(client, on_defense):
                client.transport.dispatch("cancel", {})
                return False
            position = state.get("block_position", state.get("position", {}))
            px = float(position.get("x", state.get("x", 0)) or 0)
            pz = float(position.get("z", state.get("z", 0)) or 0)
            if math.hypot(px - x, pz - z) <= tolerance:
                client.transport.dispatch("cancel", {})
                return True

            current_position = (px, pz)
            is_pathing = state.get("is_pathing")
            if is_pathing is False and current_position == last_position:
                idle_unpathing_checks += 1
            elif is_pathing is False:
                idle_unpathing_checks = 1
            else:
                idle_unpathing_checks = 0
            last_position = current_position
            if idle_unpathing_checks >= 3:
                client.transport.dispatch("cancel", {})
                return False
            time.sleep(min(float(check_interval), defense_check_interval))
        client.transport.dispatch("cancel", {})
        return False
    except PlayerDeathDetected:
        try:
            client.transport.dispatch("cancel", {})
        except Exception:
            pass
        raise
    except Exception as exc:
        print(f"Horizontal navigation error: {exc}")
        return False


def staged_goto(
    client,
    target: tuple[int, int, int],
    origin: tuple[int, int, int],
    *,
    maximum_leg: float = 32.0,
    navigate=None,
) -> bool:
    """Approach a distant exact goal through bounded horizontal legs."""
    navigate = navigate or goto
    current_x, current_y, current_z = origin
    target_x, target_y, target_z = target
    horizontal = math.hypot(target_x - current_x, target_z - current_z)
    if horizontal <= 48:
        return False
    stages = max(1, int(horizontal // maximum_leg))
    for index in range(1, stages + 1):
        ratio = min(1.0, (index * maximum_leg) / horizontal)
        nominal_y = round(current_y + (target_y - current_y) * ratio)
        waypoint = (
            round(current_x + (target_x - current_x) * ratio),
            _loaded_stage_y(
                client,
                round(current_x + (target_x - current_x) * ratio),
                nominal_y,
                round(current_z + (target_z - current_z) * ratio),
            ),
            round(current_z + (target_z - current_z) * ratio),
        )
        print(f"  Staging home approach via {waypoint}...")
        if not navigate(
            client,
            waypoint[0],
            waypoint[1],
            waypoint[2],
            timeout=90,
            check_interval=1.0,
            tolerance=6.0,
        ):
            print(
                "  Exact staging height was rejected; retrying the column "
                "without pinning Y..."
            )
            if not goto_xz(
                client,
                waypoint[0],
                waypoint[2],
                timeout=90,
                tolerance=6.0,
            ):
                return False
    if navigate(
        client,
        target_x,
        target_y,
        target_z,
        timeout=180,
        check_interval=1.0,
        tolerance=2.0,
    ):
        return True
    # The final column may still be unloaded after the last interpolation
    # leg. Load it with a Y-agnostic goal, then retry the exact doorway/farm
    # height once its terrain is known.
    if not goto_xz(client, target_x, target_z, timeout=120, tolerance=6.0):
        return False
    return navigate(
        client,
        target_x,
        target_y,
        target_z,
        timeout=90,
        check_interval=1.0,
        tolerance=2.0,
    )


def _loaded_stage_y(client, x: int, nominal_y: int, z: int) -> int:
    """Use the top of a loaded column instead of an arbitrary exact Y."""
    for y in range(nominal_y + 16, nominal_y - 33, -1):
        try:
            block = client.transport.dispatch(
                "get_block", {"x": x, "y": y, "z": z}
            ).get("id", "")
        except Exception:
            return nominal_y
        if block in ("", "minecraft:air", "minecraft:void_air"):
            continue
        if block == "minecraft:lava":
            return nominal_y
        return y + 1
    return nominal_y


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

            # Central survival reflex: exploration wanders into open water.
            from .combat import survival_tick

            if survival_tick(client, state):
                client.transport.dispatch("explore", {"x": origin_x, "z": origin_z})
                time.sleep(1.0)
                continue

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
        requested_radius = max(1, min(int(radius), 128))
        search_radii = [value for value in (8, 16, 32, 64, 128) if value < requested_radius]
        search_radii.append(requested_radius)

        # The bridge scans x/y/z order and does not sort its response.  Asking
        # for limit=1 therefore returns the western-most match, which can send
        # the bot hundreds of blocks away from a much closer tree or utility.
        # Search outward in bounded rings and sort the returned batch here.
        for search_radius in search_radii:
            data = client.transport.dispatch("find_blocks", {
                "blocks": block_types,
                "radius": search_radius,
                "limit": 4096,
            })
            found = data.get("found", [])
            if not found:
                continue

            block = min(
                found,
                key=lambda value: float(value.get("distance", float("inf"))),
            )
            return (int(block["x"]), int(block["y"]), int(block["z"]))

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
