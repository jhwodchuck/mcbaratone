"""Critical-health travel gate regressions.

Bot07 was blown up by a creeper at 08:17 on 2026-08-06 after walking roughly
300 blocks pinned at exactly 2.1 health with food 5. Below food 18 Minecraft
grants no natural regeneration, so its health never moved a single point for
the entire journey -- the trip could only ever end in damage it could not undo.

The existing hold (``food_recovery.must_hold_for_critical_food``) only gates
blind food exploration, so ordinary phase and storage travel walked straight
past it. The gate now lives in the shared navigator.
"""

from types import SimpleNamespace

import pytest

from baritone_client.common import navigation


class NavTransport:
    def __init__(self, *, health=20.0, food=20, position=(0, 64, 0)):
        self.health = health
        self.food = food
        self.position = position
        self.calls = []

    def dispatch(self, route, payload=None, **_kwargs):
        payload = payload or {}
        self.calls.append((route, dict(payload)))
        if route == "get_state":
            x, y, z = self.position
            return {
                "health": self.health,
                "food": self.food,
                "block_position": {"x": x, "y": y, "z": z},
                "is_pathing": False,
            }
        return {}

    def routes(self):
        return [route for route, _p in self.calls]


def _client(**kwargs):
    return SimpleNamespace(transport=NavTransport(**kwargs))


def test_long_route_refused_at_critical_health_without_regen():
    """Bot07's exact fatal state: 2.1 health, food 5, ~300 blocks away."""
    client = _client(health=2.1, food=5, position=(0, 64, 0))

    assert navigation.goto(client, 300, 64, 0, timeout=1) is False
    assert "goto" not in client.transport.routes(), (
        "a route the bot cannot survive or heal during was still started"
    )


def test_short_route_still_allowed_at_critical_health():
    """Local moves must stay available; the bot still needs to reach food."""
    client = _client(health=2.1, food=5, position=(0, 64, 0))

    navigation.goto(client, 10, 64, 0, timeout=1)

    assert "goto" in client.transport.routes(), (
        "gate blocked a short local move and would strand the bot"
    )


def test_recovery_navigation_is_exempt():
    """Recovery is the path that fetches food; it must not be gated."""
    client = _client(health=2.1, food=5, position=(0, 64, 0))
    setattr(client, navigation._RECOVERY_DEPTH, 1)

    navigation.goto(client, 300, 64, 0, timeout=1)

    assert "goto" in client.transport.routes(), (
        "critical food recovery was blocked from travelling to its target"
    )


def test_long_route_allowed_when_regeneration_is_possible():
    """Low health with food >= 18 still regenerates, so travel is survivable."""
    client = _client(health=2.1, food=20, position=(0, 64, 0))

    navigation.goto(client, 300, 64, 0, timeout=1)

    assert "goto" in client.transport.routes()


def test_healthy_bot_travels_freely():
    client = _client(health=20.0, food=20, position=(0, 64, 0))

    navigation.goto(client, 300, 64, 0, timeout=1)

    assert "goto" in client.transport.routes()


def test_missing_telemetry_does_not_block_navigation():
    """Absent health/food must fail open, not strand every bot."""

    class BlankTransport(NavTransport):
        def dispatch(self, route, payload=None, **_kwargs):
            self.calls.append((route, dict(payload or {})))
            if route == "get_state":
                return {}
            return {}

    client = SimpleNamespace(transport=BlankTransport())

    navigation.goto(client, 300, 64, 0, timeout=1)

    assert "goto" in client.transport.routes()
