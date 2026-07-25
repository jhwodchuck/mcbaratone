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
