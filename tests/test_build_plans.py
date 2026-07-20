import pytest

from baritone_client.build_plans import (
    BuildAnchors,
    PreviewIssueSeverity,
    compile_plan_preview,
)


def test_parse_payload_wrapper_uses_default_version():
    result = compile_plan_preview(
        {
            "build_plan": {
                "blocks": [
                    {"pos": [1, 2, 3], "block": "stone", "name": "origin"},
                ]
            }
        }
    )
    assert result.valid
    assert result.valid_count == 1
    assert result.phase_count == 1
    assert result.transformed_bounds is not None
    assert result.transformed_bounds.min.x == 1
    assert result.transformed_bounds.max.z == 3
    assert result.error is None


def test_rotate_90_transforms_positions():
    result = compile_plan_preview(
        {
            "version": 2,
            "coordMode": "player",
            "rotation": 90,
            "blocks": [
                {"pos": [1, 0, 0], "block": "stone"},
            ],
        }
    )
    assert result.valid
    assert "setblock ~ ~ ~1 minecraft:stone" in result.commands


def test_palette_and_state_are_canonicalized_and_sorted():
    result = compile_plan_preview(
        {
            "version": 2,
            "coordMode": "player",
            "palette": {
                "plank": "oak_planks",
            },
            "blocks": [
                {"pos": [0, 0, 0], "block": "$plank", "properties": {"Facing": "North", "variant": "oak"}},
                {
                    "pos": [1, 0, 0],
                    "block": "stone[Variant=Granite,Layer=3]",
                    "properties": {"shape": "straight"},
                },
            ],
        }
    )
    assert result.valid
    assert result.commands[0] == "setblock ~ ~ ~ minecraft:oak_planks[facing=north,variant=oak]"
    assert result.commands[1] == "setblock ~1 ~ ~ minecraft:stone[layer=3,shape=straight,variant=granite]"


def test_hollow_cuboid_has_shell_material_count():
    result = compile_plan_preview(
        {
            "version": 2,
            "options": {"max_total_volume": 100},
            "cuboids": [
                {
                    "from": [0, 0, 0],
                    "to": [2, 2, 2],
                    "block": "stone",
                    "hollow": True,
                }
            ],
        }
    )
    assert result.valid
    assert result.material_counts == {"minecraft:stone": 26}
    assert result.total_volume == 26
    assert "hollow" in result.commands[0]


def test_nested_phase_order_preserved_by_default():
    result = compile_plan_preview(
        {
            "version": 2,
            "coordMode": "player",
            "steps": [
                {"phase": "z-phase", "plan": {"blocks": [{"pos": [1, 0, 0], "block": "stone"}]}},
                {"phase": "a-phase", "plan": {"blocks": [{"pos": [0, 0, 0], "block": "stone"}]}},
            ],
        }
    )
    assert result.valid
    assert result.commands[0].startswith("setblock ~1 ~ ~")
    assert result.commands[1].startswith("setblock ~ ~ ~")


def test_nested_phase_reorder_sorts_by_phase_name():
    result = compile_plan_preview(
        {
            "version": 2,
            "coordMode": "player",
            "options": {"phase_reorder": True},
            "steps": [
                {"phase": "z-phase", "plan": {"blocks": [{"pos": [1, 0, 0], "block": "stone"}]}},
                {"phase": "a-phase", "plan": {"blocks": [{"pos": [0, 0, 0], "block": "stone"}]}},
            ],
        }
    )
    assert result.valid
    assert result.commands[0].startswith("setblock ~ ~ ~")
    assert result.commands[1].startswith("setblock ~1 ~ ~")


def test_bare_child_step_without_plan_key_is_supported():
    result = compile_plan_preview(
        {
            "version": 2,
            "coordMode": "player",
            "steps": [
                {
                    "phase": "walls",
                    "cuboids": [
                        {"from": [0, 0, 0], "to": [0, 1, 0], "block": "stone"},
                    ],
                }
            ],
        }
    )
    assert result.valid
    assert len(result.commands) == 1
    assert result.commands[0] == "fill ~ ~ ~ ~ ~1 ~ minecraft:stone"


