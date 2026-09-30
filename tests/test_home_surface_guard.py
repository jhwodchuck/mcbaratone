"""Homeward surface routes must not accept the cave below home as arrival."""

from types import SimpleNamespace

import pytest

from baritone_client.common import combat, home_surface, navigation


@pytest.mark.parametrize("horizontal", [False, True])
def test_homeward_route_cancels_on_drop_before_defense_or_arrival(monkeypatch, horizontal):
    calls = []
    reads = 0
    setting = "true"

    def dispatch(route, payload, **kwargs):
        nonlocal reads, setting
        calls.append((route, payload))
        if route == "settings":
            if "set" in payload:
                setting = payload["value"]
            return {"key": "allowBreak", "value": setting}
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
    assert setting == "true"


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


def test_death_propagates_through_home_route_cleanup():
    from baritone_client.common.tasks import PlayerDeathDetected
    client = SimpleNamespace(_protected_home_anchor=(100, 64, 100),
        transport=SimpleNamespace(dispatch=lambda r, p: {"value": "false"}))
    @home_surface.protect_home_route()
    def dead(client, x, y, z):
        raise PlayerDeathDetected("dead")
    with pytest.raises(PlayerDeathDetected):
        dead(client, 100, 64, 100)
