"""Parser and helpers for legacy historical JSON schematic payloads."""

from __future__ import annotations

import json
from collections import Counter, OrderedDict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field

from .models import (
    Coordinate,
    LegacyJsonBlockInput,
    LegacyJsonLimits,
    LegacyJsonSchematicError,
    LegacyJsonSchematicInput,
)


DEFAULT_MAX_BLOCKS = 100_000
DEFAULT_MAX_DIMENSION = 256


def _as_payload(value: Any) -> Any:
    if isinstance(value, (str, bytes, bytearray, Path)):
        text = value.decode("utf-8") if isinstance(value, (bytes, bytearray)) else str(value)
        trimmed = text.strip()
        if isinstance(value, (str, bytes, bytearray)) and (
            trimmed.startswith("{") or trimmed.startswith("[")
        ):
            return json.loads(trimmed)
        path = Path(trimmed)
        if not path.exists():
            raise LegacyJsonSchematicError(f"Schematic JSON path does not exist: {trimmed}")
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    if isinstance(value, (Mapping, Sequence)) and not isinstance(value, (str, bytes, bytearray)):
        return value
    raise LegacyJsonSchematicError(f"Unsupported input type: {type(value).__name__}")


def _coerce_limits(value: LegacyJsonLimits | Sequence[int] | None) -> LegacyJsonLimits | None:
    if value is None:
        return None
    if isinstance(value, LegacyJsonLimits):
        return value
    return LegacyJsonLimits.model_validate(value)


def _coerce_position_block(value: LegacyJsonBlockInput) -> "ParsedLegacyBlock":
    return ParsedLegacyBlock(
        x=value.coordinate.x,
        y=value.coordinate.y,
        z=value.coordinate.z,
        id=value.normalized_id,
        data=value.data,
    )


def _coerce_path(path: str, *, default: str) -> str:
    normalized = str(path or default).strip().replace("\\", "/")
    if not normalized:
        return default
    if normalized.startswith("/"):
        raise LegacyJsonSchematicError(f"Unsafe schematic path (absolute): {normalized!r}")
    if ".." in normalized.split("/"):
        raise LegacyJsonSchematicError(f"Unsafe schematic path (traversal): {normalized!r}")
    return normalized


def _coerce_name(name: str | None, *, default: str) -> str:
    normalized = str(name or "").strip()
    return normalized or default


def _block_counts(blocks: Sequence["ParsedLegacyBlock"]) -> Dict[str, int]:
    filtered = [block.id for block in blocks if block.id != "minecraft:air"]
    counter = Counter(filtered)
    ordered = OrderedDict((key, counter[key]) for key in sorted(counter))
    return dict(ordered)


def _dimensions(blocks: Sequence["ParsedLegacyBlock"]) -> tuple[int, int, int]:
    if not blocks:
        return (0, 0, 0)
    xs = [block.x for block in blocks]
    ys = [block.y for block in blocks]
    zs = [block.z for block in blocks]
    return (max(xs) - min(xs) + 1, max(ys) - min(ys) + 1, max(zs) - min(zs) + 1)


class ParsedLegacyBlock(BaseModel):
    """Canonical representation of one parsed legacy block."""

    x: int
    y: int
    z: int
    id: str
    data: int = 0

    model_config = ConfigDict(frozen=True)


class LegacyPlacementStep(BaseModel):
    """One deterministic, safe placement step."""

    x: int
    y: int
    z: int
    id: str
    data: int = 0

    model_config = ConfigDict(frozen=True)


class LegacyPlacementPlan(BaseModel):
    """Deterministic placement plan for one legacy schematic."""

    schematic: str
    origin: Coordinate
    steps: list[LegacyPlacementStep] = Field(default_factory=list)

    model_config = ConfigDict(frozen=True)

    def command_plan(self) -> list[str]:
        commands: list[str] = []
        for step in self.steps:
            commands.append(
                f"setblock {step.x} {step.y} {step.z} {step.id}"
            )
        return commands


