"""Chat must stay worth reading.

Chat is shared with a human, so volume is a real cost. Six bots refreshing a
standing problem every thirty seconds would emit hundreds of identical lines an
hour and train the reader to ignore the channel -- at which point the one line
that mattered is lost too.

These pin the three rules: only new need speaks, the same line never repeats
back to back, and the metric always survives the joke.
"""

from types import SimpleNamespace

from baritone_client.automator import aid_chat


def _signals(**overrides):
    values = {"food": 0, "health": 2.03, "nearby_hostiles": 8}
    values.update(overrides)
    return SimpleNamespace(**values)


def _request(kind="food", request_id="r1", is_new=True, detail="food 0<14"):
    return {
        "kind": kind, "request_id": request_id, "is_new": is_new,
        "detail": detail, "urgency": 90,
    }


class _Client:
    def __init__(self):
        self.said = []
        self.transport = SimpleNamespace(dispatch=self._dispatch)

    def _dispatch(self, route, payload):
        if route == "chat":
            self.said.append(payload["message"])
        return {}


def test_a_refreshed_need_stays_silent():
    """The whole anti-noise guarantee: a standing problem announces once."""
    state = SimpleNamespace(custom_data={})
    client = _Client()

    aid_chat.announce(client, state, _request(is_new=True), _signals(), now=1000.0)
    for tick in range(120):  # an hour of 30s ticks
        aid_chat.announce(
            client, state, _request(is_new=False), _signals(), now=1000.0 + tick * 30
        )

    assert len(client.said) == 1, client.said


def test_cooldown_blocks_a_bot_flapping_between_kinds():
    state = SimpleNamespace(custom_data={})
    client = _Client()

    aid_chat.announce(client, state, _request("food", "a"), _signals(), now=0.0)
    aid_chat.announce(client, state, _request("rescue", "b"), _signals(), now=10.0)
    aid_chat.announce(client, state, _request("clear_hostiles", "c"), _signals(), now=20.0)

    assert len(client.said) == 1


def test_speaking_resumes_after_the_quiet_period():
    state = SimpleNamespace(custom_data={})
    client = _Client()

    aid_chat.announce(client, state, _request("food", "a"), _signals(), now=0.0)
    aid_chat.announce(
        client, state, _request("food", "b"), _signals(),
        now=aid_chat.CHAT_COOLDOWN_SECONDS + 1,
    )

    assert len(client.said) == 2
    assert client.said[0] != client.said[1], "consecutive lines must differ"


def test_successive_needs_do_not_repeat_a_phrasing():
    state = SimpleNamespace(custom_data={})
    client = _Client()
    for index in range(4):
        aid_chat.announce(
            client, state, _request("food", f"r{index}"), _signals(),
            now=index * (aid_chat.CHAT_COOLDOWN_SECONDS + 1),
        )

    assert len(client.said) == 4
    assert len(set(client.said)) == 4, client.said


def test_the_metric_always_survives_the_joke():
    """A line that reads well but drops the number is a failed line."""
    signals = _signals(food=3, health=1.5, nearby_hostiles=11)
    for kind, needle in (
        ("food", "3/14"),
        ("rescue", "1.5"),
        ("clear_hostiles", "11"),
    ):
        for index in range(6):
            text = aid_chat.compose(_request(kind, f"seed{index}"), signals, [])
            assert needle in text, (kind, text)


def test_a_refresh_keeps_the_same_phrasing():
    """Re-rolling the joke on every refresh would be its own kind of noise."""
    request = _request("food", "stable-id")
    first = aid_chat.compose(request, _signals(), [])
    for _ in range(5):
        assert aid_chat.compose(request, _signals(), []) == first


def test_an_unknown_kind_still_says_something_useful():
    text = aid_chat.compose(
        _request("something_new", "r9", detail="stuck on rails"), _signals(), []
    )
    assert "stuck on rails" in text


def test_chat_failure_never_disturbs_scheduling():
    """Chat is decoration; a broken bridge must not raise into the scheduler."""
    class Broken:
        transport = SimpleNamespace(
            dispatch=lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("bridge down"))
        )

    state = SimpleNamespace(custom_data={})
    assert aid_chat.announce(Broken(), state, _request(), _signals(), now=0.0) is None


def test_a_failed_send_does_not_consume_the_cooldown():
    """Otherwise a transient bridge fault silently costs the real announcement."""
    class Broken:
        transport = SimpleNamespace(
            dispatch=lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("down"))
        )

    state = SimpleNamespace(custom_data={})
    aid_chat.announce(Broken(), state, _request(), _signals(), now=0.0)

    client = _Client()
    assert aid_chat.announce(client, state, _request(), _signals(), now=1.0) is not None
