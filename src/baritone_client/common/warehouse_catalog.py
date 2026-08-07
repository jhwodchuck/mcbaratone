"""Warehouse layout persistence mixed into the shared storage catalog."""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple


WAREHOUSE_SCHEMA = """
CREATE TABLE IF NOT EXISTS warehouses (
    world_id TEXT NOT NULL,
    warehouse_id TEXT NOT NULL,
    dimension TEXT NOT NULL,
    anchor_x INTEGER NOT NULL,
    anchor_y INTEGER NOT NULL,
    anchor_z INTEGER NOT NULL,
    facing TEXT NOT NULL CHECK(facing IN ('north', 'east', 'south', 'west')),
    expansion_direction TEXT NOT NULL,
    aisle_width INTEGER NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (world_id, warehouse_id)
);
CREATE TABLE IF NOT EXISTS warehouse_slot_reservations (
    world_id TEXT NOT NULL,
    warehouse_id TEXT NOT NULL,
    zone TEXT NOT NULL,
    category TEXT NOT NULL,
    slot_index INTEGER NOT NULL,
    first_x INTEGER NOT NULL,
    first_y INTEGER NOT NULL,
    first_z INTEGER NOT NULL,
    second_x INTEGER NOT NULL,
    second_y INTEGER NOT NULL,
    second_z INTEGER NOT NULL,
    canonical_x INTEGER NOT NULL,
    canonical_y INTEGER NOT NULL,
    canonical_z INTEGER NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('planned', 'building', 'verified', 'blocked', 'retired')),
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (world_id, warehouse_id, zone, category, slot_index),
    FOREIGN KEY (world_id, warehouse_id)
        REFERENCES warehouses(world_id, warehouse_id)
        ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_warehouse_slot_reservations_lookup
    ON warehouse_slot_reservations(
        world_id, warehouse_id, zone, category, slot_index
    );
"""


def migrate_warehouse_schema(db: sqlite3.Connection) -> None:
    """Upgrade the early reservation table so partial builds can be recorded."""
    row = db.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' "
        "AND name='warehouse_slot_reservations'"
    ).fetchone()
    if row is None or "'building'" in str(row[0] or ""):
        return
    db.execute(
        "ALTER TABLE warehouse_slot_reservations "
        "RENAME TO warehouse_slot_reservations_v2"
    )
    db.execute("DROP INDEX IF EXISTS idx_warehouse_slot_reservations_lookup")
    db.executescript(WAREHOUSE_SCHEMA)
    db.execute(
        """INSERT INTO warehouse_slot_reservations(
               world_id, warehouse_id, zone, category, slot_index,
               first_x, first_y, first_z, second_x, second_y, second_z,
               canonical_x, canonical_y, canonical_z, state, created_at,
               updated_at, metadata_json
           )
           SELECT world_id, warehouse_id, zone, category, slot_index,
               first_x, first_y, first_z, second_x, second_y, second_z,
               canonical_x, canonical_y, canonical_z, state, created_at,
               updated_at, metadata_json
           FROM warehouse_slot_reservations_v2"""
    )
    db.execute("DROP TABLE warehouse_slot_reservations_v2")


