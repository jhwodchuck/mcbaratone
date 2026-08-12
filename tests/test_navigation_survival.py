from types import SimpleNamespace

import pytest

from baritone_client.common import navigation, combat
from baritone_client.common.tasks import PlayerDeathDetected


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
            return {
                "health": 20,
                "food_level": 20,
                "block_position": {"x": 0, "y": 62, "z": 0},
            }
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


@pytest.mark.parametrize("horizontal", [False, True])
def test_navigation_propagates_player_death(horizontal):
    class DeadTransport:
        def __init__(self):
            self.calls = []

        def dispatch(self, route, payload):
            self.calls.append((route, dict(payload)))
            if route == "get_state":
                return {
                    "health": 0,
                    "is_dead": True,
                    "block_position": {"x": 0, "y": 64, "z": 0},
                }
            return {}

    transport = DeadTransport()
    client = SimpleNamespace(
        transport=transport,
        _navigation_defense_callback_active=True,
    )

    with pytest.raises(PlayerDeathDetected):
        if horizontal:
            navigation.goto_xz(client, 20, 0)
        else:
            navigation.goto(client, 20, 64, 0)
    assert transport.calls[-1] == ("cancel", {})
