"""Homeward surface routes must not accept the cave below home as arrival."""

from types import SimpleNamespace

import pytest

from baritone_client.common import combat, home_surface, navigation


@pytest.mark.parametrize("horizontal", [False, True])
def test_homeward_route_cancels_on_drop_before_defense_or_arrival(monkeypatch, horizontal):
    calls = []
    reads = 0
    settings = {
        "allowBreak": "true",
        "allowParkour": "true",
        "maxFallHeightNoWater": "3",
    }

    def dispatch(route, payload, **kwargs):
        nonlocal reads
        calls.append((route, payload))
        if route == "settings":
            name = payload.get("get", payload.get("set"))
            if "set" in payload:
                settings[name] = payload["value"]
            return {"key": name, "value": settings[name]}
        if route == "get_state":
            reads += 1
            return {"health": 20, "food_level": 20, "is_pathing": False,
                    "block_position": {"x": 100, "y": 64 if reads == 1 else 61, "z": 100}}
        return {}

    client = SimpleNamespace(transport=SimpleNamespace(dispatch=dispatch),
                             _protected_home_anchor=(100, 64, 100))
    monkeypatch.setattr(combat, "survival_tick", lambda *_a: pytest.fail("guard must run first"))
    result = (navigation.goto_xz(client, 100, 100) if horizontal else
              navigation.goto(client, 100, 64, 100, tolerance=8))
    assert not result
    assert client._last_navigation_outcome == "home_surface_abort"
    assert sum(route == "cancel" for route, _ in calls) == 1
    assert settings == {
        "allowBreak": "true",
        "allowParkour": "true",
        "maxFallHeightNoWater": "3",
    }


def test_guard_preserves_explicit_mining_and_outside_home_routes():
    client = SimpleNamespace(_protected_home_anchor=(100, 64, 100))
    assert home_surface.home_route_floor(client, 100, 50, 100) is None
    assert home_surface.home_route_floor(client, 160, 64, 100) is None
    floor = home_surface.home_route_floor(client, 100, 64, 100)
    assert not home_surface.below_home_surface(floor, {"x": 160, "y": 50, "z": 100})
    assert home_surface.below_home_surface(floor, {"x": 100, "y": 61, "z": 100})


@pytest.mark.parametrize("anchor", [[1, float("nan"), 1], [True, 64, 1], [1, 2], None])
def test_invalid_anchor_is_not_installed(anchor):
    client = SimpleNamespace(_protected_home_anchor=(100, 64, 100))
    home_surface.bind_home_surface(client, SimpleNamespace(custom_data={"base_location": anchor}))
    assert client._protected_home_anchor is None


def test_unverified_setting_does_not_start_movement():
    calls = []
    client = SimpleNamespace(_protected_home_anchor=(100, 64, 100),
        transport=SimpleNamespace(dispatch=lambda r, p: calls.append((r, p)) or {}))
    assert not navigation.goto(client, 100, 64, 100)
    assert not any(r == "goto" for r, _ in calls)


def test_requested_setting_without_application_does_not_start(monkeypatch):
    calls = []
    def dispatch(route, payload):
        calls.append((route, payload))
        if route == "settings":
            return {"value": "true"}
        return {"is_pathing": False}
    client = SimpleNamespace(_protected_home_anchor=(100, 64, 100),
                             transport=SimpleNamespace(dispatch=dispatch))
    monkeypatch.setattr(home_surface.time, "sleep", lambda *_a: None)
    assert not navigation.goto(client, 100, 64, 100)
    assert not any(r == "goto" for r, _ in calls)


def test_unverified_stop_keeps_digging_disabled():
    setting = "true"
    def dispatch(route, payload):
        nonlocal setting
        if route == "settings":
            setting = payload.get("value", setting)
            return {"value": setting}
        return {"is_pathing": True}
    client = SimpleNamespace(_protected_home_anchor=(100, 64, 100),
                             transport=SimpleNamespace(dispatch=dispatch))
    @home_surface.protect_home_route()
    def uncertain_route(client, x, y, z):
        return False
    assert not uncertain_route(client, 100, 64, 100)
    assert setting == "false"


def test_safe_movement_policy_is_verified_before_route_and_restored_after_stop():
    settings = {
        "allowBreak": "true",
        "allowParkour": "true",
        "maxFallHeightNoWater": "3",
    }
    calls = []

    def dispatch(route, payload):
        calls.append((route, payload))
        if route == "settings":
            name = payload.get("get", payload.get("set"))
            if "set" in payload:
                settings[name] = payload["value"]
            return {"key": name, "value": settings[name]}
        if route == "get_state":
            return {"is_pathing": False}
        return {}

    client = SimpleNamespace(
        transport=SimpleNamespace(dispatch=dispatch),
        _protected_home_anchor=(1, 64, 1),
    )

    @home_surface.protect_home_route(safe_movement=True)
    def route(client, x, y, z):
        assert settings == {
            "allowBreak": "false",
            "allowParkour": "false",
            "maxFallHeightNoWater": "1",
        }
        return True

    assert route(client, 1, 64, 1)
    assert settings == {
        "allowBreak": "true",
        "allowParkour": "true",
        "maxFallHeightNoWater": "3",
    }
    first_update = next(i for i, (r, p) in enumerate(calls) if r == "settings" and "set" in p)
    assert all(r == "settings" for r, _p in calls[:first_update])
    assert calls[first_update][1] == {"set": "allowBreak", "value": "false"}
    assert calls[-1][0] == "settings"


