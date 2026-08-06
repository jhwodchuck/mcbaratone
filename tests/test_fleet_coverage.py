import json
import os
from types import SimpleNamespace

from baritone_client.automator.end_readiness import FleetRole
from baritone_client.automator.fleet_coverage import (
    borrowed_specialty_roles,
    configured_specialty_roles,
)


def _controller(root, name, *, role="", heartbeat_at=None, health=20, food=20):
    controller = root / name / "controller"
    monitor = controller / "monitor"
    monitor.mkdir(parents=True)
    if role:
        (controller / "fleet-role.txt").write_text(role + "\n", encoding="utf-8")
    if heartbeat_at is not None:
        heartbeat = monitor / "heartbeat.jsonl"
        heartbeat.write_text(
            json.dumps({"health": health, "food": food, "dead": False}) + "\n",
            encoding="utf-8",
        )
        os.utime(heartbeat, (heartbeat_at, heartbeat_at))
    return controller


def test_one_live_bot_inherits_every_unstaffed_specialty(tmp_path):
    fleet = tmp_path / "runs" / "headlessmc"
    current = _controller(fleet, "Bot07")
    state = SimpleNamespace(checkpoint_dir=current)

    borrowed = borrowed_specialty_roles(
        state, FleetRole.END_RUNNER, now=1000.0
    )

    assert FleetRole.QUARTERMASTER in borrowed
    assert FleetRole.WOOD_SUPPLY in borrowed
    assert FleetRole.IRON_SUPPLY in borrowed
    assert FleetRole.VILLAGE_FOOD in borrowed


def test_fresh_dedicated_worker_removes_borrowed_duty(tmp_path):
    fleet = tmp_path / "runs" / "headlessmc"
    _controller(fleet, "Bot04", role="quartermaster", heartbeat_at=995.0)
    current = _controller(fleet, "Bot07")
    state = SimpleNamespace(checkpoint_dir=current)

    borrowed = borrowed_specialty_roles(
        state, FleetRole.END_RUNNER, now=1000.0
    )

    assert FleetRole.QUARTERMASTER not in borrowed


def test_stale_dedicated_worker_does_not_block_reduced_fleet_coverage(tmp_path):
    fleet = tmp_path / "runs" / "headlessmc"
    _controller(fleet, "Bot04", role="quartermaster", heartbeat_at=800.0)
    current = _controller(fleet, "Bot07")
    state = SimpleNamespace(checkpoint_dir=current)

    borrowed = borrowed_specialty_roles(
        state, FleetRole.END_RUNNER, now=1000.0
    )

    assert FleetRole.QUARTERMASTER in borrowed


def test_unsafe_dedicated_worker_does_not_count_as_specialty_coverage(tmp_path):
    fleet = tmp_path / "runs" / "headlessmc"
    _controller(
        fleet,
        "Bot04",
        role="quartermaster",
        heartbeat_at=995.0,
        health=2,
    )
    current = _controller(fleet, "Bot07")
    state = SimpleNamespace(checkpoint_dir=current)

    borrowed = borrowed_specialty_roles(
        state, FleetRole.END_RUNNER, now=1000.0
    )

    assert FleetRole.QUARTERMASTER in borrowed


def test_legacy_aternos_layout_also_borrows_missing_specialties(tmp_path):
    current = tmp_path / "runs" / "aternos" / "Bot07"
    (current / "monitor").mkdir(parents=True)
    state = SimpleNamespace(checkpoint_dir=current)

    borrowed = borrowed_specialty_roles(
        state, FleetRole.END_RUNNER, now=1000.0
    )

    assert FleetRole.QUARTERMASTER in borrowed


def test_profile_secondary_roles_are_recurring_and_not_borrowed(tmp_path):
    fleet = tmp_path / "runs" / "headlessmc"
    controller = _controller(
        fleet, "Bot04", role="quartermaster", heartbeat_at=995.0
    )
    (controller / "fleet-role-profile.json").write_text(
        '{"primary_role":"quartermaster","secondary_roles":["wood_supply","courier"],'
        '"on_call_roles":["recovery"]}\n',
        encoding="utf-8",
    )
    state = SimpleNamespace(checkpoint_dir=controller)

    configured = configured_specialty_roles(state, FleetRole.QUARTERMASTER)
    borrowed = borrowed_specialty_roles(
        state, FleetRole.QUARTERMASTER, now=1000.0
    )

    assert configured == (FleetRole.WOOD_SUPPLY,)
    assert FleetRole.WOOD_SUPPLY not in borrowed