class ParsedLegacySchematic(BaseModel):
    """Normalized native representation of a legacy schematic."""

    name: str
    path: str
    size: tuple[int, int, int]
    limits: LegacyJsonLimits
    blocks: list[ParsedLegacyBlock] = Field(default_factory=list)
    material_counts: Dict[str, int] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)

    model_config = ConfigDict(frozen=True)

    @property
    def origin(self) -> Coordinate:
        if not self.blocks:
            return Coordinate(x=0, y=0, z=0)
        min_x = min(block.x for block in self.blocks)
        min_y = min(block.y for block in self.blocks)
        min_z = min(block.z for block in self.blocks)
        return Coordinate(x=min_x, y=min_y, z=min_z)

    def to_native_dict(self) -> Dict[str, Any]:
        return {
            "format": "legacy-json/v1",
            "name": self.name,
            "path": self.path,
            "size": {
                "x": self.size[0],
                "y": self.size[1],
                "z": self.size[2],
            },
            "limits": self.limits.as_ordered_dict(),
            "material_counts": OrderedDict(
                (key, self.material_counts[key]) for key in sorted(self.material_counts)
            ),
            "blocks": [
                {
                    "x": block.x,
                    "y": block.y,
                    "z": block.z,
                    "id": block.id,
                    "data": block.data,
                }
                for block in self.blocks
            ],
            "warnings": list(self.warnings),
        }

    def to_native_json(self, *, indent: int | None = None) -> str:
        return json.dumps(
            self.to_native_dict(),
            sort_keys=True,
            ensure_ascii=False,
            indent=indent,
            separators=None if indent else (",", ":"),
        )

    def build_safe_placement_plan(
        self,
        *,
        origin: Coordinate | tuple[int, int, int] = (0, 0, 0),
        include_air: bool = False,
    ) -> LegacyPlacementPlan:
        base = Coordinate.coerce(origin)
        ordered = sorted(self.blocks, key=lambda block: (block.y, block.x, block.z, block.id))
        steps: list[LegacyPlacementStep] = []
        for block in ordered:
            if not include_air and block.id == "minecraft:air":
                continue
            steps.append(
                LegacyPlacementStep(
                    x=base.x + block.x,
                    y=base.y + block.y,
                    z=base.z + block.z,
                    id=block.id,
                    data=block.data,
                )
            )
        return LegacyPlacementPlan(schematic=self.name, origin=Coordinate(x=base.x, y=base.y, z=base.z), steps=steps)


class LegacyJsonSchematicCollection(BaseModel):
    """Collection of normalized legacy schematics with duplicate-path/name validation."""

    schematics: list[ParsedLegacySchematic] = Field(default_factory=list)

    def schematic_names(self) -> list[str]:
        return [item.name for item in self.schematics]

    def schematic_paths(self) -> list[str]:
        return [item.path for item in self.schematics]

    def names_and_paths(self) -> list[tuple[str, str]]:
        return [(item.name, item.path) for item in self.schematics]


def _validate_dimensions(
    size: tuple[int, int, int],
    max_dimension: int,
    limits: LegacyJsonLimits | None,
    *,
    schematic_name: str,
) -> None:
    if any(axis > max_dimension for axis in size):
        raise LegacyJsonSchematicError(
            f"Schematic {schematic_name!r} exceeds max dimension {max_dimension}: {size!r}"
        )
    if limits is not None:
        if size[0] > limits.x or size[1] > limits.y or size[2] > limits.z:
            raise LegacyJsonSchematicError(
                f"Schematic {schematic_name!r} dimensions {size!r} exceed declared limits {limits.model_dump()!r}"
            )
    return None


def parse_legacy_json_schematic(
    payload: Any,
    *,
    strict_non_zero_data: bool = False,
    max_blocks: int = DEFAULT_MAX_BLOCKS,
    max_dimension: int = DEFAULT_MAX_DIMENSION,
    allow_custom_namespaces: bool = True,
) -> ParsedLegacySchematic:
    """
    Parse one or multiple schematics from legacy JSON and return the first schematic.

    The parser supports `position` or `pos` for block coordinates and normalizes IDs
    to canonical vanilla namespace form.
    """
    collection = parse_legacy_json_schematics(
        payload=payload,
        strict_non_zero_data=strict_non_zero_data,
        max_blocks=max_blocks,
        max_dimension=max_dimension,
        allow_custom_namespaces=allow_custom_namespaces,
    )
    if not collection.schematics:
        raise LegacyJsonSchematicError("No schematics found in payload.")
    return collection.schematics[0]


