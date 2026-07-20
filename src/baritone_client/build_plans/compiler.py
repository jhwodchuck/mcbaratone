from __future__ import annotations

import re
from collections import Counter
from typing import Any, Iterable, Mapping

from .models import (
    BuildBounds,
    BuildBlock,
    BuildCuboid,
    BuildOptions,
    BuildPalette,
    BuildPlanPreview,
    BuildPlanV2,
    BuildPlanV2Error,
    BuildPoint,
    CoordinateMode,
    PreviewIssue,
    PreviewIssueSeverity,
    Rotation,
    parse_plan_payload,
)


TOKEN_ID_RE = re.compile(r"^[a-z0-9_.-]+:[a-z0-9_./-]+$")


def _rotate_point(point: BuildPoint, rotation: Rotation) -> BuildPoint:
    if rotation == Rotation.ZERO:
        return point
    if rotation == Rotation.NINETY:
        return BuildPoint(x=-point.z, y=point.y, z=point.x)
    if rotation == Rotation.ONE_EIGHTY:
        return BuildPoint(x=-point.x, y=point.y, z=-point.z)
    if rotation == Rotation.TWO_SEVENTY:
        return BuildPoint(x=point.z, y=point.y, z=-point.x)
    raise ValueError(f"Unsupported rotation: {rotation.value}")


def _rotate_bounds(from_point: BuildPoint, to_point: BuildPoint, rotation: Rotation) -> BuildBounds:
    if rotation == Rotation.ZERO:
        return BuildBounds(
            min=BuildPoint(
                x=min(from_point.x, to_point.x),
                y=min(from_point.y, to_point.y),
                z=min(from_point.z, to_point.z),
            ),
            max=BuildPoint(
                x=max(from_point.x, to_point.x),
                y=max(from_point.y, to_point.y),
                z=max(from_point.z, to_point.z),
            ),
        )

    corners = [
        BuildPoint(x=from_point.x, y=from_point.y, z=from_point.z),
        BuildPoint(x=to_point.x, y=from_point.y, z=from_point.z),
        BuildPoint(x=from_point.x, y=to_point.y, z=from_point.z),
        BuildPoint(x=to_point.x, y=to_point.y, z=from_point.z),
        BuildPoint(x=from_point.x, y=from_point.y, z=to_point.z),
        BuildPoint(x=to_point.x, y=from_point.y, z=to_point.z),
        BuildPoint(x=from_point.x, y=to_point.y, z=to_point.z),
        BuildPoint(x=to_point.x, y=to_point.y, z=to_point.z),
    ]
    rotated = [_rotate_point(point, rotation) for point in corners]
    xs = [point.x for point in rotated]
    ys = [point.y for point in rotated]
    zs = [point.z for point in rotated]
    return BuildBounds(
        min=BuildPoint(x=min(xs), y=min(ys), z=min(zs)),
        max=BuildPoint(x=max(xs), y=max(ys), z=max(zs)),
    )


def _merge_bounds(existing: BuildBounds | None, additional: BuildBounds) -> BuildBounds:
    if existing is None:
        return additional
    return BuildBounds(
        min=BuildPoint(
            x=min(existing.min.x, additional.min.x),
            y=min(existing.min.y, additional.min.y),
            z=min(existing.min.z, additional.min.z),
        ),
        max=BuildPoint(
            x=max(existing.max.x, additional.max.x),
            y=max(existing.max.y, additional.max.y),
            z=max(existing.max.z, additional.max.z),
        ),
    )


def _coerce_property_map(raw: Any) -> dict[str, str]:
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise ValueError("Block properties must be an object map.")

    properties: dict[str, str] = {}
    for key, value in dict(raw).items():
        key_text = str(key).strip().replace(" ", "_").lower()
        value_text = str(value).strip().replace(" ", "_").lower()
        if not key_text:
            raise ValueError("Property key must not be blank.")
        properties[key_text] = value_text
    return properties


def _resolve_alias_token(raw: str, palette: BuildPalette) -> str:
    token = str(raw).strip()
    if token.startswith("$"):
        token = token[1:]

    seen = set()
    while token in palette.values and token not in seen:
        seen.add(token)
        token = str(palette.values[token]).strip()

    return token


