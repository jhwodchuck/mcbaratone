from types import SimpleNamespace

import pytest

from baritone_client.actions.boot_surface import BootSurfaceSafetyAction
from baritone_client.automator.phases import boot_sequence
from baritone_client.common.tasks import IncrementalProgressRequired, PlayerDeathDetected


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
    captured = []

    def fail_if_called(*_args, **_kwargs):
        captured.append(True)
        raise AssertionError("legacy ordered SequenceAction path should not execute")

    monkeypatch.setattr(boot_sequence, "SequenceAction", fail_if_called)

    state = SimpleNamespace(
        custom_data={},
        record_phase_payload=lambda *_args, **_kwargs: None,
    )
    handler = boot_sequence.BootSequenceHandler()
    transport = SimpleNamespace(
        dispatch=lambda route, _payload: {
            "dimension": "minecraft:overworld",
            "world_time": 1000,
            "health": 20,
            "food_level": 20,
            "block_position": {"x": 0, "y": 64, "z": 0},
        }
        if route == "get_state"
        else {"id": "minecraft:air"},
    )
    steps = []
    monkeypatch.setattr(
        handler,
        "_run_dry_anchor_step",
        lambda *args, **kwargs: steps.append("dry_anchor") or True,
    )
    monkeypatch.setattr(
        handler,
        "_enforce_anchor_and_pacing",
        lambda *_args, **_kwargs: None,
    )

    with pytest.raises(IncrementalProgressRequired):
        handler.execute(transport, SimpleNamespace(), state)
    assert steps == ["dry_anchor"]
    assert captured == []