def parse_legacy_json_schematics(
    payload: Any,
    *,
    strict_non_zero_data: bool = False,
    max_blocks: int = DEFAULT_MAX_BLOCKS,
    max_dimension: int = DEFAULT_MAX_DIMENSION,
    allow_custom_namespaces: bool = True,
) -> LegacyJsonSchematicCollection:
    """
    Parse one or many legacy schematics.

    Returns a validated and normalized collection.
    """
    source = _as_payload(payload)

    if isinstance(source, Mapping):
        entries: Iterable[Any]
        if "schematics" in source:
            schematics_payload = source["schematics"]
            if not isinstance(schematics_payload, Sequence) or isinstance(schematics_payload, (str, bytes, bytearray)):
                raise LegacyJsonSchematicError("Expected `schematics` to be a JSON array.")
            entries = schematics_payload
        else:
            entries = [source]
    elif isinstance(source, Sequence) and not isinstance(source, (str, bytes, bytearray)):
        entries = source
    else:
        raise LegacyJsonSchematicError("Payload must be a map, an array of schematics, or JSON text.")

    parsed: list[ParsedLegacySchematic] = []
    seen_names: set[str] = set()
    seen_paths: set[str] = set()

    for entry in entries:
        entry_model = LegacyJsonSchematicInput.model_validate(entry)

        name = _coerce_name(entry_model.name, default="legacy-schematic")
        path = _coerce_path(entry_model.path or f"{name}.json", default= f"{name}.json")
        limits = _coerce_limits(entry_model.limits)

        if not allow_custom_namespaces:
            invalid_namespaces = [
                block.normalized_id.split(":", 1)[0]
                for block in entry_model.blocks
                if block.normalized_id.split(":", 1)[0] != "minecraft"
            ]
            if invalid_namespaces:
                raise LegacyJsonSchematicError(
                    "Only minecraft namespace IDs are allowed in strict namespace mode."
                )

        if name in seen_names:
            raise LegacyJsonSchematicError(f"Duplicate schematic name: {name!r}")
        if path in seen_paths:
            raise LegacyJsonSchematicError(f"Duplicate schematic path: {path!r}")

        if len(entry_model.blocks) > max_blocks:
            raise LegacyJsonSchematicError(
                f"Schematic {name!r} exceeds max blocks ({len(entry_model.blocks)} > {max_blocks})."
            )

        normalized_blocks: list[ParsedLegacyBlock] = []
        seen_positions: set[tuple[int, int, int]] = set()
        warnings: list[str] = []

        for block in entry_model.blocks:
            parsed_block = _coerce_position_block(block)
            position = (parsed_block.x, parsed_block.y, parsed_block.z)

            if position in seen_positions:
                raise LegacyJsonSchematicError(
                    f"Duplicate position in {name!r}: {position!r}"
                )
            seen_positions.add(position)

            if parsed_block.data != 0:
                if strict_non_zero_data:
                    raise LegacyJsonSchematicError(
                        f"Schematic {name!r} has non-zero data at {position}: {parsed_block.data!r}"
                    )
                warnings.append(
                    f"Lossy block-data normalization at {position}: {parsed_block.data} -> 0"
                )
                parsed_block = ParsedLegacyBlock(
                    x=parsed_block.x,
                    y=parsed_block.y,
                    z=parsed_block.z,
                    id=parsed_block.id,
                    data=0,
                )
            normalized_blocks.append(parsed_block)

        # Deterministic order for canonical representation.
        normalized_blocks.sort(key=lambda block: (block.y, block.x, block.z, block.id))

        size = _dimensions(normalized_blocks)
        if limits is None:
            limits = LegacyJsonLimits.model_validate(list(size))
        _validate_dimensions(size, max_dimension, limits, schematic_name=name)
        seen_names.add(name)
        seen_paths.add(path)

        parsed.append(
            ParsedLegacySchematic(
                name=name,
                path=path,
                size=size,
                limits=limits,
                blocks=normalized_blocks,
                material_counts=_block_counts(normalized_blocks),
                warnings=warnings,
            )
        )

    # Validate duplicate limit declarations across schema collection.
    seen_limit_tuples = set()
    for schematic in parsed:
        limit_tuple = (schematic.limits.x, schematic.limits.y, schematic.limits.z)
        if limit_tuple in seen_limit_tuples:
            # This is a data quality warning for mirrored files; keep validation surface stable.
            continue
        seen_limit_tuples.add(limit_tuple)

    return LegacyJsonSchematicCollection(schematics=parsed)
