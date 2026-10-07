"""Keep surface-bound home routes out of the underground home footprint.

The saved anchor identifies the route, not proof of a floor or shelter. This
guard constrains protected home routes, but does not promise a safe route or
excavation.
"""

import math
import time
from functools import wraps
from inspect import signature
from .tasks import PlayerDeathDetected, SurvivalRecoveryRequired


HOME_RADIUS = 24.0
HOME_FLOOR_MARGIN = 2.0


def bind_home_surface(client, state):
    """Install a finite home anchor, clearing stale state on invalid input."""
    custom = getattr(state, "custom_data", {})
    anchor = custom.get("base_location") if isinstance(custom, dict) else None
    valid = isinstance(anchor, (list, tuple)) and len(anchor) == 3
    try:
        valid = valid and all(not isinstance(v, bool) and math.isfinite(float(v)) for v in anchor)
        client._protected_home_anchor = tuple(float(v) for v in anchor) if valid else None
    except (TypeError, ValueError):
        client._protected_home_anchor = None


def home_route_floor(client, x, y, z):
    """Guard only homeward surface goals, not explicit underground mining."""
    work = getattr(client, "_protected_surface_work", None)
    if work is not None and math.hypot(x - work[0], z - work[2]) <= HOME_RADIUS:
        return work
    anchor = getattr(client, "_protected_home_anchor", None)
    if anchor is None:
        return None
    hx, hy, hz = anchor
    floor = hy - HOME_FLOOR_MARGIN
    if math.hypot(x - hx, z - hz) > HOME_RADIUS or y < floor:
        return None
    return hx, floor, hz


def below_home_surface(guard, position):
    """A cave elsewhere is not this guard's concern."""
    if guard is None:
        return False
    hx, floor, hz = guard
    return (math.hypot(position["x"] - hx, position["z"] - hz) <= HOME_RADIUS
            and position["y"] < floor)


def _read_break_setting(client):
    response = client.transport.dispatch("settings", {"get": "allowBreak"})
    value = response.get("value") if isinstance(response, dict) else None
    if value not in ("true", "false"):
        raise ValueError("allowBreak setting unavailable or malformed")
    return value


def _write_break_setting(client, value):
    client.transport.dispatch("settings", {"set": "allowBreak", "value": value})
    # The bridge replies 'requested', not 'applied'. Never start on that alone.
    for _ in range(3):
        if _read_break_setting(client) == value:
            return
        time.sleep(0.05)
    raise ValueError("allowBreak update was not observed")


_SAFE_ROUTE_SETTINGS = {"allowParkour": "false", "maxFallHeightNoWater": "1"}


def _read_route_setting(client, name):
    response = client.transport.dispatch("settings", {"get": name})
    key = response.get("key") if isinstance(response, dict) else None
    if not isinstance(key, str) or key.lower() != name.lower():
        raise ValueError(f"{name} setting unavailable or malformed")
    value = response.get("value")
    if not isinstance(value, str):
        raise ValueError(f"{name} setting unavailable or malformed")
    if name == "allowParkour" and value not in ("true", "false"):
        raise ValueError("allowParkour setting unavailable or malformed")
    if name == "maxFallHeightNoWater":
        try:
            if str(int(value)) != value or int(value) < 0:
                raise ValueError
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("maxFallHeightNoWater setting unavailable or malformed") from exc
    return value


def _write_route_setting(client, name, value):
    client.transport.dispatch("settings", {"set": name, "value": value})
    for _ in range(3):
        if _read_route_setting(client, name) == value:
            return
        time.sleep(0.05)
    raise ValueError(f"{name} update was not observed")


def protect_home_route(*, horizontal=False, surface_work=False, safe_movement=False):
    """Guard a homeward route's digging and optional movement settings.

    Safe movement is restricted to callers that opt in and goals inside the
    saved home/surface guard. If cleanup cannot prove the route stopped, leave
    temporary settings in their conservative state rather than alter a live goal.
    """
    def decorate(function):
        call_signature = signature(function)
        @wraps(function)
        def wrapped(*args, **kwargs):
            parameters = call_signature.bind(*args, **kwargs).arguments
            client, x, z = (parameters[key] for key in ("client", "x", "z"))
            anchor = getattr(client, "_protected_home_anchor", None)
            y = (anchor[1] if anchor else 0) if horizontal else parameters["y"]
            guarded = home_route_floor(client, x, y, z) is not None
            if surface_work and anchor is not None and y >= anchor[1] - HOME_FLOOR_MARGIN:
                guarded = True
            if not guarded:
                return function(*args, **kwargs)
            previous = None
            previous_movement = {}
            prior_work = getattr(client, "_protected_surface_work", None)
            result = False
            try:
                previous = _read_break_setting(client)
                if safe_movement:
                    previous_movement = {
                        name: _read_route_setting(client, name)
                        for name in _SAFE_ROUTE_SETTINGS
                    }
                # Read every prior value before the first write so partial
                # observations can never leave a half-applied travel policy.
                if previous == "true":
                    _write_break_setting(client, "false")
                if safe_movement:
                    for name, value in _SAFE_ROUTE_SETTINGS.items():
                        _write_route_setting(client, name, value)
                if surface_work:
                    client._protected_surface_work = (x, y - HOME_FLOOR_MARGIN, z)
                result = function(*args, **kwargs)
            except (PlayerDeathDetected, SurvivalRecoveryRequired):
                raise
            except (ValueError, RuntimeError) as exc:
                print(f"HOME ROUTE: refused unverified digging protection ({exc})")
            finally:
                client._protected_surface_work = prior_work
                if previous == "true" or previous_movement:
                    try:
                        live = client.transport.dispatch("get_state", {})
                        if live.get("is_pathing") is not False:
                            raise ValueError("route stop is unverified; safe settings remain active")
                        restore_failed = False
                        for name, value in reversed(tuple(previous_movement.items())):
                            try:
                                _write_route_setting(client, name, value)
                            except Exception as exc:
                                restore_failed = True
                                print(f"HOME ROUTE: could not restore {name} ({exc})")
                        if previous == "true":
                            _write_break_setting(client, previous)
                        if restore_failed:
                            result = False
                    except Exception as exc:
                        print(f"HOME ROUTE: could not restore protected route settings ({exc})")
                        result = False
            return result
        return wrapped
    return decorate
