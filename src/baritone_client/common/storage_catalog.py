"""Persistent, world-scoped catalog of containers and last-known contents.

Fleet workers share one catalog so a container observed by one bot is available
to every bot in the same world.  Standalone runs retain a private catalog.
SQLite provides atomic updates while controllers and monitors access it.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, Mapping, Optional, Sequence, Tuple

from .warehouse_catalog import (
    WAREHOUSE_SCHEMA,
    WarehouseCatalogMixin,
    migrate_warehouse_schema,
)


SCHEMA_VERSION = 3
DEFAULT_CATALOG_NAME = "storage_catalog.sqlite3"
SHARED_CATALOG_ENV = "MC_SHARED_STORAGE_CATALOG"
FLEET_DIRECTORY_NAMES = {"aternos", "headlessmc"}


def _run_directory(state=None) -> Path:
    if state is not None and getattr(state, "checkpoint_dir", None):
        return Path(state.checkpoint_dir).resolve()
    return Path(os.environ.get("MC_RUN_DIR", Path.cwd())).resolve()


def _checkpoint_world_id(run_dir: Path) -> Optional[str]:
    checkpoint = run_dir / "spawn_to_dragon_checkpoint.json"
    try:
        raw = json.loads(checkpoint.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    identity = raw.get("world_identity")
    if isinstance(identity, Mapping) and identity.get("stable_hash"):
        return str(identity["stable_hash"])
    if raw.get("world_signature"):
        return str(raw["world_signature"])
    return None


def _world_id(state, run_dir: Path) -> str:
    identity = getattr(state, "bound_world_identity", None) if state else None
    stable_hash = getattr(identity, "stable_hash", None)
    return str(stable_hash or _checkpoint_world_id(run_dir) or "unscoped-world")


def _catalog_path(run_dir: Path) -> Path:
    """Return a shared fleet path when ``run_dir`` has a known bot layout."""
    override = os.environ.get(SHARED_CATALOG_ENV)
    if override:
        return Path(override).resolve()

    bot_dir = run_dir.parent if run_dir.name == "controller" else run_dir
    fleet_dir = bot_dir.parent
    if (
        bot_dir.name.lower().startswith("bot")
        and bot_dir.name[3:].isdigit()
        and fleet_dir.name.lower() in FLEET_DIRECTORY_NAMES
    ):
        return fleet_dir / "shared" / DEFAULT_CATALOG_NAME
    return run_dir / DEFAULT_CATALOG_NAME


def _dimension(client) -> str:
    try:
        state = client.transport.dispatch("get_state", {})
        return str(state.get("dimension") or "minecraft:overworld")
    except Exception:
        return "minecraft:overworld"


class StorageCatalog(WarehouseCatalogMixin):
    """SQLite-backed last-known-state catalog for world containers."""

    def __init__(self, path: Path, world_id: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.world_id = str(world_id)
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(str(self.path), timeout=10.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 10000")
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as db:
            db.execute("PRAGMA journal_mode = WAL")
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS catalog_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS containers (
                    world_id TEXT NOT NULL,
                    dimension TEXT NOT NULL,
                    x INTEGER NOT NULL,
                    y INTEGER NOT NULL,
                    z INTEGER NOT NULL,
                    container_type TEXT NOT NULL,
                    label TEXT,
                    purpose TEXT,
                    capacity_slots INTEGER,
                    occupied_slots INTEGER,
                    total_items INTEGER,
                    last_seen REAL NOT NULL,
                    last_inventory_scan REAL,
                    status TEXT NOT NULL DEFAULT 'known',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    PRIMARY KEY (world_id, dimension, x, y, z)
                );
                CREATE TABLE IF NOT EXISTS container_items (
                    world_id TEXT NOT NULL,
                    dimension TEXT NOT NULL,
                    x INTEGER NOT NULL,
                    y INTEGER NOT NULL,
                    z INTEGER NOT NULL,
                    slot INTEGER NOT NULL,
                    item_id TEXT NOT NULL,
                    count INTEGER NOT NULL,
                    max_count INTEGER,
                    damage INTEGER,
                    observed_at REAL NOT NULL,
                    PRIMARY KEY (world_id, dimension, x, y, z, slot),
                    FOREIGN KEY (world_id, dimension, x, y, z)
                        REFERENCES containers (world_id, dimension, x, y, z)
                        ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_container_items_lookup
                    ON container_items(world_id, item_id, count);
                CREATE INDEX IF NOT EXISTS idx_containers_freshness
                    ON containers(world_id, last_inventory_scan);
                CREATE TABLE IF NOT EXISTS storage_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    world_id TEXT NOT NULL,
                    dimension TEXT NOT NULL,
                    x INTEGER NOT NULL,
                    y INTEGER NOT NULL,
                    z INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    event_time REAL NOT NULL,
                    details_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS landmarks (
                    world_id TEXT NOT NULL,
                    dimension TEXT NOT NULL,
                    category TEXT NOT NULL,
                    x INTEGER NOT NULL,
                    y INTEGER NOT NULL,
                    z INTEGER NOT NULL,
                    block_id TEXT NOT NULL,
                    first_seen REAL NOT NULL,
                    last_seen REAL NOT NULL,
                    discovered_by TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    PRIMARY KEY (world_id, dimension, category, x, y, z)
                );
                CREATE INDEX IF NOT EXISTS idx_landmarks_lookup
                    ON landmarks(world_id, dimension, category, last_seen);
                CREATE TABLE IF NOT EXISTS resource_leases (
                    world_id TEXT NOT NULL,
                    lease_key TEXT NOT NULL,
                    owner TEXT NOT NULL,
                    acquired_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    PRIMARY KEY (world_id, lease_key)
                );
                CREATE INDEX IF NOT EXISTS idx_resource_leases_expiry
                    ON resource_leases(world_id, expires_at);
                """
            )
            migrate_warehouse_schema(db)
            db.executescript(WAREHOUSE_SCHEMA)
            db.execute(
                "INSERT OR REPLACE INTO catalog_meta(key, value) VALUES('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )

    def register_container(
        self,
        position: Tuple[int, int, int],
        *,
        dimension: str,
        container_type: str = "minecraft:chest",
        label: Optional[str] = None,
        purpose: Optional[str] = None,
        status: str = "verified",
        metadata: Optional[Mapping[str, Any]] = None,
        seen_at: Optional[float] = None,
    ) -> None:
        x, y, z = (int(value) for value in position)
        observed = float(seen_at or time.time())
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO containers(
                    world_id, dimension, x, y, z, container_type, label,
                    purpose, last_seen, status, metadata_json
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(world_id, dimension, x, y, z) DO UPDATE SET
                    container_type=excluded.container_type,
                    label=COALESCE(excluded.label, containers.label),
                    purpose=COALESCE(excluded.purpose, containers.purpose),
                    last_seen=excluded.last_seen,
                    status=CASE
                        WHEN containers.status='missing' AND excluded.status='known'
                        THEN containers.status
                        ELSE excluded.status
                    END,
                    metadata_json=excluded.metadata_json
                """,
                (
                    self.world_id,
                    dimension,
                    x,
                    y,
                    z,
                    container_type,
                    label,
                    purpose,
                    observed,
                    status,
                    json.dumps(dict(metadata or {}), sort_keys=True),
                ),
            )

    def observe_inventory(
        self,
        position: Tuple[int, int, int],
        slots: Sequence[Mapping[str, Any]],
        *,
        dimension: str,
        container_type: str = "minecraft:chest",
        capacity_slots: Optional[int] = None,
        label: Optional[str] = None,
        purpose: Optional[str] = None,
        observed_at: Optional[float] = None,
    ) -> Dict[str, int]:
        """Atomically replace one container's last-known slot snapshot."""
        x, y, z = (int(value) for value in position)
        observed = float(observed_at or time.time())
        normalized = []
        item_totals: Dict[str, int] = {}
        for raw in slots:
            slot = int(raw.get("slot", -1))
            item_id = str(raw.get("id", "minecraft:air"))
            count = max(0, int(raw.get("count", 0) or 0))
            if slot < 0 or count <= 0 or item_id in ("", "minecraft:air"):
                continue
            normalized.append(
                (
                    slot,
                    item_id,
                    count,
                    int(raw.get("max_count", 0) or 0) or None,
                    int(raw.get("damage", 0) or 0),
                )
            )
            item_totals[item_id] = item_totals.get(item_id, 0) + count

        capacity = int(capacity_slots or 0) or None
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO containers(
                    world_id, dimension, x, y, z, container_type, label,
                    purpose, capacity_slots, occupied_slots, total_items,
                    last_seen, last_inventory_scan, status
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'verified')
                ON CONFLICT(world_id, dimension, x, y, z) DO UPDATE SET
                    container_type=excluded.container_type,
                    label=COALESCE(excluded.label, containers.label),
                    purpose=COALESCE(excluded.purpose, containers.purpose),
                    capacity_slots=excluded.capacity_slots,
                    occupied_slots=excluded.occupied_slots,
                    total_items=excluded.total_items,
                    last_seen=excluded.last_seen,
                    last_inventory_scan=excluded.last_inventory_scan,
                    status='verified'
                """,
                (
                    self.world_id,
                    dimension,
                    x,
                    y,
                    z,
                    container_type,
                    label,
                    purpose,
                    capacity,
                    len(normalized),
                    sum(item_totals.values()),
                    observed,
                    observed,
                ),
            )
            db.execute(
                """DELETE FROM container_items
                   WHERE world_id=? AND dimension=? AND x=? AND y=? AND z=?""",
                (self.world_id, dimension, x, y, z),
            )
            db.executemany(
                """
                INSERT INTO container_items(
                    world_id, dimension, x, y, z, slot, item_id, count,
                    max_count, damage, observed_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        self.world_id,
                        dimension,
                        x,
                        y,
                        z,
                        slot,
                        item_id,
                        count,
                        max_count,
                        damage,
                        observed,
                    )
                    for slot, item_id, count, max_count, damage in normalized
                ],
            )
        return item_totals

    def record_event(
        self,
        position: Tuple[int, int, int],
        event_type: str,
        *,
        dimension: str,
        details: Optional[Mapping[str, Any]] = None,
    ) -> None:
        x, y, z = (int(value) for value in position)
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO storage_events(
                    world_id, dimension, x, y, z, event_type, event_time,
                    details_json
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self.world_id,
                    dimension,
                    x,
                    y,
                    z,
                    event_type,
                    time.time(),
                    json.dumps(dict(details or {}), sort_keys=True),
                ),
            )

    def mark_missing(
        self,
        position: Tuple[int, int, int],
        *,
        dimension: str,
    ) -> None:
        """Record that a loaded, readable coordinate no longer has a container."""
        self.register_container(
            position,
            dimension=dimension,
            status="missing",
            metadata={"source": "live_block_verification"},
        )

    def list_containers(self) -> list[Dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                """SELECT * FROM containers WHERE world_id=? AND status!='missing'
                   ORDER BY COALESCE(last_inventory_scan, 0) DESC, last_seen DESC""",
                (self.world_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def find_item(self, item_id: str) -> list[Dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                """
                SELECT c.dimension, c.x, c.y, c.z, c.label, c.purpose,
                       c.last_inventory_scan, SUM(i.count) AS count
                FROM container_items i
                JOIN containers c USING(world_id, dimension, x, y, z)
                WHERE i.world_id=? AND i.item_id=? AND c.status!='missing'
                GROUP BY c.dimension, c.x, c.y, c.z, c.label, c.purpose,
                         c.last_inventory_scan
                ORDER BY count DESC
                """,
                (self.world_id, item_id),
            ).fetchall()
        return [dict(row) for row in rows]

    def item_count(self, item_id: str) -> int:
        """Return the current-world total from verified catalog snapshots."""
        with self._connect() as db:
            row = db.execute(
                """SELECT COALESCE(SUM(i.count), 0) AS count
                   FROM container_items i
                   JOIN containers c USING(world_id, dimension, x, y, z)
                   WHERE i.world_id=? AND i.item_id=? AND c.status!='missing'""",
                (self.world_id, item_id),
            ).fetchone()
        return int(row["count"] if row else 0)

    def inventory_totals(self) -> Dict[str, int]:
        """Return aggregate item counts from all non-missing containers."""
        with self._connect() as db:
            rows = db.execute(
                """SELECT i.item_id, COALESCE(SUM(i.count), 0) AS count
                   FROM container_items i
                   JOIN containers c USING(world_id, dimension, x, y, z)
                   WHERE i.world_id=? AND c.status!='missing'
                   GROUP BY i.item_id""",
                (self.world_id,),
            ).fetchall()
        return {str(row["item_id"]): int(row["count"]) for row in rows}

    def container_inventory(
        self,
        position: Tuple[int, int, int],
        *,
        dimension: str,
    ) -> Dict[str, int]:
        """Return last-known item totals for one physical container."""
        x, y, z = (int(value) for value in position)
        with self._connect() as db:
            rows = db.execute(
                """SELECT item_id, COALESCE(SUM(count), 0) AS count
                   FROM container_items
                   WHERE world_id=? AND dimension=? AND x=? AND y=? AND z=?
                   GROUP BY item_id""",
                (self.world_id, str(dimension), x, y, z),
            ).fetchall()
        return {str(row["item_id"]): int(row["count"]) for row in rows}

    def acquire_lease(
        self,
        lease_key: str,
        owner: str,
        *,
        ttl_seconds: float = 120.0,
        now: Optional[float] = None,
    ) -> bool:
        """Atomically claim or renew a world-scoped bounded-work lease."""
        observed = time.time() if now is None else float(now)
        expires = observed + max(1.0, float(ttl_seconds))
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                """INSERT INTO resource_leases(
                       world_id, lease_key, owner, acquired_at, expires_at
                   ) VALUES(?, ?, ?, ?, ?)
                   ON CONFLICT(world_id, lease_key) DO UPDATE SET
                       owner=excluded.owner,
                       acquired_at=excluded.acquired_at,
                       expires_at=excluded.expires_at
                   WHERE resource_leases.expires_at <= ?
                      OR resource_leases.owner = excluded.owner""",
                (
                    self.world_id,
                    str(lease_key),
                    str(owner),
                    observed,
                    expires,
                    observed,
                ),
            )
            row = db.execute(
                """SELECT owner, expires_at FROM resource_leases
                   WHERE world_id=? AND lease_key=?""",
                (self.world_id, str(lease_key)),
            ).fetchone()
        return bool(
            row
            and str(row["owner"]) == str(owner)
            and float(row["expires_at"]) >= expires
        )

    def release_lease(self, lease_key: str, owner: str) -> bool:
        """Release a lease only when it is still owned by this controller."""
        with self._connect() as db:
            cursor = db.execute(
                """DELETE FROM resource_leases
                   WHERE world_id=? AND lease_key=? AND owner=?""",
                (self.world_id, str(lease_key), str(owner)),
            )
        return bool(cursor.rowcount)

    def register_landmark(
        self,
        category: str,
        position: Tuple[int, int, int],
        *,
        dimension: str,
        block_id: str,
        discovered_by: Optional[str] = None,
        metadata: Optional[Mapping[str, Any]] = None,
        seen_at: Optional[float] = None,
    ) -> None:
        """Upsert one observed landmark for all bots in this world."""
        x, y, z = (int(value) for value in position)
        observed = float(seen_at or time.time())
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO landmarks(
                    world_id, dimension, category, x, y, z, block_id,
                    first_seen, last_seen, discovered_by, metadata_json
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(world_id, dimension, category, x, y, z) DO UPDATE SET
                    block_id=excluded.block_id,
                    last_seen=excluded.last_seen,
                    discovered_by=COALESCE(excluded.discovered_by, landmarks.discovered_by),
                    metadata_json=excluded.metadata_json
                """,
                (
                    self.world_id,
                    str(dimension),
                    str(category),
                    x,
                    y,
                    z,
                    str(block_id),
                    observed,
                    observed,
                    discovered_by,
                    json.dumps(dict(metadata or {}), sort_keys=True),
                ),
            )

    def list_landmarks(
        self,
        category: Optional[str] = None,
        *,
        dimension: Optional[str] = None,
    ) -> list[Dict[str, Any]]:
        """Return shared landmarks for this world, newest observations first."""
        clauses = ["world_id=?"]
        params: list[Any] = [self.world_id]
        if category is not None:
            clauses.append("category=?")
            params.append(str(category))
        if dimension is not None:
            clauses.append("dimension=?")
            params.append(str(dimension))
        query = "SELECT * FROM landmarks WHERE " + " AND ".join(clauses)
        query += " ORDER BY last_seen DESC"
        with self._connect() as db:
            rows = db.execute(query, params).fetchall()
        return [dict(row) for row in rows]


def catalog_for(client, state=None) -> StorageCatalog:
    # Unit-test doubles and ad-hoc clients without an isolated run directory or
    # real transport identity must not silently create a catalog in the repo.
    # Real TCP transports expose ``host``; production supervisors also set
    # MC_RUN_DIR or pass StateManager.
    if (
        state is None
        and not os.environ.get("MC_RUN_DIR")
        and not getattr(getattr(client, "transport", None), "host", None)
    ):
        raise RuntimeError("storage catalog has no persistent client context")
    run_dir = _run_directory(state)
    return StorageCatalog(_catalog_path(run_dir), _world_id(state, run_dir))


def catalog_from_run_dir(
    run_dir: Path, world_id: Optional[str] = None
) -> StorageCatalog:
    """Open a catalog for operator tooling without a live bridge client."""
    resolved = Path(run_dir).resolve()
    return StorageCatalog(
        _catalog_path(resolved),
        world_id or _checkpoint_world_id(resolved) or "unscoped-world",
    )


def seed_from_state(client, state) -> int:
    """Register checkpointed chest landmarks without claiming contents are fresh."""
    catalog = catalog_for(client, state)
    dimension = _dimension(client)
    positions: set[Tuple[int, int, int]] = set()
    try:
        locations = state.get_locations("chest").get("chest", [])
    except (AttributeError, TypeError):
        locations = (
            getattr(state, "custom_data", {}).get("locations", {}).get("chest", [])
        )
    for value in locations:
        try:
            positions.add((int(value["x"]), int(value["y"]), int(value["z"])))
        except (KeyError, TypeError, ValueError):
            continue

    # Import the legacy single-storage checkpoint as a landmark.  This keeps
    # existing long-running worlds useful on first catalog-enabled restart.
    legacy_path = Path(getattr(state, "checkpoint_dir", Path.cwd())) / "checkpoint_storage.json"
    try:
        legacy = json.loads(legacy_path.read_text(encoding="utf-8"))
        data = legacy.get("data", legacy)
        positions.add((int(data["x"]), int(data["y"]), int(data["z"])))
    except (OSError, ValueError, TypeError, KeyError):
        pass

    structures = getattr(state, "custom_data", {}).get("structures", {})
    for structure in structures.values():
        if not isinstance(structure, Mapping):
            continue
        for key, value in structure.items():
            if "chest" not in str(key) or not isinstance(value, (list, tuple)):
                continue
            if len(value) != 3:
                continue
            try:
                positions.add(tuple(int(part) for part in value))
            except (TypeError, ValueError):
                continue

    for position in positions:
        catalog.register_container(
            position,
            dimension=dimension,
            container_type="minecraft:chest",
            purpose="checkpointed_storage",
            status="known",
            metadata={"source": "checkpoint"},
        )
    return len(positions)


def observe_open_container(
    client,
    position: Tuple[int, int, int],
    screen: Mapping[str, Any],
    *,
    state=None,
    container_type: str = "minecraft:chest",
    label: Optional[str] = None,
    purpose: Optional[str] = None,
) -> Dict[str, int]:
    """Persist the container-owned portion of a bridge ``get_screen`` result."""
    data = screen.get("data", screen)
    slots = list(data.get("slots", []))
    total_slots = int(data.get("total_slots") or len(slots))
    container_slots = max(0, total_slots - 36)
    if container_slots not in (27, 54):
        raise ValueError(f"Unsupported container layout: {total_slots} total slots")
    owned_slots = [item for item in slots if int(item.get("slot", -1)) < container_slots]
    return catalog_for(client, state).observe_inventory(
        position,
        owned_slots,
        dimension=_dimension(client),
        container_type=container_type,
        capacity_slots=container_slots,
        label=label,
        purpose=purpose,
    )
