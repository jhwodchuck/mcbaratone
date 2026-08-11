"""
Tests for common/terraform.py, terraform_verify.py, and terraform_ledger.py --
the standalone terraforming helpers used by terraform_forever.py (and, once
wired in, phases/terraforming.py).

Uses a local FakeWorld/WorldTransport (tests/fakes/world_transport.py) rather
than conftest.py's MockTransport for anything that exercises verification.
MockTransport answers every unmodelled route with a permissive
{"status": "ok"}, so a chunk that was never actually filled reads as
"verified" -- which is the exact defect this whole module exists to catch.
See tests/fakes/__init__.py and plans/WORLD_TERRAFORM_PLAN.md section 2.
"""

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from baritone_client.common import terraform_verify as tv
from baritone_client.common.tasks import TaskResult
from baritone_client.common.terraform import (
    CHUNK_SIZE,
    CircuitBreaker,
    _migrate_ledger,
    chunk_ring_offsets,
    disk_chunk_offsets,
    sweep_pass,
    terraform_area,
    terraform_chunk,
    terraform_ring,
)

from tests.fakes.world_transport import (
    AIR_FAMILY,
    FakeWorld,
    WorldTransport,
    apply_except,
    apply_honestly,
    apply_then_reflow,
    apply_with_budget,
    never_starts,
    noop_then_apply,
    silent_noop,
)


@pytest.fixture(autouse=True)
def _no_real_sleeps():
    """select_region/fill/retry all sleep between chat commands and polls;
    none of that wall-clock time is meaningful in a test."""
    with patch("baritone_client.common.terraform.time.sleep"), \
         patch("baritone_client.common.terraform_verify.time.sleep"):
        yield


def _flat_world(cx=0, cz=0, target_y=64, size=32, default="minecraft:air"):
    """A world that is already correctly terraformed: solid up to and
    including target_y, air above, across a generous margin around the
    chunk so anchors/lattice/perimeter samples all land on real terrain."""
    world = FakeWorld(default_block=default)
    world.fill_box(cx - size, tv.WORLD_MIN_Y, cz - size, cx + size, target_y, cz + size, "minecraft:stone")
    return world


def _client(world, **kwargs):
    return SimpleNamespace(transport=WorldTransport(world, **kwargs))


# ---------------------------------------------------------------------------
# Pure geometry -- disk/ring offsets. Unchanged by verification; kept as-is.
# ---------------------------------------------------------------------------


def test_chunk_ring_offsets_center():
    assert list(chunk_ring_offsets(0)) == [(0, 0)]


def test_chunk_ring_offsets_ring1_has_8_cells_and_no_duplicates():
    ring1 = list(chunk_ring_offsets(1))
    assert len(ring1) == 8
    assert len(set(ring1)) == 8
    assert (0, 0) not in ring1
    assert all(max(abs(dx), abs(dz)) == 1 for dx, dz in ring1)


def test_chunk_ring_offsets_ring2_has_16_cells():
    ring2 = list(chunk_ring_offsets(2))
    assert len(ring2) == 16
    assert all(max(abs(dx), abs(dz)) == 2 for dx, dz in ring2)


def test_disk_chunk_offsets_radius1_is_full_3x3():
    disk = list(disk_chunk_offsets(1))
    assert len(disk) == 9
    assert set(disk) == {(dx, dz) for dx in (-1, 0, 1) for dz in (-1, 0, 1)}


def test_rings_partition_the_plane_with_no_overlap():
    radius = 3
    disk = set(disk_chunk_offsets(radius))
    from_rings = set()
    for r in range(radius + 1):
        ring_cells = set(chunk_ring_offsets(r))
        assert not (ring_cells & from_rings), f"ring {r} overlaps a previous ring"
        from_rings |= ring_cells
    assert disk == from_rings


# ---------------------------------------------------------------------------
# A. Pure sampler geometry
# ---------------------------------------------------------------------------


