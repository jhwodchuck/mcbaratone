from types import SimpleNamespace

from baritone_client.common import navigation


def test_goto_fails_fast_when_goal_is_rejected_without_movement(monkeypatch):
    class Transport:
        def __init__(self):
            self.calls = []

        def dispatch(self, route, payload):
            self.calls.append((route, dict(payload)))
            if route == "get_state":
                return {
                    "health": 20,
                    "food_level": 20,
                    "is_pathing": False,
                    "block_position": {"x": -145, "y": 66, "z": -286},
                }
            return {}

    transport = Transport()
    client = SimpleNamespace(transport=transport)
    monkeypatch.setattr(navigation.time, "sleep", lambda _seconds: None)

    assert not navigation.goto(client, -160, 104, -388, timeout=300)

    state_reads = [call for call in transport.calls if call[0] == "get_state"]
    # Three route observations plus one fresh state sample per defense tick,
    # plus one pre-flight read for the critical-health travel gate. The gate
    # must decide before any movement starts, so it cannot reuse the loop's
    # first sample.
    assert len(state_reads) == 7
    assert transport.calls[-1][0] == "cancel"


def test_goto_cancels_when_defense_intervenes(monkeypatch):
    class Transport:
        def __init__(self):
            self.calls = []

        def dispatch(self, route, payload):
            self.calls.append((route, dict(payload)))
            if route == "get_state":
                return {
                    "health": 20,
                    "is_pathing": True,
                    "block_position": {"x": 0, "y": 64, "z": 0},
                }
            return {}

    transport = Transport()
    client = SimpleNamespace(transport=transport)
    defended = []

    assert not navigation.goto(
        client,
        20,
        64,
        0,
        on_defense=lambda: defended.append(True) or True,
    )
    assert defended == [True]
    assert transport.calls[-1] == ("cancel", {})


def test_recovery_goto_uses_recovery_aware_defense(monkeypatch):
    class Transport:
        def __init__(self):
            self.calls = []

        def dispatch(self, route, payload):
            self.calls.append((route, dict(payload)))
            if route == "get_state":
                return {
                    "health": 5,
                    "is_pathing": True,
                    "block_position": {"x": 20, "y": 64, "z": 0},
                }
            return {}

    client = SimpleNamespace(transport=Transport())
    checks = []
    monkeypatch.setattr(
        navigation,
        "recovery_navigation_defense",
        lambda checked: checks.append(checked) or False,
    )

    assert navigation.recovery_goto(client, 20, 64, 0)
    assert checks == [client]


def test_recovery_scope_marks_all_nested_default_routes(monkeypatch):
    from baritone_client.common import combat

    client = SimpleNamespace(transport=SimpleNamespace())
    calls = []
    monkeypatch.setattr(
        combat,
        "defend_or_flee",
        lambda checked, **kwargs: calls.append((checked, kwargs)) or False,
    )

    @navigation.allow_recovery_navigation
    def nested_recovery(active_client):
        return navigation.run_navigation_defense(active_client)

    assert not nested_recovery(client)
    assert calls == [
        (client, {"allow_safe_recovery_movement": True}),
    ]
    assert client._safe_recovery_navigation_depth == 0


def test_staged_goto_targets_loaded_column_surface(monkeypatch):
    class Transport:
        def dispatch(self, route, payload):
            if route == "get_block":
                return {
                    "id": (
                        "minecraft:water"
                        if payload["y"] <= 62
                        else "minecraft:air"
                    )
                }
            return {}

    destinations = []

    def navigate(_client, x, y, z, **_kwargs):
        destinations.append((x, y, z))
        return True

    assert navigation.staged_goto(
        SimpleNamespace(transport=Transport()),
        (-160, 104, -388),
        (-145, 66, -286),
        navigate=navigate,
    )

    assert destinations[0] == (-150, 63, -318)
    assert destinations[-1] == (-160, 104, -388)


def test_staged_goto_falls_back_to_y_agnostic_column(monkeypatch):
    class Transport:
        def dispatch(self, _route, _payload):
            return {}

    client = SimpleNamespace(transport=Transport())
    destinations = []
    attempts = {"count": 0}

    def navigate(_client, x, y, z, **_kwargs):
        attempts["count"] += 1
        destinations.append((x, y, z))
        return attempts["count"] > 1

    horizontal = []
    monkeypatch.setattr(
        navigation,
        "goto_xz",
        lambda _client, x, z, **_kwargs: horizontal.append((x, z)) or True,
    )
    monkeypatch.setattr(navigation, "_loaded_stage_y", lambda *_a, **_k: 70)

    assert navigation.staged_goto(
        client,
        (96, 90, 0),
        (0, 64, 0),
        maximum_leg=32,
        navigate=navigate,
    )
    assert horizontal == [(32, 0)]
    assert destinations[0] == (32, 70, 0)


def test_goto_xz_survives_a_slow_chat_reply(monkeypatch):
    """A slow '#goto' reply must not read as an unreachable destination.

    `#goto` is fire-and-forget: Baritone paths on receipt and the reply says
    nothing. On 2026-08-07 Bot17's farmer crash-looped because one
    "Timeout waiting for bridge response (route: chat)" became
    goto_xz -> False -> RuntimeError("crop farm is unreachable") -> worker
    exit. The farm was 14 blocks away and perfectly reachable; the same
    message had earlier been read as a real terrain problem.
    """
    from baritone_client.common import navigation

    positions = iter([
        {"x": 0.0, "y": 64.0, "z": 0.0},
        {"x": 5.0, "y": 64.0, "z": 5.0},
        {"x": 10.0, "y": 64.0, "z": 10.0},
    ])
    last = {"x": 10.0, "y": 64.0, "z": 10.0}

    class SlowChatTransport:
        def dispatch(self, route, _payload=None):
            if route == "chat":
                raise TimeoutError("Timeout waiting for bridge response (route: chat)")
            if route == "get_state":
                return {"block_position": next(positions, last), "is_pathing": True}
            return {}

    client = SimpleNamespace(transport=SlowChatTransport())
    monkeypatch.setattr(navigation.time, "sleep", lambda _s: None)
    monkeypatch.setattr(
        "baritone_client.common.combat.survival_tick", lambda *_a, **_k: False
    )

    arrived = navigation.goto_xz(client, 10, 10, timeout=30, tolerance=4.0)

    assert arrived is True, "a slow chat reply must not fail an otherwise fine walk"