def test_nested_children_inherit_parent_defaults():
    result = compile_plan_preview(
        {
            "version": 2,
            "coordMode": "player",
            "rotation": 90,
            "palette": {"plank": "oak_planks"},
            "steps": [
                {
                    "phase": "child",
                    "blocks": [
                        {"pos": [1, 0, 0], "block": "$plank"},
                    ],
                }
            ],
        }
    )
    assert result.valid
    assert result.commands == ["setblock ~ ~ ~1 minecraft:oak_planks"]


def test_global_volume_limit_enforced_across_nested_steps():
    result = compile_plan_preview(
        {
            "version": 2,
            "options": {"max_total_volume": 3},
            "steps": [
                {"phase": "first", "plan": {"blocks": [
                    {"pos": [0, 0, 0], "block": "stone"},
                    {"pos": [1, 0, 0], "block": "stone"},
                ]}},
                {"phase": "second", "plan": {"blocks": [
                    {"pos": [2, 0, 0], "block": "stone"},
                    {"pos": [3, 0, 0], "block": "stone"},
                ]}},
            ],
        }
    )
    assert not result.valid
    assert any(issue.code == "volume_limit" for issue in result.issues)
    assert result.total_volume == 4
    # The volume in the child phase should still be deterministic even after breach.
    assert result.total_volume == len(result.commands)


def test_invalid_tokens_become_deterministic_issues():
    result = compile_plan_preview(
        {
            "version": 2,
            "blocks": [
                {"pos": [0, 0, 0], "block": "minecraft:stone[axis]"},
            ],
        }
    )
    assert not result.valid
    assert result.issues
    assert any(issue.code in {"invalid_block_token", "invalid_plan"} for issue in result.issues)
    assert any(issue.severity == PreviewIssueSeverity.ERROR for issue in result.issues)
    assert result.error is not None
    assert result.commands


def test_preview_returns_commands_only_read_only():
    result = compile_plan_preview(
        {
            "version": 2,
            "blocks": [
                {"pos": [0, 0, 0], "block": "stone"},
            ],
        }
    )
    assert result.valid
    assert all(isinstance(command, str) for command in result.commands)
    assert all(command.startswith("setblock") or command.startswith("fill") for command in result.commands)


def test_nested_step_missing_plan_returns_invalid_preview_issue():
    result = compile_plan_preview(
        {
            "version": 2,
            "steps": [
                {"phase": "missing-plan"},
            ],
        }
    )
    assert not result.valid
    assert result.error == "Step plan is required."
    assert len(result.issues) >= 1
    assert any(issue.code == "invalid_nested_plan" for issue in result.issues)
    assert any(issue.code == "invalid_nested_plan" and issue.message == "Step plan is required." for issue in result.issues)
    assert any(issue.severity == PreviewIssueSeverity.ERROR for issue in result.issues)


@pytest.mark.parametrize("coord_mode", ["player", "relative"])
def test_player_and_relative_modes_apply_offset_once(coord_mode: str):
    result = compile_plan_preview(
        {
            "version": 2,
            "coordMode": coord_mode,
            "offset": [1, 2, 3],
            "blocks": [
                {"pos": [1, 0, 0], "block": "stone"},
            ],
        }
    )
    assert result.valid
    assert result.commands == ["setblock ~2 ~2 ~3 minecraft:stone"]
    assert result.requires_runtime_origin is True
    assert any(issue.code == "runtime_origin_required" for issue in result.issues)


@pytest.mark.parametrize(
    "raw_anchors",
    [
        {"home": [9, 64, 3], "base": [3, 64, 8]},
        {"values": {"home": [9, 64, 3], "base": [3, 64, 8]}},
    ],
)
def test_build_anchors_accept_direct_and_wrapped_mappings(raw_anchors):
    result = compile_plan_preview(
        {
            "version": 2,
            "coordMode": "anchor",
            "anchor": "home",
            "anchors": raw_anchors,
            "blocks": [
                {"pos": [1, 0, 0], "block": "stone"},
            ],
        }
    )
    assert result.valid
    assert result.commands == ["setblock 10 64 3 minecraft:stone"]


def test_build_anchors_round_trip_with_values_wrapper():
    anchors = BuildAnchors.model_validate({"values": {"home": [9, 64, 3]}})
    assert anchors.model_dump() == {"values": {"home": {"x": 9, "y": 64, "z": 3}}}
