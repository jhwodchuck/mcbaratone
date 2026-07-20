from __future__ import annotations

import json
from enum import Enum
from typing import Any, Dict, Mapping, Sequence

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    model_validator,
    field_validator,
)


class BuildPlanV2Error(ValueError):
    """Raised when BuildPlanV2 payloads or preview requests are invalid."""


class PreviewIssueSeverity(str, Enum):
    ERROR = "error"
    WARNING = "warning"


class CoordinateMode(str, Enum):
    PLAYER = "player"
    RELATIVE = "relative"
    ABSOLUTE = "absolute"
    WORLD = "world"
    ANCHOR = "anchor"

    @classmethod
    def normalize(cls, value: Any) -> "CoordinateMode":
        if value is None:
            return cls.PLAYER
        text = str(value).strip().lower()
        if text in {"player", "relative"}:
            return cls.PLAYER
        if text in {"world", "absolute"}:
            return cls.ABSOLUTE
        if text == "anchor":
            return cls.ANCHOR
        raise ValueError(f"Unsupported coordinate mode: {value!r}")


class Rotation(int, Enum):
    ZERO = 0
    NINETY = 90
    ONE_EIGHTY = 180
    TWO_SEVENTY = 270

    @classmethod
    def normalize(cls, value: Any) -> "Rotation":
        if isinstance(value, Rotation):
            return value

        normalized = str(value).strip().lower() if not isinstance(value, bool) else str(int(value))
        if not normalized:
            raise ValueError("Rotation is required.")

        mapping = {
            "none": 0,
            "0": 0,
            "90": 90,
            "180": 180,
            "270": 270,
            "cw": 90,
            "ccw": 270,
            "clockwise": 90,
            "counterclockwise": 270,
            "counter-clockwise": 270,
            "flip": 180,
            "half": 180,
        }
        value_int = mapping.get(normalized)
        if value_int is None:
            try:
                value_int = int(normalized)
            except (TypeError, ValueError) as error:
                raise ValueError(f"Invalid rotation value: {value!r}") from error

        value_int %= 360
        if value_int not in {0, 90, 180, 270}:
            raise ValueError(f"Unsupported rotation (must be 0/90/180/270): {value!r}")
        return cls(value_int)


class BuildPoint(BaseModel):
    x: int
    y: int
    z: int

    model_config = ConfigDict(frozen=True)

    @model_validator(mode="before")
    @classmethod
    def coerce(cls, value: Any) -> Any:
        if isinstance(value, BuildPoint):
            return value

        if isinstance(value, Mapping):
            try:
                return {
                    "x": int(value.get("x")),
                    "y": int(value.get("y")),
                    "z": int(value.get("z")),
                }
            except (TypeError, ValueError) as exc:
                raise ValueError("Point coordinates must be integers.") from exc

        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            if len(value) != 3:
                raise ValueError("Point sequence must contain exactly three integers.")
            try:
                return {
                    "x": int(value[0]),
                    "y": int(value[1]),
                    "z": int(value[2]),
                }
            except (TypeError, ValueError) as exc:
                raise ValueError("Point sequence must contain integers.") from exc

        raise ValueError("Point must be a mapping with x/y/z or a 3-tuple/list of integers.")

    def shifted(self, dx: int, dy: int, dz: int) -> "BuildPoint":
        return BuildPoint(x=self.x + int(dx), y=self.y + int(dy), z=self.z + int(dz))

    def as_tuple(self) -> tuple[int, int, int]:
        return (self.x, self.y, self.z)


class BuildBounds(BaseModel):
    """Axis-aligned inclusive bounds in absolute coordinates."""

    min: BuildPoint
    max: BuildPoint

    model_config = ConfigDict(frozen=True)

    @property
    def volume(self) -> int:
        return (
            (self.max.x - self.min.x + 1)
            * (self.max.y - self.min.y + 1)
            * (self.max.z - self.min.z + 1)
        )


class BuildPalette(BaseModel):
    """Logical token aliases used by a build plan."""

    values: Dict[str, str] = Field(default_factory=dict)

    model_config = ConfigDict(frozen=True)

    @model_validator(mode="before")
    @classmethod
    def _coerce_values(cls, value: Any) -> Any:
        if isinstance(value, Mapping):
            if "values" in value:
                values = value["values"]
                if not isinstance(values, Mapping):
                    raise ValueError("Palette values must be a map of alias -> block token.")
                return {"values": {str(key): str(token) for key, token in values.items()}}
            return {"values": {str(key): str(token) for key, token in value.items()}}
        if isinstance(value, BuildPalette):
            return {"values": dict(value.values)}
        raise ValueError("Palette must be a map of alias -> block token.")