def test_sample_positions_stay_inside_the_target_chunk():
    clear, fill = tv.chunk_sample_positions(0, 0, target_y=64, clear_top=112, fill_bottom=52, profile="standard")
    for x, _y, z in clear + fill:
        assert 0 <= x <= 15 and 0 <= z <= 15


def test_clear_samples_above_target_and_fill_samples_at_or_below():
    clear, fill = tv.chunk_sample_positions(0, 0, target_y=64, clear_top=112, fill_bottom=52, profile="standard")
    assert all(y > 64 for _x, y, _z in clear)
    assert all(y <= 64 for _x, y, _z in fill)


def test_samples_always_include_four_corners_and_centre_column():
    clear, fill = tv.chunk_sample_positions(0, 0, target_y=64, clear_top=112, fill_bottom=52, profile="lean")
    fill_cols = {(x, z) for x, y, z in fill if y == 64}
    assert {(0, 0), (15, 0), (0, 15), (15, 15), (8, 8)} <= fill_cols


def test_samples_include_surface_layer_and_the_block_above_it():
    clear, fill = tv.chunk_sample_positions(0, 0, target_y=64, clear_top=112, fill_bottom=52, profile="lean")
    assert (8, 64, 8) in fill
    assert (8, 65, 8) in clear


def test_every_sampled_column_is_read_at_both_target_y_and_target_y_plus_one():
    """Pins the column-pair rule: solid(T) alone passes a chunk buried inside
    a mountain, air(T+1) alone passes a crater. Neither proves a platform."""
    clear, fill = tv.chunk_sample_positions(0, 0, target_y=64, clear_top=112, fill_bottom=52, profile="standard")
    clear_cols_at_t1 = {(x, z) for x, y, z in clear if y == 65}
    fill_cols_at_t = {(x, z) for x, y, z in fill if y == 64}
    assert clear_cols_at_t1 == fill_cols_at_t
    assert len(clear_cols_at_t1) > 9  # more than just the anchors


def test_lattice_guarantees_any_four_by_four_patch_is_hit():
    for attempt in range(4):
        cols = set(tv.lattice_columns(0, 0, attempt))
        for ox in range(13):
            for oz in range(13):
                patch = {(x, z) for x in range(ox, ox + 4) for z in range(oz, oz + 4)}
                assert patch & cols, (attempt, ox, oz)


def test_sample_positions_are_deterministic_for_a_fixed_chunk_and_attempt():
    a = tv.chunk_sample_positions(16, -320, 64, 112, 52, attempt=2, profile="standard")
    b = tv.chunk_sample_positions(16, -320, 64, 112, 52, attempt=2, profile="standard")
    assert a == b


def test_retry_attempt_shifts_the_lattice_phase():
    assert tv.lattice_columns(0, 0, 0) != tv.lattice_columns(0, 0, 1)
    # Anchors are not jittered -- they are the cross-chunk reference.
    a0, _ = tv.chunk_sample_positions(0, 0, 64, 112, 52, attempt=0, profile="lean")
    a1, _ = tv.chunk_sample_positions(0, 0, 64, 112, 52, attempt=1, profile="lean")
    assert a0 == a1  # "lean" is anchors-only, which never move


def test_sample_positions_handle_negative_chunk_coordinates():
    """The live target this module was built for is z=-304."""
    clear, fill = tv.chunk_sample_positions(-16, -304, 64, 112, 52, profile="lean")
    for x, _y, z in clear + fill:
        assert -16 <= x <= -1
        assert -304 <= z <= -289


def test_clear_top_never_exceeds_319():
    clear, _fill = tv.chunk_sample_positions(0, 0, target_y=79, clear_top=79 + 300, fill_bottom=67, profile="audit")
    assert max(y for _x, y, _z in clear) <= 319


def test_clear_top_argument_itself_is_clamped_to_319():
    """A caller passing an out-of-range clear_top (e.g. target_y+clear_margin
    with a large margin) must never cause a y=320 read -- that always reads
    void_air and would mark the chunk unverified forever."""
    clear, _fill = tv.chunk_sample_positions(0, 0, target_y=300, clear_top=500, fill_bottom=250, profile="lean")
    assert max(y for _x, y, _z in clear) <= 319