def _split_token_and_inline_state(raw: str) -> tuple[str, dict[str, str]]:
    normalized = str(raw).strip().replace(" ", "_")
    if "[" not in normalized:
        return normalized, {}

    if not normalized.endswith("]"):
        raise ValueError(f"Invalid block state syntax: {raw!r}")

    base, state = normalized.split("[", 1)
    state_text = state[:-1]
    if not base:
        raise ValueError(f"Invalid block token syntax: {raw!r}")

    properties: dict[str, str] = {}
    if state_text:
        for entry in state_text.split(","):
            if "=" not in entry:
                raise ValueError(f"Invalid block state entry: {entry!r}")
            key, value = entry.split("=", 1)
            key = key.strip().replace(" ", "_").lower()
            value = value.strip().replace(" ", "_").lower()
            if not key or not value:
                raise ValueError(f"Invalid block state entry: {entry!r}")
            properties[key] = value
    return base, properties


def _canonical_token(
    raw: str,
    palette: BuildPalette,
    properties: Any = None,
) -> str:
    if not isinstance(raw, str):
        raise ValueError("Block token must be a string")

    token = _resolve_alias_token(raw, palette)
    if not token:
        raise ValueError("Block token must not be blank.")

    base, inline_properties = _split_token_and_inline_state(token)
    merged = _coerce_property_map(inline_properties)
    merged.update(_coerce_property_map(properties))

    canonical_state = ""
    if merged:
        state_text = ",".join([f"{key}={value}" for key, value in sorted(merged.items())])
        canonical_state = f"[{state_text}]"

    token_id = base.lower().strip().replace(" ", "_")
    if ":" not in token_id:
        token_id = f"minecraft:{token_id}"

    if not TOKEN_ID_RE.match(token_id.split("[")[0]):
        raise ValueError(f"Invalid block token: {raw!r}")

    return f"{token_id}{canonical_state}"


def _sort_cuboids(cuboids: Iterable[BuildCuboid]) -> list[BuildCuboid]:
    return sorted(
        cuboids,
        key=lambda item: ((item.name or ""), item.from_.x, item.from_.y, item.from_.z, item.to.x, item.to.y, item.to.z),
    )


def _sort_blocks(blocks: Iterable[BuildBlock]) -> list[BuildBlock]:
    return sorted(blocks, key=lambda item: ((item.name or ""), item.pos.x, item.pos.y, item.pos.z, item.block))


def _ordered_steps(plan: BuildPlanV2) -> list:
    if plan.options.phase_reorder:
        return sorted(plan.steps, key=lambda step: str(getattr(step, "phase", "")))
    return list(plan.steps)


def _format_relative_coordinate(value: int) -> str:
    return "~" if value == 0 else f"~{value}"


def _format_point_for_command(point: BuildPoint, use_relative: bool) -> str:
    if use_relative:
        return (
            f"{_format_relative_coordinate(point.x)} "
            f"{_format_relative_coordinate(point.y)} "
            f"{_format_relative_coordinate(point.z)}"
        )
    return f"{point.x} {point.y} {point.z}"


def _limit_issue(message: str, phase: str | None, code: str, issues: list[PreviewIssue]) -> None:
    issues.append(
        PreviewIssue(
            code=code,
            message=message,
            severity=PreviewIssueSeverity.ERROR,
            phase=phase,
        )
    )


def _accumulate_volume(
    previous_volume: int,
    added_volume: int,
    cap: int,
    issues: list[PreviewIssue],
    phase: str | None,
) -> int:
    new_volume = previous_volume + added_volume
    if previous_volume <= cap < new_volume:
        _limit_issue(
            f"Total volume exceeds limit: {new_volume} > {cap}",
            phase=phase,
            code="volume_limit",
            issues=issues,
        )
    return new_volume


