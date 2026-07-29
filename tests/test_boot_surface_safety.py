from types import SimpleNamespace

import pytest

from baritone_client.actions.boot_surface import BootSurfaceSafetyAction
from baritone_client.automator.phases import boot_sequence
from baritone_client.common.tasks import PlayerDeathDetected


def _context(position):
    transport = SimpleNamespace(
        dispatch=lambda route, _payload: {
            "block_position": {
                "x": position[0],
                "y": position[1],
                "z": position[2],
            },
            "dimension": "minecraft:overworld",
            "health": 20.0,
            "food_level": 20,
        }
        if route == "get_state"
        else {}
    )
    return SimpleNamespace(client=SimpleNamespace(transport=transport))


def test_boot_surfaces_before_optional_exploration(monkeypatch):
    context = _context((12, 17, 8))
    calls = []
    monkeypatch.setattr(
        "baritone_client.actions.boot_surface.require_survival_margin",
        lambda _client: None,
    )
    monkeypatch.setattr(
        "baritone_client.actions.boot_surface.reach_dry_surface",
        lambda _client, **kwargs: calls.append(kwargs) or (14, 64, 9),
    )

    result = BootSurfaceSafetyAction().execute(context)

    assert result.success
    assert calls[0]["origin"] == (12, 17, 8)
    assert calls[0]["expected_y"] == 63


def test_boot_refuses_exploration_when_surface_recovery_fails(monkeypatch):
    context = _context((12, 17, 8))
    monkeypatch.setattr(
        "baritone_client.actions.boot_surface.require_survival_margin",
        lambda _client: None,
    )
    monkeypatch.setattr(
        "baritone_client.actions.boot_surface.reach_dry_surface",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "baritone_client.actions.boot_surface.excavate_surface_egress",
        lambda *_args, **_kwargs: None,
    )

    result = BootSurfaceSafetyAction().execute(context)

    assert not result.success
    assert "safe surface terrain" in result.message


def test_boot_surface_action_is_noop_on_surface(monkeypatch):
    context = _context((12, 64, 8))
    monkeypatch.setattr(
        "baritone_client.actions.boot_surface.reach_dry_surface",
        lambda *_args, **_kwargs: pytest.fail("surface route must not run"),
    )

    assert BootSurfaceSafetyAction().execute(context).success


def test_navigation_survival_tick_raises_on_death_screen():
    from baritone_client.common.combat import survival_tick

    client = SimpleNamespace(transport=SimpleNamespace(dispatch=lambda *_args: {}))
    with pytest.raises(PlayerDeathDetected):
        survival_tick(client, {"health": 0.0, "is_dead": True})


def test_boot_sequence_gates_optional_exploration_on_surface(monkeypatch):
    captured_actions = []

    class CapturingSequence:
        def __init__(self, actions):
            captured_actions.extend(actions)

        def execute(self, _context):
            return SimpleNamespace(success=False, message="captured")

    monkeypatch.setattr(boot_sequence, "SequenceAction", CapturingSequence)
    result = boot_sequence.BootSequenceHandler().execute(
        SimpleNamespace(),
        SimpleNamespace(),
        SimpleNamespace(),
    )

    action_names = [type(action).__name__ for action in captured_actions]
    surface_index = action_names.index("BootSurfaceSafetyAction")
    assert surface_index < action_names.index("BaseRecoveryAction")
    assert surface_index < action_names.index("ConditionalWoodGatheringAction")
    assert surface_index < action_names.index("ConditionalAction")
    assert surface_index < action_names.index("HuntingAndScoutingAction")
    assert not result.success