# ---------------------------------------------------------------------------
# B. Verified success
# ---------------------------------------------------------------------------


def test_terraform_chunk_succeeds_when_the_world_actually_changes():
    world = FakeWorld(default_block="minecraft:air")
    world.fill_box(-16, 40, -16, 31, 90, 31, "minecraft:stone")  # a hill; genuinely needs work
    client = _client(world, fill_policy=apply_honestly())

    result = terraform_chunk(client, 0, 0, target_y=64, fill_depth=12, clear_margin=30)

    assert result.success, result.reason
    assert result.data["verification"]["samples_checked"] > 0
    assert client.transport.route_count("get_block") > 0


def test_verified_success_leaves_the_chunk_genuinely_flat():
    world = FakeWorld(default_block="minecraft:air")
    world.fill_box(-16, 40, -16, 31, 90, 31, "minecraft:stone")
    client = _client(world, fill_policy=apply_honestly())

    result = terraform_chunk(client, 0, 0, target_y=64, fill_depth=12, clear_margin=30)

    assert result.success
    # Check the world directly -- independent of the production sampler.
    # terraform.py issues the bare command name ("air"), not "minecraft:air".
    for x in range(16):
        for z in range(16):
            assert world.get(x, 64, z) != "air"
            assert world.get(x, 65, z) == "air"


def test_verification_default_is_on():
    import inspect
    assert inspect.signature(terraform_chunk).parameters["verify"].default is True


def test_verification_can_be_disabled_only_explicitly():
    world = _flat_world()
    client = _client(world, fill_policy=apply_honestly())

    result = terraform_chunk(client, 0, 0, target_y=64, verify=False)

    assert result.success
    assert client.transport.route_count("get_block") == 0


def test_already_flat_chunk_passes_without_placing_anything():
    """"Already correct" and "no-op" are the same world state; the goal is
    flat ground, not proof of effort."""
    world = _flat_world()
    client = _client(world, fill_policy=silent_noop())

    result = terraform_chunk(client, 0, 0, target_y=64)

    assert result.success


# ---------------------------------------------------------------------------
# C. Fill side, including THE acceptance gate
# ---------------------------------------------------------------------------


def test_acceptance_silent_noop_fill_is_never_recorded_as_a_completed_chunk():
    """plans/WORLD_TERRAFORM_PLAN.md section 2. The builder goes idle, the
    selection commands are correct, and nothing moves -- the live shape of
    "out of materials". This must never be recorded done."""
    world = FakeWorld(default_block="minecraft:air")
    world.fill_box(0, 40, 0, 15, 62, 15, "minecraft:stone")  # tops at 62; target_y=64 genuinely needs fill
    client = _client(world, fill_policy=silent_noop())
    progress = {}

    result = terraform_area(
        client, center_x=0, center_z=0, target_y=64, radius_chunks=0,
        fill_block="minecraft:stone", fill_depth=12, clear_margin=10, progress=progress,
    )

    # (a) the OLD signal said done -- the builder really does go idle.
    assert all(r.get("is_pathing") is False for r in client.transport.state_reads[-3:])
    # (b) verdict
    assert result.success is False
    assert "unverified" in result.reason.lower() or "verif" in result.reason.lower() or "failed" in result.reason.lower()
    # (c) recorded failed, and -- load-bearing -- never done
    assert [0, 0] in progress["failed_chunks"]
    assert progress.get("completed_chunks", []) == []
    assert progress["chunks"]["0,0"]["status"] == "failed"
    assert result.data["chunks_completed"] == 0
    # (d) the verdict came from the world
    assert client.transport.route_count("get_block") >= 8
    assert world.get(8, 64, 8) == "minecraft:air"
    # (e) survives the checkpoint round trip
    assert json.loads(json.dumps(progress))["failed_chunks"] == [[0, 0]]