def _coerce_plan_step(raw_step: Any, parent_plan: BuildPlanV2 | None = None) -> BuildPlanV2:
    if raw_step is None:
        raise BuildPlanV2Error("Step plan is required.")
    if isinstance(raw_step, BuildPlanV2):
        coord_mode_explicit = bool(raw_step.coord_mode_explicit or ("coord_mode" in raw_step.__pydantic_fields_set__))
        payload = raw_step.model_dump(mode="python")
        if coord_mode_explicit:
            payload["coord_mode_explicit"] = True
        if parent_plan is not None:
            payload = _inherit_child_plan_payload(payload, parent_plan)
        return parse_plan_payload(payload)
    if isinstance(raw_step, Mapping):
        raw_mapping = dict(raw_step)
        coord_mode_explicit = False
        if "plan" in raw_mapping:
            nested_plan = raw_mapping["plan"]
            if isinstance(nested_plan, BuildPlanV2):
                coord_mode_explicit = bool(nested_plan.coord_mode_explicit or ("coord_mode" in nested_plan.__pydantic_fields_set__))
                payload = nested_plan.model_dump(mode="python")
                payload["coord_mode_explicit"] = coord_mode_explicit
            elif isinstance(nested_plan, Mapping):
                payload = dict(nested_plan)
                coord_mode_explicit = any(
                    key in payload
                    for key in ("coordMode", "coordinateMode", "coord_mode", "mode")
                )
            else:
                raise BuildPlanV2Error("Step plan must be a build plan object.")
        else:
            payload = raw_mapping
            phase_name = payload.pop("phase", None)
            if phase_name is not None and "summary" not in payload:
                payload["summary"] = phase_name
            coord_mode_explicit = any(
                key in payload
                for key in ("coordMode", "coordinateMode", "coord_mode", "mode")
            )
        if parent_plan is not None:
            payload = _inherit_child_plan_payload(payload, parent_plan)
        if coord_mode_explicit:
            payload["coord_mode_explicit"] = True
        return parse_plan_payload(payload)
    raise BuildPlanV2Error("Step plan must be a build plan object.")


def _inherit_child_plan_payload(
    child_payload: Mapping[str, Any],
    parent_plan: BuildPlanV2,
) -> dict[str, Any]:
    merged = dict(child_payload)

    if "version" not in merged:
        merged["version"] = parent_plan.version

    if not any(
        key in merged
        for key in ("coordMode", "coordinateMode", "coord_mode", "mode")
    ):
        merged["coordMode"] = parent_plan.coord_mode.value

    if not any(key in merged for key in ("rotation", "rotate")):
        merged["rotation"] = parent_plan.rotation.value

    if "palette" not in merged:
        merged["palette"] = {"values": dict(parent_plan.palette.values)}
    else:
        raw_palette = merged["palette"]
        if isinstance(raw_palette, Mapping):
            parent_values = dict(parent_plan.palette.values)
            child_values_raw = raw_palette.get("values", raw_palette)
            if isinstance(child_values_raw, Mapping):
                merged["palette"] = {
                    "values": {**parent_values, **{str(k): str(v) for k, v in dict(child_values_raw).items()}},
                }

    if "anchors" not in merged:
        merged["anchors"] = {"values": {name: point.model_dump() for name, point in parent_plan.anchors.values.items()}}
    else:
        raw_anchors = merged["anchors"]
        if isinstance(raw_anchors, Mapping):
            parent_values = {
                name: point.model_dump() for name, point in parent_plan.anchors.values.items()
            }
            child_anchors_raw = raw_anchors.get("values", raw_anchors)
            if isinstance(child_anchors_raw, Mapping):
                merged_anchors = dict(parent_values)
                for key, value in dict(child_anchors_raw).items():
                    merged_anchors[str(key)] = BuildPoint.model_validate(value).model_dump()
                merged["anchors"] = {"values": merged_anchors}

    if "options" not in merged:
        merged["options"] = parent_plan.options.model_dump()
    else:
        raw_options = merged["options"]
        if isinstance(raw_options, Mapping):
            merged["options"] = {**parent_plan.options.model_dump(), **dict(raw_options)}

    if "origin" not in merged and parent_plan.origin is not None and parent_plan.coord_mode in {
        CoordinateMode.ABSOLUTE,
        CoordinateMode.WORLD,
    }:
        merged["origin"] = parent_plan.origin.model_dump()

    if "anchor" not in merged and parent_plan.coord_mode == CoordinateMode.ANCHOR and parent_plan.anchor is not None:
        merged["anchor"] = parent_plan.anchor
        merged["coordMode"] = CoordinateMode.ANCHOR.value

    return merged


