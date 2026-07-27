from types import SimpleNamespace

from baritone_client.common import navigation, combat


class SubmergedNavTransport:
    """get_state reports a normal position; the head block is underwater."""

    def __init__(self):
        self.calls = []
        self.gotos = 0

    def dispatch(self, route, payload):
        self.calls.append((route, payload))
        if route == "goto":
            self.gotos += 1
        if route == "get_state":
            return {"health": 20, "block_position": {"x": 0, "y": 62, "z": 0}}
        if route == "get_block":
            return {"id": "minecraft:water"}
        return {}


def test_goto_surfaces_and_aborts_submerged_path_without_replay(monkeypatch):
    client = SimpleNamespace(
        transport=SubmergedNavTransport(), _submersion_ticks=1
    )
    surfaced = []
    monkeypatch.setattr(
        combat,
        "_surface_after_aquatic_hunt",
        lambda _c, **_k: surfaced.append(True) or True,
    )
    monkeypatch.setattr(navigation.time, "sleep", lambda _s: None)
    clock = iter([0.0, 1.0])
    monkeypatch.setattr(navigation.time, "time", lambda: next(clock))

    result = navigation.goto(client, 100, 62, 100, timeout=10, check_interval=0)

    assert result is False
    assert surfaced == [True]  # drowning reflex fired mid-path
    assert client.transport.gotos == 1
    assert client._last_navigation_survival_abort is True