def test_partial_fill_leaving_air_at_a_sampled_block_fails():
    world = FakeWorld(default_block="minecraft:air")
    world.fill_box(-16, 40, -16, 31, 52, 31, "minecraft:stone")  # solid only well below target_y
    client = _client(world, fill_policy=apply_except([(8, 64, 8)]))

    result = terraform_chunk(client, 0, 0, target_y=64, fill_depth=20, clear_margin=10)

    assert result.success is False
    assert world.get(8, 64, 8) == "minecraft:air"


def test_fill_exhausting_its_material_budget_fails():
    world = FakeWorld(default_block="minecraft:air")
    world.fill_box(-16, 40, -16, 31, 90, 31, "minecraft:stone")
    client = _client(world, fill_policy=apply_with_budget(20))

    result = terraform_chunk(client, 0, 0, target_y=64, fill_depth=12, clear_margin=30)

    assert result.success is False


def test_zero_fill_blocks_held_short_circuits_before_issuing_the_fill():
    world = _flat_world()
    # Solid at/below target_y already (clear passes); nothing to fill anyway,
    # but the preflight must still gate on inventory rather than trust that.
    world.fill_box(-16, 40, -16, 31, 50, 31, "minecraft:air")  # a hole needing fill
    client = _client(world, fill_policy=apply_honestly(), inventory={"minecraft:stone": 0})

    result = terraform_chunk(client, 0, 0, target_y=64, fill_depth=20)

    assert result.success is False
    assert result.data.get("reason_class") == "insufficient_materials"
    assert client.transport.chat_count("#sel set minecraft:stone") == 0


def test_bedrock_in_the_fill_band_is_accepted():
    """The == fill_block predicate was rejected specifically because bedrock
    can never equal a chosen fill_block; this is that regression test."""
    world = FakeWorld(default_block="minecraft:air")
    world.fill_box(-16, tv.WORLD_MIN_Y, -16, 31, tv.WORLD_MIN_Y + 4, 31, "minecraft:bedrock")
    world.fill_box(-16, tv.WORLD_MIN_Y + 5, -16, 31, 64, 31, "minecraft:stone")
    client = _client(world, fill_policy=apply_honestly())

    result = terraform_chunk(client, 0, 0, target_y=64, fill_depth=30, clear_margin=10)

    assert result.success, result.reason


def test_gravel_over_a_void_at_the_band_bottom_fails():
    world = _flat_world()
    world.set(8, 52, 8, "minecraft:gravel")
    world.set(8, 51, 8, "minecraft:air")  # unsupported

    def policy(w, box, block, attempt):
        pass  # nothing changes; we are directly probing an already-bad world

    client = _client(world, fill_policy=policy)

    report = tv.verify_chunk_flat(client, 0, 0, target_y=64, fill_depth=12, clear_margin=10, bands=("fill",))

    assert report.verdict == "fail"
    assert any(f.get("position") == [8, 52, 8] for f in report.failures)


# ---------------------------------------------------------------------------
# D. Clear side
# ---------------------------------------------------------------------------


def test_tree_left_standing_above_target_y_fails():
    world = _flat_world()
    world.set(8, 65, 8, "minecraft:oak_log")
    client = _client(world, fill_policy=silent_noop())

    report = tv.verify_chunk_flat(client, 0, 0, target_y=64, clear_margin=10, bands=("clear",))

    assert report.verdict == "fail"
    failure = next(f for f in report.failures if f["position"] == [8, 65, 8])
    assert failure["observed"] == "oak_log"
    assert failure["stage"] == "clear"


def test_bedrock_above_target_y_fails_and_is_not_retried():
    world = _flat_world()
    world.set(0, 65, 0, "minecraft:bedrock")  # an anchor column -- always sampled
    client = _client(world, fill_policy=apply_honestly())

    result = terraform_chunk(client, 0, 0, target_y=64, clear_margin=10)

    assert result.success is False
    assert result.data.get("retryable") is False
    # The clear was only ever issued once -- a permanent verdict must not retry.
    assert client.transport.chat_count("#sel set air") == 1