def _resolve_origin(
    plan: BuildPlanV2,
    inherited_origin: BuildPoint | None,
    inherited_requires_runtime: bool,
    issues: list[PreviewIssue],
) -> tuple[BuildPoint, bool, bool]:
    mode = plan.coord_mode
    anchor_name = plan.anchor
    origin = plan.origin or BuildPoint(x=0, y=0, z=0)
    valid = True
    requires_runtime_origin = False
    is_coord_mode_explicit = bool(getattr(plan, "coord_mode_explicit", True))

    if mode in {CoordinateMode.ABSOLUTE, CoordinateMode.WORLD}:
        if plan.origin is None:
            if inherited_origin is None:
                valid = False
                issues.append(
                    PreviewIssue(
                        code="missing_origin",
                        message="absolute/world coordinate mode requires an origin",
                        severity=PreviewIssueSeverity.ERROR,
                        phase=plan.summary,
                    )
                )
                return origin, requires_runtime_origin, valid
            origin = inherited_origin
        else:
            origin = plan.origin

    elif mode == CoordinateMode.ANCHOR:
        if not anchor_name:
            valid = False
            issues.append(
                PreviewIssue(
                    code="missing_anchor",
                    message="anchor mode requires an anchor reference",
                    severity=PreviewIssueSeverity.ERROR,
                    phase=plan.summary,
                )
            )
            return origin, requires_runtime_origin, valid

        anchor_key = anchor_name
        if ":" in anchor_key:
            anchor_key = anchor_key.split(":", 1)[1]
        if anchor_key in plan.anchors.values:
            origin = plan.anchors.values[anchor_key]
        else:
            valid = False
            issues.append(
                PreviewIssue(
                    code="unknown_anchor",
                    message=f"unknown anchor: {anchor_name!r}",
                    severity=PreviewIssueSeverity.ERROR,
                    phase=plan.summary,
                )
            )
            return origin, requires_runtime_origin, valid

    elif mode in {CoordinateMode.PLAYER, CoordinateMode.RELATIVE}:
        if is_coord_mode_explicit:
            requires_runtime_origin = inherited_requires_runtime or (plan.origin is None and inherited_origin is None)
        if requires_runtime_origin:
            issues.append(
                PreviewIssue(
                    code="runtime_origin_required",
                    message="player/relative plan has no runtime origin; commands are emitted relative to player",
                    severity=PreviewIssueSeverity.WARNING,
                    phase=plan.summary,
                )
            )

    return origin, requires_runtime_origin, valid


def _ensure_limits_for_point(
    point: BuildPoint,
    options: BuildOptions,
    phase: str | None,
    issues: list[PreviewIssue],
    *,
    enforce_vertical: bool = True,
) -> bool:
    valid = True
    if abs(point.x) > options.max_horizontal_offset or abs(point.z) > options.max_horizontal_offset:
        _limit_issue(
            "Point exceeds configured horizontal range",
            phase,
            "horizontal_limit",
            issues,
        )
        valid = False
    if enforce_vertical and abs(point.y) > options.max_vertical_offset:
        _limit_issue(
            "Point exceeds configured vertical range",
            phase,
            "vertical_limit",
            issues,
        )
        valid = False
    return valid


def _hollow_block_count(bounds: BuildBounds, is_hollow: bool) -> int:
    total = bounds.volume
    if not is_hollow:
        return total
    x_size = bounds.max.x - bounds.min.x + 1
    y_size = bounds.max.y - bounds.min.y + 1
    z_size = bounds.max.z - bounds.min.z + 1
    if x_size <= 2 or y_size <= 2 or z_size <= 2:
        return total
    return total - (x_size - 2) * (y_size - 2) * (z_size - 2)


def _compile_single_block(
    block: BuildBlock,
    origin: BuildPoint,
    rotation: Rotation,
    offset: BuildPoint,
    phase: str | None,
    options: BuildOptions,
    palette: BuildPalette,
    issues: list[PreviewIssue],
    materials: Counter[str],
    *,
    enforce_vertical_limits: bool = True,
    emit_relative: bool = False,
) -> tuple[bool, int, BuildBounds | None, str]:
    transformed = _rotate_point(block.pos, rotation).shifted(offset.x, offset.y, offset.z)
    valid = _ensure_limits_for_point(
        transformed,
        options,
        phase,
        issues,
        enforce_vertical=enforce_vertical_limits,
    )
    try:
        token = _canonical_token(block.block, palette, block.properties)
        command_point = transformed if emit_relative else transformed.shifted(origin.x, origin.y, origin.z)
        command = f"setblock {_format_point_for_command(command_point, emit_relative)} {token}"
        materials[token] += 1
        return valid, 1, BuildBounds(min=transformed, max=transformed), command
    except ValueError as error:
        _limit_issue(
            f"Invalid block token in block: {block.block!r}. {error}",
            phase=phase,
            code="invalid_block_token",
            issues=issues,
        )
        command_point = transformed if emit_relative else transformed.shifted(origin.x, origin.y, origin.z)
        return (
            False,
            0,
            BuildBounds(min=transformed, max=transformed),
            f"setblock {_format_point_for_command(command_point, emit_relative)} {block.block}",
        )


