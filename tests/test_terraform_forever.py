"""
Tests for terraform_forever.py's own logic -- distinct from
tests/test_terraform.py, which covers the terraform/terraform_ledger/
terraform_verify library functions this script drives.

Bug 10 (adversarial review, 2026-08-10): commit 9b63079's message claimed
the main loop's abort handling was "driven... against scripted outcomes",
but that verification was done ad-hoc via a throwaway script during
implementation and never committed as an actual test file -- so a
regression to that handling (or to Bug 7's later sweep_failed_ledger fix)
could ship silently. The main()-loop tests below mock terraform_ring and
sweep_pass to script abort/success sequences, bounded by raising
KeyboardInterrupt (which main() already treats as a clean shutdown) once
the scenario under test has run its course.
"""

import json
import sys
from unittest.mock import MagicMock, patch

import pytest

import terraform_forever as tf
from baritone_client.common.tasks import TaskResult


def _stub_client():
    client = MagicMock()
    client.shutdown = MagicMock()
    return client


@pytest.fixture(autouse=True)
def _isolated_checkpoint(tmp_path, monkeypatch):
    """Never touch the real repo-root terraform_checkpoint.json -- it may be
    an in-progress live run's own state."""
    monkeypatch.setattr(tf, "CHECKPOINT_FILE", str(tmp_path / "terraform_checkpoint.json"))


@pytest.fixture(autouse=True)
def _no_real_sleeps(monkeypatch):
    calls = []
    monkeypatch.setattr(tf.time, "sleep", lambda seconds: calls.append(seconds))
    return calls


# ---------------------------------------------------------------------------
# A. _checkpoint_position / mission_is_complete -- pure correlation logic
# (Bug 2: a COMPLETE checkpoint used to be trusted with no proof it belongs
# to the world currently at --host/--port. Manually verified live against
# this repo's own runs/ tree at the time, but never pinned as a test.)
# ---------------------------------------------------------------------------


def test_checkpoint_position_reads_the_campaign_schema():
    data = {
        "phase": "COMPLETE",
        "position": {"x": 100, "y": 64, "z": -50},
        "world_identity": "minecraft:overworld",
    }
    assert tf._checkpoint_position(data) == ("minecraft:overworld", 100, 64, -50)


def test_checkpoint_position_reads_the_live_ops_schema():
    data = {
        "phase": "COMPLETE",
        "leader_snapshot": {
            "dimension": "minecraft:the_end",
            "position": {"x": 1, "y": 70, "z": 2},
        },
    }
    assert tf._checkpoint_position(data) == ("minecraft:the_end", 1, 70, 2)


def test_checkpoint_position_returns_none_for_an_unrecognised_shape():
    assert tf._checkpoint_position({"phase": "COMPLETE"}) is None


def test_mission_is_complete_without_live_position_trusts_any_complete_checkpoint(tmp_path):
    """The offline pre-check (no bridge connected yet, so no position to
    correlate against) -- same permissive behavior as before Bug 2."""
    path = tmp_path / "checkpoint.json"
    path.write_text(json.dumps({"phase": "COMPLETE", "position": {"x": 0, "y": 64, "z": 0}}))

    assert tf.mission_is_complete(str(path)) is True


def test_mission_is_complete_correlates_a_nearby_live_position(tmp_path):
    path = tmp_path / "checkpoint.json"
    path.write_text(json.dumps({
        "phase": "COMPLETE",
        "position": {"x": 100, "y": 64, "z": 100},
        "world_identity": "minecraft:overworld",
    }))

    live_position = ("minecraft:overworld", 200, 64, 100)  # 100 blocks away
    assert tf.mission_is_complete(str(path), live_position=live_position) is True


def test_mission_is_complete_rejects_a_checkpoint_far_from_the_live_bot(tmp_path):
    """Bug 2 regression: this is the exact failure reproduced live against
    this repo -- a COMPLETE checkpoint belonging to a different bot/world
    must not authorize terraforming wherever the CURRENTLY connected bot
    happens to be standing."""
    path = tmp_path / "checkpoint.json"
    path.write_text(json.dumps({
        "phase": "COMPLETE",
        "position": {"x": 0, "y": 64, "z": 0},
        "world_identity": "minecraft:overworld",
    }))

    far_away = ("minecraft:overworld", 50_000, 64, 50_000)
    assert tf.mission_is_complete(str(path), live_position=far_away) is False


def test_mission_is_complete_rejects_a_checkpoint_in_a_different_dimension(tmp_path):
    path = tmp_path / "checkpoint.json"
    path.write_text(json.dumps({
        "phase": "COMPLETE",
        "position": {"x": 0, "y": 64, "z": 0},
        "world_identity": "minecraft:the_end",
    }))

    same_coords_other_dimension = ("minecraft:overworld", 0, 64, 0)
    assert tf.mission_is_complete(str(path), live_position=same_coords_other_dimension) is False


def test_mission_is_complete_false_when_phase_is_not_complete(tmp_path):
    path = tmp_path / "checkpoint.json"
    path.write_text(json.dumps({"phase": "BASE_CONSTRUCTION", "position": {"x": 0, "y": 64, "z": 0}}))

    assert tf.mission_is_complete(str(path)) is False


# ---------------------------------------------------------------------------
# B. main()'s loop -- abort handling and backoff (Bug 7, Bug 10)
# ---------------------------------------------------------------------------


def _argv(*extra):
    return [
        "terraform_forever.py", "--force",
        "--center-x", "0", "--center-z", "0", "--target-y", "64",
        "--sweep-failed-every", "0",
        *extra,
    ]