def test_water_backfilling_a_cleared_chunk_is_not_treated_as_flat():
    world = FakeWorld(default_block="minecraft:air")
    world.fill_box(-16, 40, -16, 31, 90, 31, "minecraft:stone")
    client = _client(world, fill_policy=apply_then_reflow("minecraft:water"))
    progress = {}

    result = terraform_area(
        client, center_x=0, center_z=0, target_y=64, radius_chunks=0,
        clear_margin=30, fill_depth=12, progress=progress,
    )

    assert result.success is False
    assert [0, 0] not in progress.get("completed_chunks", [])


def test_regrowth_fails_in_strict_mode_but_passes_in_audit_mode():
    world = _flat_world()
    world.set(8, 65, 8, "minecraft:short_grass")
    client = _client(world)

    strict = tv.verify_chunk_flat(client, 0, 0, target_y=64, profile="lean", mode="strict", bands=("clear",))
    audit = tv.verify_chunk_flat(client, 0, 0, target_y=64, profile="lean", mode="audit", bands=("clear",))

    assert strict.verdict == "fail"
    assert audit.verdict == "pass"


# ---------------------------------------------------------------------------
# E. Retry and builder liveness
# ---------------------------------------------------------------------------


def test_fill_retries_and_succeeds_on_second_attempt():
    world = _flat_world()
    world.fill_box(-16, 40, -16, 31, 50, 31, "minecraft:air")  # needs a fill
    client = _client(world, fill_policy=noop_then_apply(after=1))

    result = terraform_chunk(client, 0, 0, target_y=64, fill_depth=20, clear_margin=10)

    assert result.success, result.reason


def test_retry_exhaustion_fails_after_max_attempts():
    world = _flat_world()
    world.fill_box(-16, 40, -16, 31, 50, 31, "minecraft:air")
    client = _client(world, fill_policy=silent_noop())

    result = terraform_chunk(
        client, 0, 0, target_y=64, fill_depth=20, clear_margin=10, verify_attempts=3,
    )

    assert result.success is False


def test_never_starting_builder_is_detected():
    """A rejected block id or refused selection: nothing changes, and the
    world (which still needs work) proves it -- not the wait signal alone."""
    world = FakeWorld(default_block="minecraft:air")
    world.fill_box(-16, 40, -16, 31, 90, 31, "minecraft:stone")  # a hill needing clearing
    client = _client(world, fill_policy=never_starts())

    result = terraform_chunk(client, 0, 0, target_y=64, timeout=5.0, clear_margin=30)

    assert result.success is False
    assert client.transport.route_count("get_block") > 0  # verification still ran


def test_selection_is_cleared_even_on_a_failed_chunk():
    world = _flat_world()
    world.set(0, 65, 0, "minecraft:bedrock")
    client = _client(world, fill_policy=apply_honestly())

    terraform_chunk(client, 0, 0, target_y=64, clear_margin=10)

    assert client.transport.chat_count("#sel clear") >= 1


# ---------------------------------------------------------------------------
# F. Observation failure -- the anti-false-pass tests
# ---------------------------------------------------------------------------


def test_all_void_air_reads_are_unverified_not_done_and_not_failed():
    """The single most important test in this file. void_air must never be
    treated as evidence in either direction -- an unvisited chunk must not
    silently outrank a real success."""
    world = FakeWorld(default_block="minecraft:void_air")
    client = _client(world, fill_policy=silent_noop())
    progress = {}

    result = terraform_area(client, center_x=0, center_z=0, target_y=64, radius_chunks=0, progress=progress)

    assert result.success is False
    assert [0, 0] not in progress.get("completed_chunks", [])
    assert [0, 0] not in progress.get("failed_chunks", [])
    assert progress["chunks"]["0,0"]["status"] == "unverified"


def test_positive_control_short_circuits_before_the_full_pass():
    world = FakeWorld(default_block="minecraft:void_air")
    client = _client(world)

    report = tv.verify_chunk_flat(client, 0, 0, target_y=64)

    assert report.verdict == "unverified"
    assert client.transport.route_count("get_block") <= 4