def _compile_cuboid(
    cuboid: BuildCuboid,
    origin: BuildPoint,
    rotation: Rotation,
    offset: BuildPoint,
    phase: str | None,
    options: BuildOptions,
    palette: BuildPalette,
    issues: list[PreviewIssue],
    materials: Counter[str],
    *,
    enforce_vertical_limits: bool = True,
    emit_relative: bool = False,
) -> tuple[bool, int, BuildBounds, str]:
    rotated_bounds = _rotate_bounds(cuboid.from_, cuboid.to, rotation)
    transformed = BuildBounds(
        min=rotated_bounds.min.shifted(offset.x, offset.y, offset.z),
        max=rotated_bounds.max.shifted(offset.x, offset.y, offset.z),
    )

    valid = True
    if not _ensure_limits_for_point(
        transformed.min,
        options,
        phase,
        issues,
        enforce_vertical=enforce_vertical_limits,
    ):
        valid = False
    if not _ensure_limits_for_point(
        transformed.max,
        options,
        phase,
        issues,
        enforce_vertical=enforce_vertical_limits,
    ):
        valid = False

    fill_mode = (cuboid.fill_mode or "").strip()
    is_hollow = bool(cuboid.hollow) or fill_mode.lower() == "hollow"
    if cuboid.hollow and not fill_mode:
        fill_mode = "hollow"

    try:
        token = _canonical_token(cuboid.block, palette, cuboid.properties)
        material_volume = _hollow_block_count(transformed, is_hollow)
        materials[token] += material_volume
        if emit_relative:
            min_point = transformed.min
            max_point = transformed.max
            command = (
                f"fill {_format_point_for_command(min_point, True)} "
                f"{_format_point_for_command(max_point, True)} {token}"
                + (f" {fill_mode}" if fill_mode else "")
            )
        else:
            world_min = transformed.min.shifted(origin.x, origin.y, origin.z)
            world_max = transformed.max.shifted(origin.x, origin.y, origin.z)
            command = (
                f"fill {world_min.x} {world_min.y} {world_min.z} "
                f"{world_max.x} {world_max.y} {world_max.z} {token}"
                + (f" {fill_mode}" if fill_mode else "")
            )
        return valid, material_volume, transformed, command
    except ValueError as error:
        _limit_issue(
            f"Invalid block token in cuboid: {cuboid.block!r}. {error}",
            phase=phase,
            code="invalid_block_token",
            issues=issues,
        )
        min_point = transformed.min
        max_point = transformed.max
        if not emit_relative:
            min_point = min_point.shifted(origin.x, origin.y, origin.z)
            max_point = max_point.shifted(origin.x, origin.y, origin.z)
        return False, 0, transformed, (
            f"fill {_format_point_for_command(min_point, emit_relative)} {_format_point_for_command(max_point, emit_relative)} {cuboid.block}"
            + (f" {fill_mode}" if fill_mode else "")
        )


