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


def bind_home_surface(client, state, *, controller_start=False):
    """Install a finite home anchor, clearing stale state on invalid input.

    ``controller_start``: Baritone saves ``#set`` changes, so never inherit a
    crashed run's water ban or a guard's conservative route settings.
    """
    custom = getattr(state, "custom_data", {})
    anchor = custom.get("base_location") if isinstance(custom, dict) else None
    valid = isinstance(anchor, (list, tuple)) and len(anchor) == 3
    try:
        valid = valid and all(not isinstance(v, bool) and math.isfinite(float(v)) for v in anchor)
        client._protected_home_anchor = tuple(float(v) for v in anchor) if valid else None
    except (TypeError, ValueError):
        client._protected_home_anchor = None
    from .water_hazards import allow_water, bind_drowning_hazards

    bind_drowning_hazards(client, state)
    if controller_start:
        try:
            allow_water(client, force=True)
            reset_guarded_route_settings(client)
        except Exception as exc:
            print(f"Route setting reset deferred: {exc}")
    elif getattr(client, "_home_route_owed", None):
        restore_owed_home_route_settings(client)


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


def _checked_response_data(response, operation):
    data = response
    seen = set()
    while isinstance(data, dict) and id(data) not in seen:
        seen.add(id(data))
        if (data.get("success") is False or str(data.get("status", "")).lower() == "error"
                or data.get("error")):
            raise ValueError(f"{operation} returned an error response")
        nested = data.get("data")
        if not isinstance(nested, dict):
            return data
        data = nested
    raise ValueError(f"{operation} response is malformed")


def _read_break_setting(client, *, strict=False):
    response = client.transport.dispatch("settings", {"get": "allowBreak"})
    if strict:
        response = _checked_response_data(response, "allowBreak read")
    value = response.get("value") if isinstance(response, dict) else None
    if value not in ("true", "false"):
        raise ValueError("allowBreak setting unavailable or malformed")
    return value


def _write_break_setting(client, value, *, strict=False):
    response = client.transport.dispatch("settings", {"set": "allowBreak", "value": value})
    if strict:
        _checked_response_data(response, "allowBreak write")
    # The bridge replies 'requested', not 'applied'. Never start on that alone.
    for _ in range(_OBSERVE_POLLS):
        observed = (
            _read_break_setting(client, strict=True)
            if strict else _read_break_setting(client)
        )
        if observed == value:
            return
        time.sleep(_OBSERVE_INTERVAL)
    raise ValueError("allowBreak update was not observed")


_SAFE_ROUTE_SETTINGS = {"allowParkour": "false", "maxFallHeightNoWater": "1"}
#: The bridge applies a setting on a later client tick. Three 50 ms reads
#: reported correct restores as "not observed" whenever the client lagged:
#: A1 logged nine between 2026-10-15 and 10-18, and the last one left
#: digging disabled for three days. Wait about a second instead.
_OBSERVE_POLLS = 20
_OBSERVE_INTERVAL = 0.05
#: Settings a guard turns conservative for a while. A crash or an unverified
#: stop between "off" and "back on" is saved by Baritone and outlives the
#: controller; A1 then spent 25 hours sealed in a 1x1 shaft it could not dig
#: out of (2026-10-19..21). Controller start returns them to their defaults.
_GUARDED_SETTINGS = ("allowBreak", "allowSprint", "allowParkour", "maxFallHeightNoWater")


def reset_guarded_route_settings(client):
    """Return guard-owned Baritone settings to their defaults at controller start."""
    for name in _GUARDED_SETTINGS:
        client.transport.dispatch("chat", {"message": f"#set reset {name}"})
    client._home_route_owed = {}
    time.sleep(0.1)  # chat settings apply on a client tick


def _owe(client, owed):
    """Remember values an unverified stop left conservative. The first value
    owed is the real original; later reads only see the leftover."""
    pending = dict(getattr(client, "_home_route_owed", None) or {})
    for name, value in owed.items():
        pending.setdefault(name, value)
    client._home_route_owed = pending


def _write_owed(client, name, value):
    if name == "allowBreak":
        _write_break_setting(client, value)
    else:
        _write_route_setting(client, name, value)


def restore_owed_home_route_settings(client):
    """Retry restores an unverified stop deferred, once nothing is moving.

    Without this the guard restored whatever it read on entry, so one leaked
    "false" was re-read and kept by every later guard: a permanent ratchet.
    """
    owed = dict(getattr(client, "_home_route_owed", None) or {})
    if not owed or getattr(client, "_home_route_active", 0):
        return not owed
    try:
        if client.transport.dispatch("get_state", {}).get("is_pathing") is not False:
            return False
        for name, value in list(owed.items()):
            _write_owed(client, name, value)
            owed.pop(name)
    except Exception:
        client._home_route_owed = owed
        return False
    client._home_route_owed = owed
    print("HOME ROUTE: restored route settings an unverified stop had left conservative")
    return True


def _read_route_setting(client, name):
    response = client.transport.dispatch("settings", {"get": name})
    response = _checked_response_data(response, f"{name} read")
    key = response.get("key")
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
    response = client.transport.dispatch("settings", {"set": name, "value": value})
    _checked_response_data(response, f"{name} write")
    for _ in range(_OBSERVE_POLLS):
        if _read_route_setting(client, name) == value:
            return
        time.sleep(_OBSERVE_INTERVAL)
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
            client._home_route_active = getattr(client, "_home_route_active", 0) + 1
            try:
                owed = getattr(client, "_home_route_owed", None) or {}
                previous = (
                    _read_break_setting(client, strict=True)
                    if safe_movement else _read_break_setting(client)
                )
                if owed.get("allowBreak") == "true":
                    previous = "true"  # our own leftover, not the caller's choice
                if safe_movement:
                    previous_movement = {
                        name: owed.get(name, _read_route_setting(client, name))
                        for name in _SAFE_ROUTE_SETTINGS
                    }
                # Read every prior value before the first write so partial
                # observations can never leave a half-applied travel policy.
                if previous == "true":
                    if safe_movement:
                        _write_break_setting(client, "false", strict=True)
                    else:
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
                client._home_route_active = max(0, getattr(client, "_home_route_active", 1) - 1)
                if previous == "true" or previous_movement:
                    owing = dict(previous_movement)
                    if previous == "true":
                        owing["allowBreak"] = "true"
                    try:
                        live = client.transport.dispatch("get_state", {})
                        if safe_movement:
                            live = _checked_response_data(live, "route stop read")
                        if live.get("is_pathing") is not False:
                            raise ValueError("route stop is unverified; safe settings remain active")
                        restore_failed = False
                        for name, value in reversed(tuple(previous_movement.items())):
                            try:
                                _write_route_setting(client, name, value)
                                owing.pop(name, None)
                            except Exception as exc:
                                restore_failed = True
                                print(f"HOME ROUTE: could not restore {name} ({exc})")
                        if previous == "true":
                            if safe_movement:
                                _write_break_setting(client, previous, strict=True)
                            else:
                                _write_break_setting(client, previous)
                            owing.pop("allowBreak", None)
                        if restore_failed:
                            result = False
                    except Exception as exc:
                        print(f"HOME ROUTE: could not restore protected route settings ({exc})")
                        result = False
                    # Restored values are no longer owed; anything left is
                    # retried once nothing is moving.
                    pending = {name: value for name, value in (getattr(client, "_home_route_owed", None) or {}).items()
                               if name not in previous_movement and not (name == "allowBreak" and previous == "true")}
                    client._home_route_owed = pending
                    if owing:
                        _owe(client, owing)
            return result
        return wrapped
    return decorate
