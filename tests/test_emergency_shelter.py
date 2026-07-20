import time
from unittest.mock import MagicMock

import pytest

from baritone_client.common import base


# ------------------------------------------------------------------------
# Helper: build a minimal client double
# ------------------------------------------------------------------------
def _make_transport(blocks):
    dispatch_calls = {"get_block": [], "place_block": [], "goto": [], "mine": []}

    def dispatch(route, payload=None):
        dispatch_calls[route].append(payload)
        if route == "get_block":
            x, y, z = payload["x"], payload["y"], payload["z"]
            return blocks.get((x, y, z), {"id": "minecraft:air"})
        if route == "place_block":
            return {"ok": True}
        if route == "goto":
            return {"ok": True}
        if route == "mine":
            return {"ok": True}
        if route == "get_state":
            return {"block_position": {"x": 0.0, "y": 64.0, "z": 0.0}}
        if route in ("close_screen", "cancel"):
            return {}
        return {}

    transport = MagicMock()
    transport.dispatch.side_effect = dispatch
    transport._calls = dispatch_calls
    return transport


def _make_client(blocks):
    client = MagicMock()
    client.transport = _make_transport(blocks)
    return client


# ------------------------------------------------------------------------
# _standing_in_liquid
# ------------------------------------------------------------------------
def test_standing_in_liquid_feet_water(monkeypatch):
    monkeypatch.setattr(base, "get_player_pos", lambda client: (0, 64, 0))
    client = _make_client({
        (0, 64, 0): {"id": "minecraft:water"},
        (0, 65, 0): {"id": "minecraft:air"},
    })
    assert base._standing_in_liquid(client) is True


def test_standing_in_liquid_head_lava(monkeypatch):
    monkeypatch.setattr(base, "get_player_pos", lambda client: (0, 64, 0))
    client = _make_client({
        (0, 64, 0): {"id": "minecraft:stone"},
        (0, 65, 0): {"id": "minecraft:lava"},
    })
    assert base._standing_in_liquid(client) is True


def test_standing_in_liquid_dry(monkeypatch):
    monkeypatch.setattr(base, "get_player_pos", lambda client: (0, 64, 0))
    client = _make_client({
        (0, 64, 0): {"id": "minecraft:air"},
        (0, 65, 0): {"id": "minecraft:air"},
    })
    assert base._standing_in_liquid(client) is False


# ------------------------------------------------------------------------
# establish_dry_footing – already dry
# ------------------------------------------------------------------------
def test_establish_dry_footing_already_dry(monkeypatch):
    monkeypatch.setattr(base, "get_player_pos", lambda client: (0, 64, 0))
    monkeypatch.setattr(base, "count_item", lambda client, it: 10)
    monkeypatch.setattr(base, "robust_place", MagicMock(return_value=True))
    monkeypatch.setattr(time, "sleep", MagicMock())

    client = _make_client({
        (0, 64, 0): {"id": "minecraft:air"},
        (0, 65, 0): {"id": "minecraft:air"},
    })
    assert base.establish_dry_footing(client) is True
    base.robust_place.assert_not_called()


# ------------------------------------------------------------------------
# establish_dry_footing – escapes after a few lifts
# ------------------------------------------------------------------------
def test_establish_dry_footing_escapes_liquid(monkeypatch):
    monkeypatch.setattr(base, "get_player_pos", lambda client: (0, 64, 0))
    monkeypatch.setattr(base, "count_item", lambda client, it: 10)
    monkeypatch.setattr(base, "robust_place", MagicMock(return_value=True))
    monkeypatch.setattr(time, "sleep", MagicMock())

    client = _make_client({
        (0, 64, 0): {"id": "minecraft:water"},
        (0, 65, 0): {"id": "minecraft:air"},
    })

    # Simulate liquid detection: after 3 calls to _standing_in_liquid it becomes dry.
    # (the function calls _standing_in_liquid once at start and once per lift)
    liquid_calls = [True, True, True, False]  # first call (dry check), then 3 placements
    monkeypatch.setattr(base, "_standing_in_liquid", lambda cl: liquid_calls.pop(0))

    result = base.establish_dry_footing(client)
    assert result is True
    # Should have placed exactly 3 blocks (3 lifts) before drying
    assert base.robust_place.call_count == 3


# ------------------------------------------------------------------------
# establish_dry_footing – bounded
# ------------------------------------------------------------------------
def test_establish_dry_footing_bounded(monkeypatch):
    max_lifts = 6
    monkeypatch.setattr(base, "get_player_pos", lambda client: (0, 64, 0))
    monkeypatch.setattr(base, "count_item", lambda client, it: 10)
    monkeypatch.setattr(base, "robust_place", MagicMock(return_value=True))
    monkeypatch.setattr(time, "sleep", MagicMock())

    client = _make_client({
        (0, 64, 0): {"id": "minecraft:water"},
        (0, 65, 0): {"id": "minecraft:water"},
    })

    # Always liquid
    monkeypatch.setattr(base, "_standing_in_liquid", lambda cl: True)

    result = base.establish_dry_footing(client, max_lifts)
    assert result is False
    assert base.robust_place.call_count == max_lifts


# ------------------------------------------------------------------------
# build_emergency_shelter calls establish_dry_footing first
# ------------------------------------------------------------------------
def test_build_emergency_shelter_calls_dry_footing_first(monkeypatch):
    call_order = []

    def tracking_establish(client):
        call_order.append("dry_footing")
        return True

    def tracking_dirt(client, x, y, z, size):
        call_order.append("build_dirt_shelter")
        return True

    # prevent actual place/wall code
    monkeypatch.setattr(base, "establish_dry_footing", tracking_establish)
    monkeypatch.setattr(base, "build_dirt_shelter", tracking_dirt)
    monkeypatch.setattr(base, "robust_place", MagicMock(return_value=True))
    monkeypatch.setattr(time, "sleep", MagicMock())

    # give enough dirt to trigger build_dirt_shelter branch
    monkeypatch.setattr(base, "count_item", lambda client, it: {"minecraft:dirt": 30}.get(it, 0))
    monkeypatch.setattr(base, "get_player_pos", lambda client: (0, 64, 0))

    client = MagicMock()
    transport = MagicMock()
    transport.dispatch = MagicMock(return_value={})
    client.transport = transport

    result = base.build_emergency_shelter(client)

    assert result is True
    assert call_order[0] == "dry_footing", (
        f"Expected establish_dry_footing to be called first, got {call_order}"
    )