def _compile_plan(
    plan: BuildPlanV2,
    inherited_origin: BuildPoint | None,
    inherited_requires_runtime: bool,
    cumulative_volume: int,
    max_total_volume: int,
    issues: list[PreviewIssue],
    commands: list[str],
    materials: Counter[str],
) -> tuple[bool, int, BuildBounds | None, int, bool]:
    options = plan.options
    rotation = plan.rotation
    origin, requires_runtime_origin, origin_valid = _resolve_origin(
        plan,
        inherited_origin=inherited_origin,
        inherited_requires_runtime=inherited_requires_runtime,
        issues=issues,
    )
    if inherited_origin is not None and not inherited_requires_runtime and plan.coord_mode not in {CoordinateMode.ABSOLUTE, CoordinateMode.WORLD}:
        origin = inherited_origin.shifted(origin.x, origin.y, origin.z)

    phase_count = 1 if (plan.cuboids or plan.blocks) else 0
    valid = origin_valid
    total_volume = cumulative_volume
    bounds: BuildBounds | None = None
    enforce_plan_relative_limits = plan.coord_mode not in {CoordinateMode.WORLD, CoordinateMode.ABSOLUTE}
    offset = plan.offset or BuildPoint(x=0, y=0, z=0)
    emit_relative = bool(requires_runtime_origin)

    for cuboid in _sort_cuboids(plan.cuboids):
        cuboid_valid, cuboid_volume, cuboid_bounds, command = _compile_cuboid(
            cuboid,
            origin,
            rotation,
            offset,
            plan.summary,
            options,
            plan.palette,
            issues,
            materials,
            enforce_vertical_limits=enforce_plan_relative_limits,
            emit_relative=emit_relative,
        )
        valid &= cuboid_valid
        total_volume = _accumulate_volume(
            total_volume,
            cuboid_volume,
            max_total_volume,
            issues,
            plan.summary,
        )
        commands.append(command)
        bounds = cuboid_bounds if bounds is None else _merge_bounds(bounds, cuboid_bounds)

    for block in _sort_blocks(plan.blocks):
        block_valid, block_volume, block_bounds, command = _compile_single_block(
            block,
            origin,
            rotation,
            offset,
            plan.summary,
            options,
            plan.palette,
            issues,
            materials,
            enforce_vertical_limits=enforce_plan_relative_limits,
            emit_relative=emit_relative,
        )
        valid &= block_valid
        total_volume = _accumulate_volume(
            total_volume,
            block_volume,
            max_total_volume,
            issues,
            plan.summary,
        )
        commands.append(command)
        bounds = block_bounds if bounds is None else _merge_bounds(bounds, block_bounds)

    for step in _ordered_steps(plan):
        try:
            subplan = _coerce_plan_step(getattr(step, "plan", None), parent_plan=plan)
        except BuildPlanV2Error as error:
            _limit_issue(str(error), phase=plan.summary, code="invalid_nested_plan", issues=issues)
            valid = False
            continue

        (
            child_valid,
            total_volume,
            child_bounds,
            child_phases,
            child_requires_runtime_origin,
        ) = _compile_plan(
            subplan,
            inherited_origin=None if requires_runtime_origin else origin,
            inherited_requires_runtime=requires_runtime_origin,
            cumulative_volume=total_volume,
            max_total_volume=max_total_volume,
            issues=issues,
            commands=commands,
            materials=materials,
        )
        valid &= child_valid
        phase_count += child_phases
        requires_runtime_origin = requires_runtime_origin or child_requires_runtime_origin
        if child_bounds is not None:
            bounds = child_bounds if bounds is None else _merge_bounds(bounds, child_bounds)

    return (
        valid and not any(issue.code == "volume_limit" for issue in issues),
        total_volume,
        bounds,
        max(phase_count, 0),
        requires_runtime_origin,
    )


def compile_plan_preview(payload: Any) -> BuildPlanPreview:
    """Compile a build plan payload to a deterministic read-only preview."""
    issues: list[PreviewIssue] = []
    commands: list[str] = []
    materials: Counter[str] = Counter()

    try:
        plan = parse_plan_payload(payload)
    except BuildPlanV2Error as error:
        _limit_issue(str(error), phase=None, code="invalid_plan", issues=issues)
        return BuildPlanPreview(
            valid=False,
            valid_count=0,
            requires_runtime_origin=False,
            error=str(error),
            applied_rotation=0,
            phase_count=0,
            total_volume=0,
            transformed_bounds=None,
            material_counts={},
            commands=[],
            issues=issues,
        )

    max_total_volume = plan.options.max_total_volume
    valid, total_volume, bounds, phase_count, requires_runtime_origin = _compile_plan(
        plan=plan,
        inherited_origin=None,
        inherited_requires_runtime=False,
        cumulative_volume=0,
        max_total_volume=max_total_volume,
        issues=issues,
        commands=commands,
        materials=materials,
    )

    material_counts = {token: materials[token] for token in sorted(materials)}

    return BuildPlanPreview(
        valid=valid,
        valid_count=(len(commands) if valid else 0),
        error=next((issue.message for issue in issues if issue.severity == PreviewIssueSeverity.ERROR), None),
        requires_runtime_origin=requires_runtime_origin,
        applied_rotation=plan.rotation.value,
            phase_count=phase_count,
        total_volume=total_volume,
        transformed_bounds=bounds,
        material_counts=material_counts,
        commands=commands,
        issues=issues,
    )


__all__ = ["compile_plan_preview"]
