"""Tests for legacy block-list JSON schematic compatibility."""

import json

import pytest

from baritone_client.schematics import (
    LegacyJsonSchematicError,
    ParsedLegacySchematic,
    parse_legacy_json_schematic,
    parse_legacy_json_schematics,
)


def _legacy_payload_from_dict(payload: dict) -> str:
    return json.dumps(payload)


def test_parses_blocks_with_position_and_pos():
    payload = {
        "name": "mixed-coords",
        "path": "builds/mixed-coords.json",
        "blocks": [
            {"id": "minecraft:stone", "position": {"x": 1, "y": 2, "z": 3}},
            {"id": "dirt", "pos": [0, 1, 2]},
            {"material": "minecraft:air", "position": [0, 0, 0]},
        ],
    }
    schematic = parse_legacy_json_schematic(_legacy_payload_from_dict(payload))
    assert isinstance(schematic, ParsedLegacySchematic)
    positions = {(block.x, block.y, block.z): block.id for block in schematic.blocks}
    assert positions[(1, 2, 3)] == "minecraft:stone"
    assert positions[(0, 1, 2)] == "minecraft:dirt"
    assert positions[(0, 0, 0)] == "minecraft:air"


def test_namespace_is_applied_to_vanilla_ids_without_namespace():
    payload = {
        "name": "namespace-test",
        "path": "namespace-test.json",
        "blocks": [
            {"id": "stone", "pos": [0, 0, 0]},
            {"id": "minecraft:dirt", "pos": [1, 0, 0]},
        ],
    }
    schematic = parse_legacy_json_schematic(_legacy_payload_from_dict(payload))
    ids = [block.id for block in schematic.blocks]
    assert ids == ["minecraft:stone", "minecraft:dirt"]

    assert schematic.material_counts == {"minecraft:dirt": 1, "minecraft:stone": 1}


def test_rejects_duplicate_block_positions():
    payload = {
        "name": "dup-pos",
        "path": "dup-pos.json",
        "blocks": [
            {"id": "stone", "pos": [0, 0, 0]},
            {"id": "dirt", "position": [0, 0, 0]},
        ],
    }
    with pytest.raises(LegacyJsonSchematicError, match="Duplicate position"):
        parse_legacy_json_schematic(_legacy_payload_from_dict(payload))


def test_rejects_duplicate_schematic_name_and_path():
    payload = [
        {
            "name": "castle",
            "path": "castle.json",
            "blocks": [{"id": "stone", "pos": [0, 0, 0]}],
        },
        {
            "name": "castle",
            "path": "other_castle.json",
            "blocks": [{"id": "stone", "pos": [1, 0, 0]}],
        },
    ]
    with pytest.raises(LegacyJsonSchematicError, match="Duplicate schematic name"):
        parse_legacy_json_schematics(json.dumps(payload))


def test_rejects_duplicate_path_with_same_name_and_payload():
    payload = [
        {
            "name": "castle",
            "path": "duplicate/path.json",
            "blocks": [{"id": "stone", "pos": [0, 0, 0]}],
        },
        {
            "name": "tower",
            "path": "duplicate/path.json",
            "blocks": [{"id": "stone", "pos": [1, 0, 0]}],
        },
    ]
    with pytest.raises(LegacyJsonSchematicError, match="Duplicate schematic path"):
        parse_legacy_json_schematics(json.dumps(payload))


def test_applies_limits_and_blocks_over_limit_rejected():
    payload = {
        "name": "too-wide",
        "path": "too-wide.json",
        "limits": [1, 1, 1],
        "blocks": [
            {"id": "stone", "pos": [0, 0, 0]},
            {"id": "stone", "pos": [1, 0, 0]},
        ],
    }
    with pytest.raises(LegacyJsonSchematicError, match="exceed declared limits"):
        parse_legacy_json_schematic(_legacy_payload_from_dict(payload))

    tight = parse_legacy_json_schematic(
        _legacy_payload_from_dict(
            {
                "name": "just-wide",
                "path": "just-wide.json",
                "limits": [2, 1, 1],
                "blocks": [
                    {"id": "stone", "pos": [0, 0, 0]},
                    {"id": "stone", "pos": [1, 0, 0]},
                ],
            }
        )
    )
    assert tight.size == (2, 1, 1)
    assert tight.limits.x == 2


def test_nonzero_data_is_lossy_by_default_and_strict_when_requested():
    payload = {
        "name": "data-loss",
        "path": "data-loss.json",
        "blocks": [{"id": "stone", "pos": [0, 0, 0], "data": 5}],
    }
    lossy = parse_legacy_json_schematic(_legacy_payload_from_dict(payload))
    assert lossy.blocks[0].data == 0
    assert len(lossy.warnings) == 1
    assert "Lossy block-data normalization" in lossy.warnings[0]

    with pytest.raises(LegacyJsonSchematicError, match="non-zero data"):
        parse_legacy_json_schematic(
            _legacy_payload_from_dict(payload),
            strict_non_zero_data=True,
        )


def test_returns_deterministic_native_representation():
    payload = {
        "name": "deterministic",
        "path": "deterministic.json",
        "blocks": [
            {"id": "stone", "pos": [1, 0, 2]},
            {"id": "dirt", "pos": [0, 0, 2]},
            {"id": "stone", "pos": [0, 1, 0]},
        ],
    }
    schematic = parse_legacy_json_schematic(_legacy_payload_from_dict(payload))

    native_text = schematic.to_native_json()
    parsed = json.loads(native_text)
    assert parsed["name"] == "deterministic"
    assert parsed["format"] == "legacy-json/v1"
    assert [b["id"] for b in parsed["blocks"]] == ["minecraft:dirt", "minecraft:stone", "minecraft:stone"]
    assert parsed["blocks"][0]["y"] <= parsed["blocks"][1]["y"] <= parsed["blocks"][2]["y"]


def test_builds_safe_placement_plan_without_air_and_sorted():
    payload = {
        "name": "placement",
        "path": "placement.json",
        "blocks": [
            {"id": "minecraft:air", "pos": [1, 0, 0]},
            {"id": "stone", "pos": [0, 1, 0]},
            {"id": "dirt", "pos": [0, 0, 0]},
            {"id": "stone", "pos": [1, 0, 1]},
        ],
    }
    schematic = parse_legacy_json_schematic(_legacy_payload_from_dict(payload))

    plan = schematic.build_safe_placement_plan(origin=(10, 64, -2))
    assert len(plan.steps) == 3
    assert plan.steps[0].x == 10
    assert plan.steps[0].y == 64
    assert plan.steps[0].z == -2
    assert [step.id for step in plan.steps] == ["minecraft:dirt", "minecraft:stone", "minecraft:stone"]
    assert plan.command_plan()[0].startswith("setblock 10 64 -2 ")