class BuildAnchors(BaseModel):
    """Named anchor points for anchor coordinate mode."""

    values: Dict[str, BuildPoint] = Field(default_factory=dict)

    model_config = ConfigDict(frozen=True)

    @model_validator(mode="before")
    @classmethod
    def _coerce_values(cls, value: Any) -> Any:
        if isinstance(value, BuildAnchors):
            return {"values": dict(value.values)}
        if not isinstance(value, Mapping):
            raise ValueError("Anchors must be a map from anchor name to point.")

        entries = {}
        root = value
        if "values" in value:
            wrapped = value["values"]
            if not isinstance(wrapped, Mapping):
                raise ValueError("Anchors must be a map from anchor name to point.")
            root = wrapped

        for key, point in root.items():
            if key == "values":
                continue
            entries[str(key)] = BuildPoint.model_validate(point)
        return {"values": entries}


class BuildOptions(BaseModel):
    """Preview and compiler options."""

    phase_reorder: bool = False
    dry_run: bool = True
    max_horizontal_offset: int = Field(
        default=32,
        ge=0,
        validation_alias=AliasChoices("maxHorizontalOffset", "max_horizontal_offset"),
    )
    max_vertical_offset: int = Field(
        default=24,
        ge=0,
        validation_alias=AliasChoices("maxVerticalOffset", "max_vertical_offset"),
    )
    max_total_volume: int = Field(
        default=32_768,
        gt=0,
        validation_alias=AliasChoices("maxTotalVolume", "max_total_volume"),
    )

    model_config = ConfigDict(frozen=True)


class BuildBlock(BaseModel):
    """Single block placement."""

    name: str | None = Field(default=None, validation_alias=AliasChoices("name", "label", "phase"))
    pos: BuildPoint = Field(validation_alias=AliasChoices("pos", "position", "location"))
    block: str = Field(validation_alias=AliasChoices("block", "material", "id"))
    properties: Dict[str, str] = Field(default_factory=dict, validation_alias=AliasChoices("properties", "state"))

    model_config = ConfigDict(frozen=True)

    @field_validator("properties", mode="before")
    @classmethod
    def _coerce_properties(cls, value: Any) -> Dict[str, str]:
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            raise ValueError("Properties must be an object map.")
        return {str(k): str(v) for k, v in dict(value).items()}


class BuildCuboid(BaseModel):
    """Inclusive cuboid fill operation."""

    name: str | None = Field(default=None, validation_alias=AliasChoices("name", "label"))
    from_: BuildPoint = Field(validation_alias=AliasChoices("from", "start", "origin", "offset"))
    to: BuildPoint = Field(validation_alias=AliasChoices("to", "end"))
    block: str = Field(validation_alias=AliasChoices("block", "material", "id"))
    fill_mode: str | None = Field(default=None, validation_alias=AliasChoices("fill", "mode"))
    hollow: bool | None = None
    properties: Dict[str, str] = Field(default_factory=dict, validation_alias=AliasChoices("properties", "state"))

    model_config = ConfigDict(frozen=True)

    @field_validator("properties", mode="before")
    @classmethod
    def _coerce_properties(cls, value: Any) -> Dict[str, str]:
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            raise ValueError("Properties must be an object map.")
        return {str(k): str(v) for k, v in dict(value).items()}

    @model_validator(mode="after")
    def _normalize_bounds(self) -> "BuildCuboid":
        if self.from_ is None or self.to is None:
            raise ValueError("Cuboid requires both from and to coordinates.")
        return self


class BuildStep(BaseModel):
    """A phased step within a build plan."""

    phase: str = Field(default="phase", validation_alias=AliasChoices("phase", "name", "label", "step"))
    plan: Any | None = None

    @model_validator(mode="before")
    @classmethod
    def coerce_step_plan(cls, value: Any) -> Any:
        if not isinstance(value, Mapping):
            return value

        if "plan" in value:
            return value

        bare_plan_fields = {
            "version",
            "coordMode",
            "coordinateMode",
            "coord_mode",
            "mode",
            "origin",
            "offset",
            "rotation",
            "rotate",
            "palette",
            "anchors",
            "options",
            "blocks",
            "cuboids",
            "summary",
            "steps",
        }
        if not any(key in value for key in bare_plan_fields):
            return value

        payload = dict(value)
        phase_name = payload.pop("phase", None)
        if phase_name is not None and "summary" not in payload:
            payload["summary"] = phase_name
        return {"phase": value.get("phase", "phase"), "plan": payload}

    model_config = ConfigDict(frozen=True)


