"""Compatibility layer for historical block-list JSON schematics."""

from .models import (
    Coordinate,
    LegacyJsonBlockInput,
    LegacyJsonLimits,
    LegacyJsonSchematicError,
    LegacyJsonSchematicInput,
)
from .parser import (
    DEFAULT_MAX_BLOCKS,
    DEFAULT_MAX_DIMENSION,
    LegacyJsonSchematicCollection,
    LegacyPlacementPlan,
    LegacyPlacementStep,
    ParsedLegacyBlock,
    ParsedLegacySchematic,
    parse_legacy_json_schematic,
    parse_legacy_json_schematics,
)

__all__ = [
    "Coordinate",
    "LegacyJsonBlockInput",
    "LegacyJsonLimits",
    "LegacyJsonSchematicError",
    "LegacyJsonSchematicInput",
    "DEFAULT_MAX_BLOCKS",
    "DEFAULT_MAX_DIMENSION",
    "LegacyJsonSchematicCollection",
    "LegacyPlacementPlan",
    "LegacyPlacementStep",
    "ParsedLegacyBlock",
    "ParsedLegacySchematic",
    "parse_legacy_json_schematic",
    "parse_legacy_json_schematics",
]
