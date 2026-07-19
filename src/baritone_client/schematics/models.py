"""Data models for legacy JSON schematic parsing."""

from __future__ import annotations

from collections import OrderedDict
from typing import Any, Dict, List, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field, model_validator


class LegacyJsonSchematicError(ValueError):
    """Raised when a legacy schematic payload fails strict validation."""


class Coordinate(BaseModel):
    """Integer coordinate in schematic space."""

    x: int
    y: int
    z: int

    model_config = ConfigDict(frozen=True)

    @classmethod
    def coerce(cls, value: Any) -> "Coordinate":
        if isinstance(value, Coordinate):
            return value
        if isinstance(value, Mapping):
            return cls(
                x=value["x"],
                y=value["y"],
                z=value["z"],
            )
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            if len(value) != 3:
                raise ValueError("Coordinate sequences must contain exactly three integers.")
            return cls(x=value[0], y=value[1], z=value[2])
        raise ValueError("Coordinates must be a mapping or a sequence of three integers.")

    def tuple(self) -> tuple[int, int, int]:
        return (self.x, self.y, self.z)


class LegacyJsonLimits(BaseModel):
    """Optional explicit size limits for a schematic."""

    x: int = Field(gt=0)
    y: int = Field(gt=0)
    z: int = Field(gt=0)

    model_config = ConfigDict(frozen=True)

    @model_validator(mode="before")
    @classmethod
    def _coerce_sequence(cls, value: Any) -> Mapping[str, int]:
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            if len(value) != 3:
                raise ValueError("limits must be three integers [x, y, z] or an object.")
            return {"x": value[0], "y": value[1], "z": value[2]}
        if isinstance(value, Mapping):
            return value
        raise ValueError("limits must be an object or list/tuple of three integers.")

    def as_ordered_dict(self) -> Dict[str, int]:
        return OrderedDict(
            [
                ("x", self.x),
                ("y", self.y),
                ("z", self.z),
            ]
        )


class LegacyJsonBlockInput(BaseModel):
    """Input record for one legacy block entry."""

    # Some historical data uses block/id/material/blockId interchangeably.
    id: str | None = None
    block: str | None = None
    material: str | None = None
    block_id: str | None = None

    # Both `position` and `pos` can be present in historical payloads.
    position: Coordinate | Mapping[str, int] | Sequence[int] | None = None
    pos: Coordinate | Mapping[str, int] | Sequence[int] | None = None

    # Older generators sometimes include meta/state under "data".
    data: int = 0

    model_config = ConfigDict(extra="ignore")

    @model_validator(mode="before")
    @classmethod
    def _coerce_keys(cls, value: Any) -> Mapping[str, Any]:
        if not isinstance(value, Mapping):
            raise ValueError("Block entry must be an object.")
        normalized = dict(value)
        if normalized.get("id") is None:
            for key in ("block", "material", "block_id"):
                if normalized.get(key) is not None:
                    normalized["id"] = normalized[key]
                    break
        return normalized

    @model_validator(mode="after")
    def _validate_position_and_id(self) -> "LegacyJsonBlockInput":
        if self.id is None:
            raise ValueError("Block entry missing required id.")

        has_position = self.position is not None
        has_pos = self.pos is not None
        if has_position == has_pos:
            raise ValueError("Block entry must include exactly one of 'position' or 'pos'.")

        if has_position:
            self.position = Coordinate.coerce(self.position)
        if has_pos:
            self.pos = Coordinate.coerce(self.pos)
        return self

    @property
    def normalized_id(self) -> str:
        value = self.id.strip().lower()
        if ":" not in value:
            value = f"minecraft:{value}"
        namespace, _, name = value.partition(":")
        if not namespace or not name:
            raise LegacyJsonSchematicError(f"Invalid block id: {self.id!r}")
        return value

    @property
    def coordinate(self) -> Coordinate:
        return self.position if self.position is not None else self.pos  # type: ignore[return-value]


class LegacyJsonSchematicInput(BaseModel):
    """Raw top-level schematic entry from historical JSON."""

    name: str | None = None
    path: str | None = None
    blocks: List[LegacyJsonBlockInput] = Field(default_factory=list)

    limits: LegacyJsonLimits | Sequence[int] | None = None
    metadata: Mapping[str, Any] | None = None

    model_config = ConfigDict(extra="ignore")

    @model_validator(mode="before")
    @classmethod
    def _coerce_fields(cls, value: Any) -> Mapping[str, Any]:
        if not isinstance(value, Mapping):
            raise ValueError("Schematic payload must be an object.")
        normalized = dict(value)
        if normalized.get("name") is None:
            if normalized.get("id") is not None:
                normalized["name"] = normalized["id"]
        if normalized.get("path") is None:
            normalized["path"] = normalized.get("file", normalized.get("filename"))
        if normalized.get("blocks") is None:
            if normalized.get("blockList") is not None:
                normalized["blocks"] = normalized["blockList"]
            elif normalized.get("block_list") is not None:
                normalized["blocks"] = normalized["block_list"]
        return normalized

    @model_validator(mode="after")
    def _validate_blocks(self) -> "LegacyJsonSchematicInput":
        if self.blocks is None:
            self.blocks = []
        return self