def test_get_block_response_without_an_id_is_not_treated_as_verified():
    """This is literally the pre-fix MockTransport behaviour -- a route that
    answers {"status": "ok"} with no "id" must never look like a pass."""
    class _NoIdTransport:
        def dispatch(self, route, payload=None):
            return {"status": "ok"}

    client = SimpleNamespace(transport=_NoIdTransport())
    report = tv.verify_chunk_flat(client, 0, 0, target_y=64)

    assert report.verdict != "pass"


def test_unreadable_block_does_not_consume_a_retry_attempt():
    class _RaisingTransport:
        def __init__(self):
            self.calls = 0

        def dispatch(self, route, payload=None):
            self.calls += 1
            raise ConnectionError("bridge hiccup")

    client = SimpleNamespace(transport=_RaisingTransport())
    result = terraform_chunk(client, 0, 0, target_y=64, verify_attempts=3)

    assert result.success is False
    # A read failure must never look like a specific terrain verdict.
    assert result.data.get("reason_class") in ("unloaded", "bridge_error")


def test_unrecognised_solid_block_passes_and_is_counted():
    world = _flat_world()
    world.set(8, 64, 8, "minecraft:some_future_block_nobody_has_seen")
    client = _client(world)

    report = tv.verify_chunk_flat(client, 0, 0, target_y=64, profile="lean", bands=("fill",))

    assert report.verdict == "pass"


# ---------------------------------------------------------------------------
# G. Progress, resume, ring behaviour
# ---------------------------------------------------------------------------


def test_failed_chunk_is_retried_on_resume_and_never_enters_completed_chunks():
    world = FakeWorld(default_block="minecraft:air")
    world.fill_box(0, 40, 0, 15, 62, 15, "minecraft:stone")
    client = _client(world, fill_policy=silent_noop())
    progress = {}
    terraform_area(client, center_x=0, center_z=0, target_y=64, radius_chunks=0, fill_depth=10, clear_margin=10, progress=progress)
    assert progress["failed_chunks"] == [[0, 0]]

    # Round-trip through JSON like a real checkpoint, then let the fill
    # actually work on the resumed run.
    resumed = json.loads(json.dumps(progress))
    client.transport.fill_policy = apply_honestly()
    result = terraform_area(client, center_x=0, center_z=0, target_y=64, radius_chunks=0, fill_depth=10, clear_margin=10, progress=resumed)

    assert result.success
    assert resumed["failed_chunks"] == []
    assert resumed["completed_chunks"] == [[0, 0]]


def test_resume_does_not_reprocess_a_verified_completed_chunk():
    world = _flat_world()
    client = _client(world, fill_policy=apply_honestly())
    progress = {}
    terraform_area(client, center_x=0, center_z=0, target_y=64, radius_chunks=0, progress=progress)
    assert progress["completed_chunks"] == [[0, 0]]

    client.transport.chat_messages.clear()
    terraform_area(client, center_x=0, center_z=0, target_y=64, radius_chunks=0, progress=progress)

    assert client.transport.chat_count("#sel set minecraft:stone") == 0


