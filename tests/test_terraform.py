"""
Tests for common/terraform.py - the standalone terraforming helpers used by
terraform_forever.py (and, once wired in, phases/terraforming.py).

Uses the shared MockClient/MockTransport fixtures from conftest.py so no
Minecraft/bridge connection is needed.
"""

from unittest.mock import patch

from baritone_client.common.tasks import TaskResult

from baritone_client.common.terraform import (
    CHUNK_SIZE,
    chunk_ring_offsets,
    disk_chunk_offsets,
    terraform_chunk,
    terraform_ring,
    terraform_area,
)


def test_chunk_ring_offsets_center():
    assert list(chunk_ring_offsets(0)) == [(0, 0)]


def test_chunk_ring_offsets_ring1_has_8_cells_and_no_duplicates():
    ring1 = list(chunk_ring_offsets(1))
    assert len(ring1) == 8
    assert len(set(ring1)) == 8
    assert (0, 0) not in ring1
    # Every cell should be at Chebyshev distance exactly 1 from center.
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
    """disk(radius) should equal the union of ring(0..radius) with no duplicates."""
    radius = 3
    disk = set(disk_chunk_offsets(radius))
    from_rings = set()
    for r in range(radius + 1):
        ring_cells = set(chunk_ring_offsets(r))
        assert not (ring_cells & from_rings), f"ring {r} overlaps a previous ring"
        from_rings |= ring_cells
    assert disk == from_rings


def test_terraform_chunk_issues_expected_selection_and_fill_commands(mock_client):
    with patch("baritone_client.common.terraform.time.sleep"):
        result = terraform_chunk(
            mock_client, x=5, z=20, target_y=64,
            fill_block="minecraft:stone", clear_margin=10, fill_depth=5,
        )

    assert result.success, result.reason

    chat_messages = [
        payload["message"]
        for route, payload in mock_client.transport.calls
        if route == "chat"
    ]

    # Chunk origin should be rounded down to a multiple of CHUNK_SIZE.
    assert "#sel pos1 0 65 16" in chat_messages
    assert "#sel pos2 15 74 31" in chat_messages
    assert "#sel set air" in chat_messages

    assert "#sel pos1 0 59 16" in chat_messages
    assert "#sel pos2 15 64 31" in chat_messages
    assert "#sel set minecraft:stone" in chat_messages

    assert "#sel clear" in chat_messages


def test_terraform_ring_is_resumable_via_progress_dict(mock_client):
    progress = {}
    with patch("baritone_client.common.terraform.time.sleep"):
        # Simulate a run that stops after the first chunk.
        progress["next_index"] = 0
        offsets = list(chunk_ring_offsets(1))
        assert len(offsets) == 8

        # Manually process one chunk then bail, like an interrupted run would.
        terraform_chunk(mock_client, 0, 0, 64)
        progress["next_index"] = 1
        progress["total"] = 8

        seen_chunks = []
        result = terraform_ring(
            mock_client, center_x=0, center_z=0, target_y=64, ring=1,
            progress=progress,
            on_chunk_done=lambda done, total, coord: seen_chunks.append(coord),
        )

    assert result.success
    # Only the remaining 7 chunks (index 1..7) should have been processed.
    assert len(seen_chunks) == 7
    assert progress["next_index"] == 8


def test_terraform_area_reports_full_disk(mock_client):
    with patch("baritone_client.common.terraform.time.sleep"):
        result = terraform_area(mock_client, center_x=0, center_z=0, target_y=64, radius_chunks=1)

    assert result.success
    assert result.data["chunks"] == 9  # 3x3 disk


def test_terraform_area_skips_protected_chunks_and_checkpoints_them(mock_client):
    progress = {}
    protected = {(0, 0)}
    with patch(
        "baritone_client.common.terraform.terraform_chunk",
        return_value=TaskResult.ok("done"),
    ) as terraform:
        result = terraform_area(
            mock_client,
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
