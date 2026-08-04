from types import SimpleNamespace

from tests.utils.mc_harness import actions


def test_incomplete_nearby_goto_cancels_pathing_without_name_error(monkeypatch):
    cancelled = []
    events = []
    ctx = SimpleNamespace(
        client=SimpleNamespace(
            transport=SimpleNamespace(dispatch=lambda *_args, **_kwargs: {})
        ),
        get_position=lambda: (0.0, 64.0, 0.0),
        log_event=events.append,
    )
    monkeypatch.setattr(actions, "wait_for_position_change", lambda *_a, **_k: True)
    monkeypatch.setattr(actions, "wait_for_pathing_stop", lambda *_a, **_k: False)
    monkeypatch.setattr(actions, "wait_for_arrival", lambda *_a, **_k: True)
    monkeypatch.setattr(actions, "cancel_pathing", lambda value: cancelled.append(value))

    assert actions.do_goto(
        ctx,
        {"x": 5, "y": 64, "z": 0},
        allow_incomplete=True,
    )
    assert cancelled == [ctx]
    assert "Movement incomplete but near destination" in events


def test_move_near_bails_immediately_when_player_is_dead(monkeypatch):
    """A corpse cannot move, but each candidate still burned a full goto
    timeout. Bot07/Bot08 ground through all 41 candidates after dying instead
    of surfacing the death, wasting minutes per cycle."""
    from tests.functional.shared import block_ops

    events = []
    ctx = SimpleNamespace(
        client=SimpleNamespace(
            transport=SimpleNamespace(dispatch=lambda *_a, **_k: {})
        ),
        get_state=lambda: {"health": 0.0},
        log_event=events.append,
    )
    monkeypatch.setattr(
        block_ops,
        "find_stand_positions",
        lambda *_a, **_k: [(1, 64, 1), (2, 64, 2), (3, 64, 3)],
    )

    def _must_not_run(*_a, **_k):
        raise AssertionError("do_goto must not be attempted while dead")

    monkeypatch.setattr(
        "tests.utils.mc_harness.actions.do_goto", _must_not_run
    )

    assert block_ops.move_near(ctx, 5, 64, 5) is False
    assert any("player is dead" in event for event in events)


def test_move_near_still_attempts_candidates_when_alive(monkeypatch):
    from tests.functional.shared import block_ops

    ctx = SimpleNamespace(
        client=SimpleNamespace(
            transport=SimpleNamespace(dispatch=lambda *_a, **_k: {})
        ),
        get_state=lambda: {"health": 20.0},
        log_event=lambda _e: None,
    )
    monkeypatch.setattr(
        block_ops, "find_stand_positions", lambda *_a, **_k: [(1, 64, 1)]
    )
    monkeypatch.setattr(
        "tests.utils.mc_harness.actions.do_goto", lambda *_a, **_k: True
    )
    monkeypatch.setattr(block_ops, "in_range", lambda *_a, **_k: True)

    assert block_ops.move_near(ctx, 5, 64, 5) is True


def test_move_near_stops_after_three_stationary_candidate_failures(monkeypatch):
    from tests.functional.shared import block_ops

    attempts = []
    events = []
    ctx = SimpleNamespace(
        client=SimpleNamespace(
            transport=SimpleNamespace(dispatch=lambda *_a, **_k: {})
        ),
        get_state=lambda: {"health": 20.0},
        get_position=lambda: (0.0, 64.0, 0.0),
        log_event=events.append,
    )
    monkeypatch.setattr(
        block_ops,
        "find_stand_positions",
        lambda *_a, **_k: [(n, 64, n) for n in range(1, 8)],
    )
    monkeypatch.setattr(block_ops, "in_range", lambda *_a, **_k: False)
    monkeypatch.setattr(block_ops, "cancel_pathing", lambda *_a, **_k: None)
    monkeypatch.setattr(
        "tests.utils.mc_harness.actions.do_goto",
        lambda *_a, **_k: attempts.append(True) or False,
    )

    assert block_ops.move_near(ctx, 40, 64, 40) is False
    assert len(attempts) == 3
    assert any("three candidates" in event for event in events)


def test_harness_ops_move_near_raises_player_death_for_the_controller(monkeypatch):
    """The harness returns False (right for the read-only suites); the
    adapter must escalate it so the automator enters death recovery instead
    of retrying movement on a corpse."""
    import pytest
    from baritone_client.common import harness_ops
    from baritone_client.common.tasks import PlayerDeathDetected

    client = SimpleNamespace(
        transport=SimpleNamespace(
            dispatch=lambda *_a, **_k: {"health": 0.0, "is_dead": True}
        )
    )
    monkeypatch.setattr(
        harness_ops, "_load", lambda: {"move_near": lambda *_a, **_k: False}
    )
    monkeypatch.setattr(harness_ops, "make_ctx", lambda _c: object())

    with pytest.raises(PlayerDeathDetected):
        harness_ops.move_near(client, 1, 2, 3)


def test_harness_ops_move_near_returns_false_when_alive_but_blocked(monkeypatch):
    """A plain unreachable target must stay a soft False, not a death."""
    from baritone_client.common import harness_ops

    client = SimpleNamespace(
        transport=SimpleNamespace(
            dispatch=lambda *_a, **_k: {"health": 20.0, "is_dead": False}
        )
    )
    monkeypatch.setattr(
        harness_ops, "_load", lambda: {"move_near": lambda *_a, **_k: False}
    )
    monkeypatch.setattr(harness_ops, "make_ctx", lambda _c: object())

    assert harness_ops.move_near(client, 1, 2, 3) is False


def test_harness_context_accepts_current_player_inventory_menu(monkeypatch):
    from baritone_client.common import harness_ops

    class Context:
        def __init__(self, client):
            self.client = client
            self.events = []

        def log_event(self, event):
            self.events.append(event)

    class Transport:
        def dispatch(self, route, _payload, **_kwargs):
            assert route == "get_screen"
            return {
                "type": "InventoryMenu",
                "slots": [{"slot": slot} for slot in range(46)],
            }

    command_facade = object()
    client = type(
        "Client",
        (),
        {"transport": Transport(), "command": command_facade},
    )()
    monkeypatch.setattr(harness_ops, "_load", lambda: {"TestContext": Context})

    context = harness_ops.make_ctx(client)
    screen = context.client.transport.dispatch("get_screen", {})

    assert screen["type"] == "PlayerScreenHandler"
    assert context.client.command is command_facade