def test_safe_movement_settings_are_untouched_outside_the_home_guard():
    settings = {"allowBreak": "true", "allowParkour": "true", "maxFallHeightNoWater": "3"}
    calls = []

    def dispatch(route, payload):
        calls.append((route, payload))
        if route == "settings":
            name = payload.get("get", payload.get("set"))
            if "set" in payload:
                settings[name] = payload["value"]
            return {"key": name, "value": settings[name]}
        return {"is_pathing": False}

    client = SimpleNamespace(
        transport=SimpleNamespace(dispatch=dispatch),
        _protected_home_anchor=(100, 64, 100),
    )

    @home_surface.protect_home_route(safe_movement=True)
    def route(client, x, y, z):
        return True

    assert route(client, 200, 64, 200)
    assert not calls
    assert settings == {
        "allowBreak": "true",
        "allowParkour": "true",
        "maxFallHeightNoWater": "3",
    }


@pytest.mark.parametrize("bad", [None, "unknown", "NaN", "1.5"])
def test_safe_movement_unknown_or_malformed_prior_value_refuses_route(bad, monkeypatch):
    settings = {"allowBreak": "true", "allowParkour": "true", "maxFallHeightNoWater": bad}
    calls = []

    def dispatch(route, payload):
        calls.append((route, payload))
        if route == "settings":
            name = payload.get("get", payload.get("set"))
            return {"key": name, "value": settings[name]}
        return {"is_pathing": False}

    monkeypatch.setattr(home_surface.time, "sleep", lambda *_a: None)
    client = SimpleNamespace(
        transport=SimpleNamespace(dispatch=dispatch),
        _protected_home_anchor=(1, 64, 1),
    )

    @home_surface.protect_home_route(safe_movement=True)
    def route(client, x, y, z):
        pytest.fail("unsafe settings must not start movement")

    assert not route(client, 1, 64, 1)
    assert not any(
        r == "settings" and p.get("set") in {"allowParkour", "maxFallHeightNoWater"}
        for r, p in calls
    )
    assert settings["allowBreak"] == "true"


def test_safe_movement_lost_readback_refuses_route_and_restores_after_stop(monkeypatch):
    settings = {
        "allowBreak": "true",
        "allowParkour": "true",
        "maxFallHeightNoWater": "3",
    }
    calls = []

    def dispatch(route, payload):
        calls.append((route, payload))
        if route == "settings":
            name = payload.get("get", payload.get("set"))
            if "set" in payload and name != "allowParkour":
                settings[name] = payload["value"]
            return {"key": name, "value": settings[name]}
        if route == "get_state":
            return {"is_pathing": False}
        return {}

    monkeypatch.setattr(home_surface.time, "sleep", lambda *_a: None)
    client = SimpleNamespace(
        transport=SimpleNamespace(dispatch=dispatch),
        _protected_home_anchor=(1, 64, 1),
    )

    @home_surface.protect_home_route(safe_movement=True)
    def route(client, x, y, z):
        pytest.fail("movement must not start without the requested readback")

    assert not route(client, 1, 64, 1)
    assert settings == {
        "allowBreak": "true",
        "allowParkour": "true",
        "maxFallHeightNoWater": "3",
    }
    assert not any(
        r == "settings" and p.get("set") == "maxFallHeightNoWater" and p.get("value") == "1"
        for r, p in calls
    )


@pytest.mark.parametrize("pathing", [True, None])
def test_safe_movement_settings_stay_conservative_without_verified_stop(pathing):
    settings = {
        "allowBreak": "true",
        "allowParkour": "true",
        "maxFallHeightNoWater": "3",
    }

    def dispatch(route, payload):
        if route == "settings":
            name = payload.get("get", payload.get("set"))
            if "set" in payload:
                settings[name] = payload["value"]
            return {"key": name, "value": settings[name]}
        if route == "get_state":
            return {} if pathing is None else {"is_pathing": pathing}
        return {}

    client = SimpleNamespace(
        transport=SimpleNamespace(dispatch=dispatch),
        _protected_home_anchor=(1, 64, 1),
    )

    @home_surface.protect_home_route(safe_movement=True)
    def uncertain_route(client, x, y, z):
        return True

    assert not uncertain_route(client, 1, 64, 1)
    assert settings == {
        "allowBreak": "false",
        "allowParkour": "false",
        "maxFallHeightNoWater": "1",
    }


def test_death_propagates_through_home_route_cleanup():
    from baritone_client.common.tasks import PlayerDeathDetected
    client = SimpleNamespace(_protected_home_anchor=(100, 64, 100),
        transport=SimpleNamespace(dispatch=lambda r, p: {"value": "false"}))
    @home_surface.protect_home_route()
    def dead(client, x, y, z):
        raise PlayerDeathDetected("dead")
    with pytest.raises(PlayerDeathDetected):
        dead(client, 100, 64, 100)
