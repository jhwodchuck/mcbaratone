"""Navigation utilities - movement, exploration, and hazard avoidance."""

import math
import time
from functools import wraps
from typing import Callable, Iterable, Optional, Tuple

from ..core.exceptions import BridgeResponseTimeout
from .tasks import PlayerDeathDetected, TaskResult


DefenseCallback = Callable[[], bool]
_DEFENSE_GUARD = "_navigation_defense_callback_active"
_RECOVERY_DEPTH = "_safe_recovery_navigation_depth"

# Refuse long journeys when critical health cannot regenerate; retain recovery.
_CRITICAL_TRAVEL_HEALTH = 6.0
_REGEN_FOOD_FLOOR = 18
_MAX_CRITICAL_TRAVEL_DISTANCE = 48.0
_MAX_CONSECUTIVE_STATE_MISSES = 2
_VERIFIED_STATE_FRESH_SECONDS = 30.0


class _UnsafeNavigationTelemetry(RuntimeError):
    """The bridge returned state that cannot safely supervise movement."""


def _verified_navigation_state(raw_state) -> dict:
    """Validate the safety fields required before or during movement."""
    if not isinstance(raw_state, dict):
        raise _UnsafeNavigationTelemetry("navigation state is not an object")
    health_value = raw_state.get("health")
    try:
        health = float(health_value)
    except (TypeError, ValueError) as exc:
        raise _UnsafeNavigationTelemetry("navigation health is not numeric") from exc
    if raw_state.get("is_dead") is True or health <= 0:
        raise PlayerDeathDetected("player died during navigation")
    position = raw_state.get("block_position", raw_state.get("position"))
    food_value = raw_state.get("food_level", raw_state.get("food"))
    values = (health_value, food_value)
    if not isinstance(position, dict) or any(value is None for value in values):
        raise _UnsafeNavigationTelemetry("navigation state is incomplete")
    coordinates = (position.get("x"), position.get("y"), position.get("z"))
    if any(value is None or isinstance(value, bool) for value in coordinates + values):
        raise _UnsafeNavigationTelemetry("navigation state has invalid fields")
    try:
        food = int(values[1])
        x, y, z = (float(value) for value in coordinates)
    except (TypeError, ValueError) as exc:
        raise _UnsafeNavigationTelemetry("navigation state is not numeric") from exc
    if not all(math.isfinite(value) for value in (health, x, y, z)):
        raise _UnsafeNavigationTelemetry("navigation state is not finite")
    if not 0 <= food <= 20:
        raise _UnsafeNavigationTelemetry("navigation food level is out of range")
    verified = dict(raw_state)
    verified["health"], verified["food_level"] = health, food
    verified["block_position"] = {"x": x, "y": y, "z": z}
    return verified


class _VerifiedStateWindow:
    """Bound stale-state grace to recent, healthy bridge telemetry."""

    def __init__(self, state: dict) -> None:
        self.state = state
        self.observed_at = time.monotonic()
        self.misses = 0

    def read(self, client) -> Optional[dict]:
        try:
            raw_state = client.transport.dispatch("get_state", {})
        except BridgeResponseTimeout as exc:
            if exc.route != "get_state":
                raise
            self.misses += 1
            age = time.monotonic() - self.observed_at
            too_many = self.misses > _MAX_CONSECUTIVE_STATE_MISSES
            stale = age > _VERIFIED_STATE_FRESH_SECONDS
            critical = float(self.state["health"]) < _CRITICAL_TRAVEL_HEALTH
            if too_many or stale or critical:
                raise
            return None
        state = _verified_navigation_state(raw_state)
        self.state = state
        self.observed_at = time.monotonic()
        self.misses = 0
        return state


def _dispatch_indeterminate_goal(client, route: str, payload: dict) -> None:
    """Accept only a matching timeout after local transmission completed."""
    try:
        client.transport.dispatch(route, payload)
    except BridgeResponseTimeout as exc:
        if exc.route != route or not exc.request_sent:
            raise
        print(f"Navigation: {route} response timed out; polling verified state")


def _refuse_critical_long_travel(
    client, x: int, y: int, z: int, *, state: Optional[dict] = None
) -> bool:
    """Return True when a long route must not start at critical health."""
    if state is None:
        try:
            state = _verified_navigation_state(client.transport.dispatch("get_state", {}))
        except _UnsafeNavigationTelemetry:
            return True
    if int(getattr(client, _RECOVERY_DEPTH, 0) or 0) > 0:
        return False
    health = float(state["health"])
    food = int(state["food_level"])
    if health >= _CRITICAL_TRAVEL_HEALTH or food >= _REGEN_FOOD_FLOOR:
        return False

    position = state["block_position"]
    distance = math.dist(
        (float(position["x"]), float(position["z"])),
        (float(x), float(z)),
    )
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
    """Navigate to exact coordinates under fresh-state safety supervision."""
    from .navigation_supervision import goto as supervised_goto
    return supervised_goto(
        client, x, y, z, timeout=timeout, check_interval=check_interval,
        tolerance=tolerance, on_tick=on_tick, on_defense=on_defense,
        defense_check_interval=defense_check_interval,
    )


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
    from .navigation_supervision import goto_xz as supervised_goto_xz
    return supervised_goto_xz(
        client, x, z, timeout=timeout, check_interval=check_interval,
        tolerance=tolerance, on_defense=on_defense,
        defense_check_interval=defense_check_interval,
    )


def _loaded_stage_y(client, x: int, nominal_y: int, z: int) -> int:
    """Use the top of a loaded column instead of an arbitrary exact Y."""
    for y in range(nominal_y + 16, nominal_y - 33, -1):
        block = client.transport.dispatch(
            "get_block", {"x": x, "y": y, "z": z}
        ).get("id", "")
        if block in ("", "minecraft:air", "minecraft:void_air"):
            continue
        if block == "minecraft:lava":
            raise ValueError(f"unsafe lava surface at ({x}, {y}, {z})")
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


# staged_goto lives in staged_approach (navigation.py is over its size budget).
# Re-exported here because callers and tests import it from this module.
from .staged_approach import (  # noqa: E402
    ARRIVAL_RADIUS,
    _horizontal_gap_to,
    staged_goto,
)