def test_ring_continues_past_a_failed_chunk_and_reports_it():
    calls = []

    def fake_chunk(client, x, z, target_y, **kw):
        calls.append((x, z))
        if (x, z) == (-16, -16):
            return TaskResult.fail("nope", chunk=(-16, -16), retryable=True, reason_class="fill_incomplete")
        return TaskResult.ok("ok", chunk=((x // 16) * 16, (z // 16) * 16),
                              verification={"samples_checked": 10, "samples_failed": 0})

    with patch("baritone_client.common.terraform.terraform_chunk", fake_chunk):
        progress = {}
        result = terraform_ring(SimpleNamespace(), center_x=0, center_z=0, target_y=64, ring=1, progress=progress)

    assert result.success is False
    assert result.data["chunks_completed"] == 7
    assert progress["next_index"] == 8
    assert [-16, -16] in progress["failed_chunks"]


def test_three_consecutive_failures_trip_the_circuit_breaker():
    def always_fail(client, x, z, target_y, **kw):
        return TaskResult.fail("nope", chunk=((x // 16) * 16, (z // 16) * 16), retryable=True, reason_class="fill_incomplete")

    calls = []
    def counting_fail(client, x, z, target_y, **kw):
        calls.append((x, z))
        return always_fail(client, x, z, target_y, **kw)

    with patch("baritone_client.common.terraform.terraform_chunk", counting_fail):
        result = terraform_area(SimpleNamespace(), center_x=0, center_z=0, target_y=64, radius_chunks=2, progress={})

    assert result.data.get("abort_scope") == "lap"
    assert len(calls) == 3


def test_circuit_breaker_counter_resets_on_a_done_chunk():
    # fail, fail, done, fail, fail: the breaker's threshold is 3 consecutive,
    # so this must NOT trip if (and only if) the "done" in the middle resets
    # the counter. A 9-chunk disk gives room for all 5 scripted outcomes plus
    # 4 more (which just keep succeeding).
    outcomes = iter([False, False, True, False, False])

    def scripted(client, x, z, target_y, **kw):
        origin = ((x // 16) * 16, (z // 16) * 16)
        ok = next(outcomes, True)
        if ok:
            return TaskResult.ok("ok", chunk=origin, verification={"samples_checked": 1, "samples_failed": 0})
        return TaskResult.fail("nope", chunk=origin, retryable=True, reason_class="fill_incomplete")

    with patch("baritone_client.common.terraform.terraform_chunk", scripted):
        result = terraform_area(SimpleNamespace(), center_x=0, center_z=0, target_y=64, radius_chunks=1, progress={})

    assert result.data.get("abort_scope") != "lap"


def test_blacklisted_chunk_is_not_reattempted_on_a_second_lap():
    world = _flat_world()
    world.set(0, 65, 0, "minecraft:bedrock")
    client = _client(world, fill_policy=apply_honestly())
    progress = {}
    terraform_area(client, center_x=0, center_z=0, target_y=64, radius_chunks=0, clear_margin=10, progress=progress)
    assert progress["chunks"]["0,0"]["retryable"] is False

    client.transport.chat_messages.clear()
    terraform_area(client, center_x=0, center_z=0, target_y=64, radius_chunks=0, clear_margin=10, progress=progress)

    assert client.transport.chat_count("#sel set air") == 0


def test_progress_dict_is_json_serialisable_after_a_failure():
    world = FakeWorld(default_block="minecraft:air")
    world.fill_box(0, 40, 0, 15, 62, 15, "minecraft:stone")
    client = _client(world, fill_policy=silent_noop())
    progress = {}

    terraform_area(client, center_x=0, center_z=0, target_y=64, radius_chunks=0, fill_depth=10, clear_margin=10, progress=progress)

    json.dumps(progress)  # must not raise


def test_v1_checkpoint_migrates_and_marks_nothing_done():
    v1 = {"next_index": 2, "total": 9, "completed_chunks": [[0, 0], [16, 0]]}
    ledger = _migrate_ledger(v1)

    assert v1["schema_version"] == 2
    assert ledger["0,0"]["status"] == "unverified"
    assert ledger["16,0"]["status"] == "unverified"


def test_skipped_chunk_is_recorded_skipped_not_done():
    world = _flat_world()
    client = _client(world, fill_policy=apply_honestly())
    progress = {}

    terraform_area(
        client, center_x=0, center_z=0, target_y=64, radius_chunks=0,
        progress=progress, skip_chunks={(0, 0)},
    )

    assert progress["chunks"]["0,0"]["status"] == "skipped"
    assert [0, 0] not in progress.get("completed_chunks", [])


def test_hoisted_ledger_scopes_ring_completion_to_its_own_chunks():
    """A shared ledger across many rings must not let one ring's old failure
    make every later ring report incomplete forever."""
    def fake_chunk(client, x, z, target_y, **kw):
        if (x, z) == (0, 0):
            return TaskResult.fail("nope", chunk=(0, 0), retryable=False, reason_class="unbreakable")
        return TaskResult.ok("ok", chunk=((x // 16) * 16, (z // 16) * 16),
                              verification={"samples_checked": 1, "samples_failed": 0})

    with patch("baritone_client.common.terraform.terraform_chunk", fake_chunk):
        shared_ledger = {}
        r0 = terraform_ring(SimpleNamespace(), 0, 0, 64, ring=0, progress={}, ledger=shared_ledger)
        r1 = terraform_ring(SimpleNamespace(), 0, 0, 64, ring=1, progress={}, ledger=shared_ledger)

    assert r0.success is False
    assert r1.success is True
    assert "0,0" in shared_ledger  # still recorded, just not blocking ring 1


# ---------------------------------------------------------------------------
# H. #sel command-string coverage (rewritten to use WorldTransport)
# ---------------------------------------------------------------------------


def test_terraform_chunk_issues_expected_selection_and_fill_commands():
    world = FakeWorld(default_block="minecraft:air")
    world.fill_box(-16, 40, -16, 31, 90, 31, "minecraft:stone")
    client = _client(world, fill_policy=apply_honestly())

    result = terraform_chunk(client, x=5, z=20, target_y=64, fill_block="minecraft:stone", clear_margin=10, fill_depth=5)

    assert result.success, result.reason
    messages = client.transport.chat_messages

    assert "#sel pos1 0 65 16" in messages
    assert "#sel pos2 15 74 31" in messages
    assert "#sel set air" in messages

    assert "#sel pos1 0 59 16" in messages
    assert "#sel pos2 15 64 31" in messages
    assert "#sel set minecraft:stone" in messages

    assert "#sel clear" in messages
    assert client.transport.route_count("get_block") > 0


def test_terraform_ring_is_resumable_via_progress_dict():
    calls = []

    def fake_chunk(client, x, z, target_y, **kw):
        calls.append((x, z))
        return TaskResult.ok("ok", chunk=((x // 16) * 16, (z // 16) * 16),
                              verification={"samples_checked": 1, "samples_failed": 0})

    with patch("baritone_client.common.terraform.terraform_chunk", fake_chunk):
        progress = {"next_index": 1, "total": 8}
        result = terraform_ring(SimpleNamespace(), center_x=0, center_z=0, target_y=64, ring=1, progress=progress)

    assert result.success
    assert len(calls) == 7  # index 1..7; index 0 was already "done" via cursor skip...
    assert progress["next_index"] == 8


def test_terraform_area_reports_full_disk():
    def fake_chunk(client, x, z, target_y, **kw):
        return TaskResult.ok("ok", chunk=((x // 16) * 16, (z // 16) * 16),
                              verification={"samples_checked": 1, "samples_failed": 0})

    with patch("baritone_client.common.terraform.terraform_chunk", fake_chunk):
        result = terraform_area(SimpleNamespace(), center_x=0, center_z=0, target_y=64, radius_chunks=1, progress={})

    assert result.success
    assert result.data["chunks"] == 9


def test_terraform_area_skips_protected_chunks_and_checkpoints_them():
    progress = {}
    protected = {(0, 0)}
    with patch(
        "baritone_client.common.terraform.terraform_chunk",
        return_value=TaskResult.ok("done", verification={"samples_checked": 1, "samples_failed": 0}),
    ) as terraform:
        result = terraform_area(
            SimpleNamespace(),
            center_x=0,
            center_z=0,
            target_y=64,
            radius_chunks=1,
            progress=progress,
            skip_chunks=protected,
        )

    assert result.success
    assert terraform.call_count == 8
    assert result.data["chunks_completed"] == result.data["chunks_total"] == 8
    assert result.data["skipped_chunks"] == [[0, 0]]
    assert progress["next_index"] == progress["total"] == 9
