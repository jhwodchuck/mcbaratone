"""Pure, deterministic geometry for a one-floor storage warehouse.

The layout deliberately knows nothing about Minecraft clients or item catalogs.
It only assigns stable coordinates to labelled double-chest slots.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final, Tuple


Coordinate = Tuple[int, int, int]
Facing = str

CATEGORY_ORDER: Final[Tuple[str, ...]] = (
    "intake",
    "building",
    "wood",
    "ores",
    "food",
    "mob_drops",
    "tools",
    "rare",
    "misc",
)

_LOCAL_AXES: Final[dict[Facing, tuple[tuple[int, int], tuple[int, int]]]] = {
    # (local X, local Z).  Local Z points in the direction the warehouse faces.
    "north": ((1, 0), (0, -1)),
    "east": ((0, 1), (1, 0)),
    "south": ((-1, 0), (0, 1)),
    "west": ((0, -1), (-1, 0)),
}


@dataclass(frozen=True)
class PlannedDoubleChestSlot:
    """A labelled pair of horizontally adjacent chest-block coordinates."""

    zone: str
    category: str
    row: int
    index: int
    canonical_coordinate: Coordinate
    paired_coordinate: Coordinate

    @property
    def coordinates(self) -> Tuple[Coordinate, Coordinate]:
        """Return canonical then paired coordinates in their stable order."""
        return (self.canonical_coordinate, self.paired_coordinate)

    def metadata(self) -> dict[str, object]:
        """Return portable, explicit metadata for a catalog or sign label."""
        return {
            "zone": self.zone,
            "category": self.category,
            "row": self.row,
            "index": self.index,
            "canonical_coordinate": self.canonical_coordinate,
            "paired_coordinate": self.paired_coordinate,
        }


@dataclass(frozen=True)
class WarehouseLayout:
    """Immutable coordinate contract for one floor of double-chest storage."""

    anchor: Coordinate
    facing: Facing
    aisle_width: int = 3
    categories: Tuple[str, ...] = field(default=CATEGORY_ORDER)

    def __post_init__(self) -> None:
        if self.facing not in _LOCAL_AXES:
            raise ValueError("facing must be north, east, south, or west")
        if self.aisle_width < 3:
            raise ValueError("aisle_width must be at least 3")
        if len(self.anchor) != 3:
            raise ValueError("anchor must contain x, y, and z")
        if not self.categories:
            raise ValueError("categories must not be empty")
        if self.categories[0] != "intake":
            raise ValueError("intake must be category row 0")
        if len(set(self.categories)) != len(self.categories):
            raise ValueError("categories must be unique")

    @property
    def row_pitch(self) -> int:
        """Distance between category rows, including the clear aisle."""
        return self.aisle_width + 1

    @property
    def pair_pitch(self) -> int:
        """Distance to the next capacity pair: two chests and one separator."""
        return 3

    def category_row(self, category: str) -> int:
        """Return the deterministic row for a configured broad category."""
        try:
            return self.categories.index(category)
        except ValueError as error:
            raise ValueError(f"unknown warehouse category: {category}") from error

    def slot(self, category: str, index: int = 0) -> PlannedDoubleChestSlot:
        """Plan a chest pair by category and zero-based capacity index."""
        if index < 0:
            raise ValueError("index must be nonnegative")
        row = self.category_row(category)
        local_x, local_z = _LOCAL_AXES[self.facing]
        x = self.anchor[0] + local_x[0] * index * self.pair_pitch + local_z[0] * row * self.row_pitch
        z = self.anchor[2] + local_x[1] * index * self.pair_pitch + local_z[1] * row * self.row_pitch
        canonical = (x, self.anchor[1], z)
        paired = (x + local_x[0], self.anchor[1], z + local_x[1])
        return PlannedDoubleChestSlot(
            zone="intake" if row == 0 else "category",
            category=category,
            row=row,
            index=index,
            canonical_coordinate=canonical,
            paired_coordinate=paired,
        )

    def coordinate_metadata(self, coordinate: Coordinate) -> dict[str, object] | None:
        """Invert a planned coordinate to its slot metadata, or return ``None``.

        Separator and aisle coordinates intentionally have no metadata.
        """
        if len(coordinate) != 3 or coordinate[1] != self.anchor[1]:
            return None
        local_x, local_z = _LOCAL_AXES[self.facing]
        dx, dz = coordinate[0] - self.anchor[0], coordinate[2] - self.anchor[2]
        local_x_offset = dx * local_x[0] + dz * local_x[1]
        local_z_offset = dx * local_z[0] + dz * local_z[1]
        if local_z_offset < 0 or local_z_offset % self.row_pitch:
            return None
        row = local_z_offset // self.row_pitch
        if row >= len(self.categories) or local_x_offset < 0:
            return None
        pair_offset = local_x_offset % self.pair_pitch
        if pair_offset not in (0, 1):
            return None
        index = local_x_offset // self.pair_pitch
        return self.slot(self.categories[row], index).metadata()