class WarehouseCatalogMixin:
    """World-scoped warehouse definitions and immutable slot reservations."""

    world_id: str

    @staticmethod
    def _warehouse_row(row: Optional[sqlite3.Row]) -> Optional[Dict[str, Any]]:
        if row is None:
            return None
        return {
            "warehouse_id": str(row["warehouse_id"]),
            "dimension": str(row["dimension"]),
            "anchor": (
                int(row["anchor_x"]),
                int(row["anchor_y"]),
                int(row["anchor_z"]),
            ),
            "facing": str(row["facing"]),
            "expansion_direction": str(row["expansion_direction"]),
            "aisle_width": int(row["aisle_width"]),
            "created_at": float(row["created_at"]),
            "updated_at": float(row["updated_at"]),
            "metadata": json.loads(row["metadata_json"]),
        }

    @staticmethod
    def _reservation_row(row: Optional[sqlite3.Row]) -> Optional[Dict[str, Any]]:
        if row is None:
            return None
        return {
            "warehouse_id": str(row["warehouse_id"]),
            "zone": str(row["zone"]),
            "category": str(row["category"]),
            "slot_index": int(row["slot_index"]),
            "paired_coordinates": (
                (
                    int(row["first_x"]),
                    int(row["first_y"]),
                    int(row["first_z"]),
                ),
                (
                    int(row["second_x"]),
                    int(row["second_y"]),
                    int(row["second_z"]),
                ),
            ),
            "canonical_coordinate": (
                int(row["canonical_x"]),
                int(row["canonical_y"]),
                int(row["canonical_z"]),
            ),
            "state": str(row["state"]),
            "created_at": float(row["created_at"]),
            "updated_at": float(row["updated_at"]),
            "metadata": json.loads(row["metadata_json"]),
        }

    def register_warehouse(
        self,
        warehouse_id: str,
        *,
        dimension: str,
        anchor: Tuple[int, int, int],
        facing: str,
        expansion_direction: str,
        aisle_width: int,
        metadata: Optional[Mapping[str, Any]] = None,
        updated_at: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Create an immutable warehouse geometry or refresh its metadata."""
        normalized_facing = str(facing).lower()
        if normalized_facing not in {"north", "east", "south", "west"}:
            raise ValueError("facing must be north, east, south, or west")
        if int(aisle_width) < 3:
            raise ValueError("aisle_width must be at least 3")
        x, y, z = (int(value) for value in anchor)
        observed = float(updated_at or time.time())
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT * FROM warehouses WHERE world_id=? AND warehouse_id=?",
                (self.world_id, str(warehouse_id)),
            ).fetchone()
            if existing is not None:
                persisted = (
                    str(existing["dimension"]),
                    int(existing["anchor_x"]),
                    int(existing["anchor_y"]),
                    int(existing["anchor_z"]),
                    str(existing["facing"]),
                    str(existing["expansion_direction"]),
                    int(existing["aisle_width"]),
                )
                requested = (
                    str(dimension),
                    x,
                    y,
                    z,
                    normalized_facing,
                    str(expansion_direction),
                    int(aisle_width),
                )
                if persisted != requested:
                    raise ValueError(
                        "warehouse geometry is immutable; use an explicit relocation workflow"
                    )
            db.execute(
                """
                INSERT INTO warehouses(
                    world_id, warehouse_id, dimension, anchor_x, anchor_y, anchor_z,
                    facing, expansion_direction, aisle_width, created_at, updated_at,
                    metadata_json
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(world_id, warehouse_id) DO UPDATE SET
                    updated_at=excluded.updated_at,
                    metadata_json=excluded.metadata_json
                """,
                (
                    self.world_id,
                    str(warehouse_id),
                    str(dimension),
                    x,
                    y,
                    z,
                    normalized_facing,
                    str(expansion_direction),
                    int(aisle_width),
                    observed,
                    observed,
                    json.dumps(dict(metadata or {}), sort_keys=True),
                ),
            )
        result = self.get_warehouse(warehouse_id)
        assert result is not None
        return result

    def get_warehouse(self, warehouse_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM warehouses WHERE world_id=? AND warehouse_id=?",
                (self.world_id, str(warehouse_id)),
            ).fetchone()
        return self._warehouse_row(row)

    def list_warehouses(self) -> list[Dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM warehouses WHERE world_id=? ORDER BY warehouse_id",
                (self.world_id,),
            ).fetchall()
        return [self._warehouse_row(row) for row in rows]

    def retire_warehouse(
        self,
        warehouse_id: str,
        *,
        reason: str,
        replaced_by: str,
        updated_at: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Retire a warehouse without rewriting its immutable geometry.

        A replacement must have a distinct identity.  Keeping the original
        record and its reservations lets operators audit why it was abandoned,
        while keeping ``register_warehouse``'s geometry guarantee intact.
        Callers must establish that no placed or populated storage exists
        before using this lifecycle transition.
        """
        warehouse = self.get_warehouse(warehouse_id)
        if warehouse is None:
            raise KeyError("warehouse is not registered in this world")
        metadata = dict(warehouse["metadata"])
        metadata.update(
            {
                "lifecycle": "retired",
                "retired_reason": str(reason),
                "replaced_by": str(replaced_by),
                "retired_at": float(updated_at or time.time()),
            }
        )
        return self.register_warehouse(
            str(warehouse["warehouse_id"]),
            dimension=str(warehouse["dimension"]),
            anchor=tuple(warehouse["anchor"]),
            facing=str(warehouse["facing"]),
            expansion_direction=str(warehouse["expansion_direction"]),
            aisle_width=int(warehouse["aisle_width"]),
            metadata=metadata,
            updated_at=updated_at,
        )

    def reserve_slot(
        self,
        warehouse_id: str,
        zone: str,
        category: str,
        slot_index: int,
        *,
        paired_coordinates: Sequence[Tuple[int, int, int]],
        canonical_coordinate: Tuple[int, int, int],
        state: str = "planned",
        metadata: Optional[Mapping[str, Any]] = None,
        updated_at: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Reserve immutable coordinates, rejecting same-dimension collisions."""
        if str(state) not in {"planned", "building", "verified", "blocked", "retired"}:
            raise ValueError("invalid reservation state")
        try:
            first, second = paired_coordinates
            first = tuple(int(value) for value in first)
            second = tuple(int(value) for value in second)
            canonical = tuple(int(value) for value in canonical_coordinate)
        except (TypeError, ValueError):
            raise ValueError(
                "paired and canonical coordinates must be xyz triples"
            ) from None
        if len(first) != 3 or len(second) != 3 or len(canonical) != 3:
            raise ValueError("paired and canonical coordinates must be xyz triples")
        if canonical not in (first, second):
            raise ValueError("canonical coordinate must name one coordinate in the pair")
        if (
            first[1] != second[1]
            or abs(first[0] - second[0]) + abs(first[2] - second[2]) != 1
        ):
            raise ValueError("reserved chest coordinates must be horizontally adjacent")
        key = (
            self.world_id,
            str(warehouse_id),
            str(zone),
            str(category),
            int(slot_index),
        )
        observed = float(updated_at or time.time())
        coordinates = (first, second, canonical)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            warehouse = db.execute(
                "SELECT dimension FROM warehouses WHERE world_id=? AND warehouse_id=?",
                key[:2],
            ).fetchone()
            if warehouse is None:
                raise KeyError("warehouse is not registered in this world")
            existing = db.execute(
                """SELECT * FROM warehouse_slot_reservations
                   WHERE world_id=? AND warehouse_id=? AND zone=?
                     AND category=? AND slot_index=?""",
                key,
            ).fetchone()
            if existing is not None:
                persisted_coordinates = (
                    (
                        int(existing["first_x"]),
                        int(existing["first_y"]),
                        int(existing["first_z"]),
                    ),
                    (
                        int(existing["second_x"]),
                        int(existing["second_y"]),
                        int(existing["second_z"]),
                    ),
                    (
                        int(existing["canonical_x"]),
                        int(existing["canonical_y"]),
                        int(existing["canonical_z"]),
                    ),
                )
                if persisted_coordinates != (first, second, canonical):
                    raise ValueError(
                        "reserved slot coordinates are immutable; reserve a new slot index"
                    )
            rows = db.execute(
                """SELECT r.* FROM warehouse_slot_reservations r
                   JOIN warehouses w
                     ON w.world_id=r.world_id AND w.warehouse_id=r.warehouse_id
                   WHERE r.world_id=? AND w.dimension=?
                     AND NOT (r.warehouse_id=? AND r.zone=?
                              AND r.category=? AND r.slot_index=?)""",
                (self.world_id, str(warehouse["dimension"]), *key[1:]),
            ).fetchall()
            reserved = {
                (
                    int(row[column]),
                    int(row[column.replace("_x", "_y")]),
                    int(row[column.replace("_x", "_z")]),
                )
                for row in rows
                for column in ("first_x", "second_x", "canonical_x")
            }
            if any(coordinate in reserved for coordinate in coordinates):
                raise ValueError("coordinate is already reserved in this world")
            db.execute(
                """
                INSERT INTO warehouse_slot_reservations(
                    world_id, warehouse_id, zone, category, slot_index,
                    first_x, first_y, first_z, second_x, second_y, second_z,
                    canonical_x, canonical_y, canonical_z, state, created_at,
                    updated_at, metadata_json
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(world_id, warehouse_id, zone, category, slot_index)
                DO UPDATE SET state=excluded.state, updated_at=excluded.updated_at,
                    metadata_json=excluded.metadata_json
                """,
                (
                    *key,
                    *first,
                    *second,
                    *canonical,
                    str(state),
                    observed,
                    observed,
                    json.dumps(dict(metadata or {}), sort_keys=True),
                ),
            )
        result = self.get_slot_reservation(
            warehouse_id, zone, category, slot_index
        )
        assert result is not None
        return result

    def get_slot_reservation(
        self,
        warehouse_id: str,
        zone: str,
        category: str,
        slot_index: int,
    ) -> Optional[Dict[str, Any]]:
        with self._connect() as db:
            row = db.execute(
                """SELECT * FROM warehouse_slot_reservations WHERE world_id=?
                   AND warehouse_id=? AND zone=? AND category=? AND slot_index=?""",
                (
                    self.world_id,
                    str(warehouse_id),
                    str(zone),
                    str(category),
                    int(slot_index),
                ),
            ).fetchone()
        return self._reservation_row(row)

    def list_slot_reservations(
        self, warehouse_id: str
    ) -> list[Dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                """SELECT * FROM warehouse_slot_reservations
                   WHERE world_id=? AND warehouse_id=?
                   ORDER BY zone, category, slot_index""",
                (self.world_id, str(warehouse_id)),
            ).fetchall()
        return [self._reservation_row(row) for row in rows]

    def update_slot_reservation_state(
        self,
        warehouse_id: str,
        zone: str,
        category: str,
        slot_index: int,
        state: str,
        *,
        metadata: Optional[Mapping[str, Any]] = None,
        updated_at: Optional[float] = None,
    ) -> Optional[Dict[str, Any]]:
        """Update a reservation lifecycle state without moving its geometry."""
        if str(state) not in {"planned", "building", "verified", "blocked", "retired"}:
            raise ValueError("invalid reservation state")
        observed = float(updated_at or time.time())
        key = (
            self.world_id,
            str(warehouse_id),
            str(zone),
            str(category),
            int(slot_index),
        )
        with self._connect() as db:
            if metadata is None:
                db.execute(
                    """UPDATE warehouse_slot_reservations
                       SET state=?, updated_at=? WHERE world_id=?
                       AND warehouse_id=? AND zone=? AND category=?
                       AND slot_index=?""",
                    (str(state), observed, *key),
                )
            else:
                db.execute(
                    """UPDATE warehouse_slot_reservations
                       SET state=?, updated_at=?, metadata_json=? WHERE world_id=?
                       AND warehouse_id=? AND zone=? AND category=?
                       AND slot_index=?""",
                    (
                        str(state),
                        observed,
                        json.dumps(dict(metadata), sort_keys=True),
                        *key,
                    ),
                )
        return self.get_slot_reservation(
            warehouse_id, zone, category, slot_index
        )


__all__ = [
    "WAREHOUSE_SCHEMA",
    "WarehouseCatalogMixin",
    "migrate_warehouse_schema",
]
