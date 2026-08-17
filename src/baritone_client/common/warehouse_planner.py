"""Catalog-backed planning for deterministic warehouse geometry."""

from __future__ import annotations

import time
from typing import Any, Mapping, Sequence, Tuple

from . import harness_ops
from .storage_catalog import StorageCatalog
from .terraform_verify import classify_block
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


def _active_warehouse(record: Mapping[str, Any]) -> bool:
    return str(dict(record.get("metadata", {})).get("lifecycle", "active")) != "retired"


def _replacement_warehouse_id(
    catalog: StorageCatalog,
    dimension: str,
) -> str:
    """Return a new immutable identity without reusing the failed layout."""
    base = _warehouse_id_for_dimension(dimension)
    existing = {str(record["warehouse_id"]) for record in catalog.list_warehouses()}
    index = 1
    while f"{base}_rehome_{index}" in existing:
        index += 1
    return f"{base}_rehome_{index}"


#: How close the bot must be for an absent block read to mean "nothing is
#: there" rather than "that chunk is not loaded". Four chunks sits well inside
#: any server's render distance, so a void read within it is trustworthy.
LOADED_EVIDENCE_RADIUS = 64.0


def _within_loaded_radius(client: Any, coordinates: Sequence[Position]) -> bool:
    """Return whether every coordinate is near enough to have been observed."""
    try:
        state = client.transport.dispatch("get_state", {})
        position = state.get("block_position", state.get("position", {}))
        px = float(position["x"])
        py = float(position["y"])
        pz = float(position["z"])
    except Exception:
        # No position means no way to know whether anything was loaded, and a
        # retirement here is permanent. Withhold the evidence.
        return False
    for x, y, z in coordinates:
        distance = ((px - x) ** 2 + (py - y) ** 2 + (pz - z) ** 2) ** 0.5
        if distance > LOADED_EVIDENCE_RADIUS:
            return False
    return True


def _pair_is_unplaced_and_unsupported(client: Any, coordinates: Sequence[Position]) -> bool:
    """Prove this planned chest pair was never placed on usable ground.

    This is intentionally stricter than a failed placement check: a normal
    empty pair above solid ground is a valid unfinished warehouse and must not
    be retired.  Both target blocks must still be placeable air and both
    supports must be absent/non-solid.  A chest, any other block, or a solid
    support leaves the immutable layout in place for an operator to
    investigate.

    ``void_air`` is ambiguous: it means "genuinely nothing there" for a loaded
    chunk and "not looked at" for an unloaded one. Distance is what separates
    them, so a void read only counts as evidence while the bot is close enough
    for the chunk to be loaded. Without that gate every warehouse merely out of
    render distance scored as "never placed" -- ``ensure_warehouse_layout``
    calls this on every layout load without travelling to the anchor first --
    and the whole immutable layout was retired, with a replacement registered
    wherever the bot happened to be standing.
    """
    if not _within_loaded_radius(client, coordinates):
        return False
    for position in coordinates:
        target = harness_ops._block_at(client, *position)
        support = harness_ops._block_at(client, position[0], position[1] - 1, position[2])
        if "air" not in target or harness_ops._is_solid_support_block(support):
            return False
    return True


def rehome_abandoned_warehouse(
    client: Any,
    catalog: StorageCatalog,
    warehouse: Mapping[str, Any],
    dimension: str,
) -> Mapping[str, Any] | None:
    """Safely replace an unplaced warehouse whose full footprint is void.

    No database-only recovery is permitted.  The old layout stays immutable
    and is retired only after exact live block evidence proves every reserved
    chest pair (including intake) has neither a chest nor solid support.  A
    supported replacement pair is found *before* mutating the catalog.
    """
    reservations = catalog.list_slot_reservations(str(warehouse["warehouse_id"]))
    if any(item["state"] in {"building", "verified"} for item in reservations):
        return None
    layout = WarehouseLayout(
        tuple(warehouse["anchor"]), str(warehouse["facing"]),
        aisle_width=int(warehouse["aisle_width"]),
    )
    pairs = [layout.slot("intake", 0).coordinates]
    pairs.extend(item["paired_coordinates"] for item in reservations)
    if not all(_pair_is_unplaced_and_unsupported(client, pair) for pair in pairs):
        return None
    spot = harness_ops.find_double_chest_spot(client, dry_only=True)
    if spot is None:
        return None
    first, second = spot
    replacement_id = _replacement_warehouse_id(catalog, dimension)
    observed = time.time()
    catalog.retire_warehouse(
        str(warehouse["warehouse_id"]),
        reason="all planned chest pairs were unplaced and unsupported",
        replaced_by=replacement_id,
        updated_at=observed,
    )
    return catalog.register_warehouse(
        replacement_id,
        dimension=dimension,
        anchor=first,
        facing=_facing_for_pair(first, second),
        expansion_direction=WAREHOUSE_EXPANSION_DIRECTION,
        aisle_width=WAREHOUSE_AISLE_WIDTH,
        metadata={
            "floor": 0,
            "layout_version": 1,
            "lifecycle": "active",
            "replaces": str(warehouse["warehouse_id"]),
        },
        updated_at=observed,
    )


def ensure_warehouse_layout(
    client: Any,
    catalog: StorageCatalog,
    dimension: str,
) -> tuple[dict[str, Any], WarehouseLayout]:
    """Load a dimension's immutable layout or reserve its safe anchor."""
    matching = [
        record
        for record in catalog.list_warehouses()
        if str(record["dimension"]) == str(dimension) and _active_warehouse(record)
    ]
    if matching:
        record = matching[0]
        replacement = rehome_abandoned_warehouse(client, catalog, record, dimension)
        if replacement is not None:
            record = replacement
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
    """Return whether an exact pair is occupied or lacks solid support.

    An unloaded read reports "not obstructed". That is deliberate: callers
    persist a positive as ``blocked`` reservation state, and a slot retired on
    a coordinate nobody looked at stays retired. Being wrong the other way is
    cheap and self-correcting -- the placement is attempted, ``place_block_exact``
    verifies it, and a genuine obstruction simply fails that attempt.
    """
    allowed = {tuple(position) for position in allowed_existing}
    for position in coordinates:
        block_id = harness_ops._block_at(client, *position)
        if classify_block(block_id) == "unknown":
            return False
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
        if classify_block(support) == "unknown":
            return False
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
    "rehome_abandoned_warehouse",
    "reserve_category_slot",
]