class BuildPlanV2(BaseModel):
    """Primary read-only build-plan model."""

    summary: str | None = Field(
        default=None,
        validation_alias=AliasChoices("summary", "label", "description", "message"),
    )
    coord_mode_explicit: bool = Field(default=False, exclude=True, repr=False)
    version: int = Field(default=2)
    anchor: str | None = None
    coord_mode: CoordinateMode = Field(
        default=CoordinateMode.PLAYER,
        validation_alias=AliasChoices("coordMode", "coordinateMode", "coord_mode", "mode"),
    )
    origin: BuildPoint | None = Field(default=None, validation_alias=AliasChoices("origin", "position"))
    offset: BuildPoint | None = None
    rotation: Rotation = Field(
        default=Rotation.ZERO,
        validation_alias=AliasChoices("rotate", "rotation"),
    )
    palette: BuildPalette = Field(default_factory=BuildPalette)
    anchors: BuildAnchors = Field(default_factory=BuildAnchors)
    options: BuildOptions = Field(default_factory=BuildOptions)
    cuboids: list[BuildCuboid] = Field(default_factory=list)
    blocks: list[BuildBlock] = Field(default_factory=list)
    steps: list[BuildStep] = Field(default_factory=list)

    model_config = ConfigDict(extra="ignore", frozen=True)

    @field_validator("coord_mode", mode="before")
    @classmethod
    def _coerce_coord_mode(cls, value: Any) -> CoordinateMode:
        if isinstance(value, CoordinateMode):
            return value
        return CoordinateMode.normalize(value)

    @field_validator("rotation", mode="before")
    @classmethod
    def _coerce_rotation(cls, value: Any) -> Rotation:
        return Rotation.normalize(value)

    @field_validator("version", mode="before")
    @classmethod
    def _coerce_version(cls, value: Any) -> int:
        if value is None:
            return 2
        if isinstance(value, bool):
            raise ValueError("version must be a positive integer")
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("version must be an integer") from exc

    @field_validator("version", mode="after")
    @classmethod
    def _validate_version(cls, value: int) -> int:
        if value != 2:
            raise ValueError("BuildPlanV2 requires version 2.")
        return value

    @model_validator(mode="after")
    def _normalize_aliases(self) -> "BuildPlanV2":
        return self


class PreviewIssue(BaseModel):
    """One diagnostic item produced by preview compilation."""

    code: str
    message: str
    severity: PreviewIssueSeverity = PreviewIssueSeverity.ERROR
    phase: str | None = None

    model_config = ConfigDict(frozen=True)


class BuildPlanPreview(BaseModel):
    """Deterministic preview output for a build plan."""

    valid: bool
    valid_count: int = 0
    error: str | None = None
    requires_runtime_origin: bool = False
    applied_rotation: int
    phase_count: int
    total_volume: int
    transformed_bounds: BuildBounds | None
    material_counts: Dict[str, int] = Field(default_factory=dict)
    commands: list[str] = Field(default_factory=list)
    issues: list[PreviewIssue] = Field(default_factory=list)

    model_config = ConfigDict(frozen=True)


def _has_explicit_coord_mode(value: Mapping[str, Any]) -> bool:
    return any(key in value for key in ("coordMode", "coordinateMode", "coord_mode", "mode"))


def parse_plan_payload(payload: Any) -> BuildPlanV2:
    """Parse either a direct payload or a {'build_plan': {...}} wrapper."""

    if isinstance(payload, BuildPlanV2):
        return payload

    if isinstance(payload, (bytes, bytearray)):
        payload = bytes(payload).decode("utf-8")

    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise BuildPlanV2Error("Build plan payload must be valid JSON.") from exc

    if not isinstance(payload, Mapping):
        raise BuildPlanV2Error("Build plan payload must be a mapping or JSON text.")

    root = payload
    for wrapper_key in ("build_plan", "plan", "payload", "data"):
        wrapped = root.get(wrapper_key)
        if isinstance(wrapped, Mapping):
            root = wrapped
            break

    if not isinstance(root, Mapping):
        raise BuildPlanV2Error("Build plan payload must be a mapping or JSON text.")

    root_payload = dict(root)
    root_payload.setdefault("coord_mode_explicit", _has_explicit_coord_mode(root_payload))

    try:
        return BuildPlanV2.model_validate(root_payload)
    except ValidationError as exc:
        raise BuildPlanV2Error("Invalid BuildPlanV2 payload.") from exc


__all__ = [
    "BuildPlanV2",
    "BuildPoint",
    "BuildBounds",
    "BuildPalette",
    "BuildAnchors",
    "BuildOptions",
    "BuildBlock",
    "BuildCuboid",
    "BuildStep",
    "BuildPlanPreview",
    "BuildPlanV2Error",
    "CoordinateMode",
    "Rotation",
    "PreviewIssue",
    "PreviewIssueSeverity",
    "parse_plan_payload",
]