def test_ring_abort_backs_off_and_retries_the_same_ring_without_advancing(tmp_path, _no_real_sleeps):
    ring_calls = []

    def scripted_ring(client, center_x, center_z, target_y, *, ring, **kw):
        ring_calls.append(ring)
        if len(ring_calls) == 1:
            return TaskResult.fail(
                "circuit breaker tripped", abort_scope="lap",
                reason_class="insufficient_materials",
            )
        if len(ring_calls) == 2:
            return TaskResult.ok(
                "ring complete", chunks_completed=1, chunks_total=1,
                sweep_complete=True, progress_complete=True,
            )
        raise KeyboardInterrupt()

    with patch.object(sys, "argv", _argv()), \
         patch("terraform_forever.TcpTransport"), \
         patch("terraform_forever.Client", return_value=_stub_client()), \
         patch("terraform_forever.terraform_ring", side_effect=scripted_ring):
        tf.main()

    # Same ring (0) on both the aborted attempt and its retry -- advancing
    # would spread the systemic failure across more chunks instead of
    # backing off and trying again, which is the exact bug commit 9b63079
    # fixed (result.success was never checked before advancing).
    assert ring_calls[:2] == [0, 0]
    assert _no_real_sleeps == [60.0]  # BACKOFF_SECONDS * 1 consecutive abort

    saved = json.loads((tmp_path / "terraform_checkpoint.json").read_text())
    assert saved["ring"] == 1  # only advanced after the SUCCESS, not the abort


def test_two_consecutive_ring_aborts_back_off_for_longer(tmp_path, _no_real_sleeps):
    def scripted_ring(client, center_x, center_z, target_y, *, ring, **kw):
        if len(scripted_ring.calls) >= 2:
            raise KeyboardInterrupt()
        scripted_ring.calls.append(ring)
        return TaskResult.fail(
            "circuit breaker tripped", abort_scope="lap",
            reason_class="bridge_error",
        )
    scripted_ring.calls = []

    with patch.object(sys, "argv", _argv()), \
         patch("terraform_forever.TcpTransport"), \
         patch("terraform_forever.Client", return_value=_stub_client()), \
         patch("terraform_forever.terraform_ring", side_effect=scripted_ring):
        tf.main()

    assert _no_real_sleeps == [60.0, 120.0]  # BACKOFF_SECONDS * (1, 2)


def test_sweep_failed_ledger_abort_backs_off_without_blocking_ring_progress(tmp_path, _no_real_sleeps):
    """Bug 7 regression: sweep_failed_ledger's own sweep_pass return value
    used to be discarded entirely -- a circuit-breaker trip mid-sweep (e.g.
    the bot died on chunk 1 of 40 accumulated failures) left the main loop
    completely unaware, so it proceeded straight into the next ring against
    what might still be a dead bot, with no backoff and no recorded error.
    """
    checkpoint_path = tmp_path / "terraform_checkpoint.json"
    checkpoint_path.write_text(json.dumps({
        "schema_version": 2,
        "center_x": 0, "center_z": 0, "target_y": 64, "ring": 0,
        "ring_progress": {},
        "chunks": {"16,0": {"status": "failed", "attempts": 1, "retryable": True}},
    }))

    ring_calls = []

    def scripted_ring(client, center_x, center_z, target_y, *, ring, **kw):
        ring_calls.append(ring)
        if len(ring_calls) >= 2:
            raise KeyboardInterrupt()
        return TaskResult.ok(
            "ring complete", chunks_completed=1, chunks_total=1,
            sweep_complete=True, progress_complete=True,
        )

    def scripted_sweep(client, origins, target_y, **kw):
        return TaskResult.fail(
            "Bot is dead mid-sweep", abort_scope="lap", reason_class="player_dead",
        )

    with patch.object(sys, "argv", _argv("--sweep-failed-every", "1")), \
         patch("terraform_forever.TcpTransport"), \
         patch("terraform_forever.Client", return_value=_stub_client()), \
         patch("terraform_forever.terraform_ring", side_effect=scripted_ring), \
         patch("baritone_client.common.terraform.sweep_pass", side_effect=scripted_sweep):
        tf.main()

    # Ring 0 succeeded and the loop moved on to ring 1 -- the sweep's own
    # abort must not block ring progress, only trigger its own backoff.
    assert ring_calls == [0, 1]
    assert _no_real_sleeps == [60.0]

    saved = json.loads(checkpoint_path.read_text())
    assert saved["last_error"] == "Bot is dead mid-sweep"


def test_sweep_failed_ledger_with_nothing_stale_does_not_back_off(tmp_path, _no_real_sleeps):
    """The common case: sweep_failed_every fires but the ledger has no
    retryable failures accumulated -- no sweep_pass call, no backoff."""
    def scripted_ring(client, center_x, center_z, target_y, *, ring, **kw):
        if len(scripted_ring.calls) >= 1:
            raise KeyboardInterrupt()
        scripted_ring.calls.append(ring)
        return TaskResult.ok(
            "ring complete", chunks_completed=1, chunks_total=1,
            sweep_complete=True, progress_complete=True,
        )
    scripted_ring.calls = []

    with patch.object(sys, "argv", _argv("--sweep-failed-every", "1")), \
         patch("terraform_forever.TcpTransport"), \
         patch("terraform_forever.Client", return_value=_stub_client()), \
         patch("terraform_forever.terraform_ring", side_effect=scripted_ring), \
         patch("baritone_client.common.terraform.sweep_pass") as sweep_mock:
        tf.main()

    sweep_mock.assert_not_called()
    assert _no_real_sleeps == []
