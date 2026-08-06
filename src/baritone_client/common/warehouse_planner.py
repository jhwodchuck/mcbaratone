"""Catalog-backed planning for deterministic warehouse geometry."""

from __future__ import annotations

from typing import Any, Mapping, Sequence, Tuple

from . import harness_ops
from .storage_catalog import StorageCatalog
from .storage_warehouse import WarehouseLayout


Position = Tuple[int, int, int]
DEFAULT_WAREHOUSE_ID = "warehouse_01"
WAREHOUSE_AISLE_WIDTH = 3
WAREHOUSE_EXPANSION_DIRECTION = "positive_local_x"


def _warehouse_id_for_dimension(dimension: str) -> str:
    if str(dimension) == "minecraft:overworld":
        return DEFAULT_WAREHOUSE_ID
    suffix = str(dimension).split(":", 1)[-1].replace("/", "_")
    return f"{DEFAULT_WAREHOUSE_ID}_{suffix}"


def _facing_for_pair(first: Position, second: Position) -> str:
    offset = (second[0] - first[0], second[2] - first[2])
    by_offset = {
        (1, 0): "north",
        (0, 1): "east",
        (-1, 0): "south",
        (0, -1): "west",
    }
    try:
        return by_offset[offset]
    except KeyError as error:
        raise RuntimeError("warehouse anchor pair is not horizontally adjacent") from error


def ensure_warehouse_layout(
    client: Any,
    catalog: StorageCatalog,
    dimension: str,
) -> tuple[dict[str, Any], WarehouseLayout]:
    """Load a dimension's immutable layout or reserve its safe anchor."""
    matching = [
        record
        for record in catalog.list_warehouses()
        if str(record["dimension"]) == str(dimension)
    ]
    if matching:
        record = matching[0]
    else:
        spot = harness_ops.find_double_chest_spot(client, dry_only=True)
        if spot is None:
            raise RuntimeError("could not find a supported warehouse anchor pair")
        first, second = spot
        record = catalog.register_warehouse(
            _warehouse_id_for_dimension(dimension),
            dimension=dimension,
            anchor=first,
            facing=_facing_for_pair(first, second),
            expansion_direction=WAREHOUSE_EXPANSION_DIRECTION,
            aisle_width=WAREHOUSE_AISLE_WIDTH,
            metadata={"floor": 0, "layout_version": 1},
        )
    if str(record["expansion_direction"]) != WAREHOUSE_EXPANSION_DIRECTION:
        raise RuntimeError("warehouse expansion direction is unsupported")
    layout = WarehouseLayout(
        tuple(record["anchor"]),
        str(record["facing"]),
        aisle_width=int(record["aisle_width"]),
    )
    return record, layout


def reserve_category_slot(
    catalog: StorageCatalog,
    warehouse: Mapping[str, Any],
    layout: WarehouseLayout,
    category: str,
) -> dict[str, Any]:
    """Resume the frontier reservation or allocate its next stable index."""
    warehouse_id = str(warehouse["warehouse_id"])
    zone = "intake" if category == "intake" else "category"
    matching = [
        reservation
        for reservation in catalog.list_slot_reservations(warehouse_id)
        if reservation["zone"] == zone and reservation["category"] == category
    ]
    planned = next(
        (
            reservation
            for reservation in matching
            if reservation["state"] in {"planned", "building"}
        ),
        None,
    )
    if planned is not None:
        return planned
    if any(reservation["state"] == "blocked" for reservation in matching):
        raise RuntimeError(f"the planned {category} warehouse frontier is blocked")
    index = 0 if category == "intake" else 1 + max(
        (int(reservation["slot_index"]) for reservation in matching),
        default=-1,
    )
    slot = layout.slot(category, index)
    return catalog.reserve_slot(
        warehouse_id,
        slot.zone,
        category,
        index,
        paired_coordinates=slot.coordinates,
        canonical_coordinate=slot.canonical_coordinate,
        metadata={**slot.metadata(), "layout_version": 1},
    )


def planned_slot_is_obstructed(
    client: Any,
    coordinates: Sequence[Position],
    *,
    allowed_existing: Sequence[Position] = (),
) -> bool:
    """Return whether an exact pair is occupied or lacks solid support."""
    allowed = {tuple(position) for position in allowed_existing}
    for position in coordinates:
        block_id = harness_ops._block_at(client, *position)
        if block_id == "minecraft:chest":
            if tuple(position) in allowed:
                continue
            return True
        if "chest" in block_id:
            return True
        if "air" not in block_id:
            return True
        support = harness_ops._block_at(
            client, position[0], position[1] - 1, position[2]
        )
        if not harness_ops._is_solid_support_block(support):
            return True
    return False


def mark_slot_blocked(
    catalog: StorageCatalog,
    warehouse: Mapping[str, Any],
    reservation: Mapping[str, Any],
    reason: str,
    *,
    metadata: Mapping[str, Any] | None = None,
) -> None:
    """Persist a bounded placement failure without changing reserved geometry."""
    catalog.update_slot_reservation_state(
        str(warehouse["warehouse_id"]),
        str(reservation["zone"]),
        str(reservation["category"]),
        int(reservation["slot_index"]),
        "blocked",
        metadata={**dict(metadata or reservation["metadata"]), "blocked_reason": reason},
    )


__all__ = [
    "DEFAULT_WAREHOUSE_ID",
    "ensure_warehouse_layout",
    "mark_slot_blocked",
    "planned_slot_is_obstructed",
    "reserve_category_slot",
]
