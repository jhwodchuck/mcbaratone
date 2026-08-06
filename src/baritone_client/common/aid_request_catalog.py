"""Aid-request persistence mixed into the fleet-shared storage catalog."""

from __future__ import annotations

import json
import time
from typing import Any, Dict, Optional, Tuple


class AidRequestCatalogMixin:
    """World-scoped M1 need facts with storage-enforced anti-spam."""

    world_id: str

    def upsert_aid_request(
        self,
        request_id: str,
        requester: str,
        kind: str,
        detail: str,
        urgency: int,
        ttl_seconds: float,
        *,
        dimension: str,
        position: Tuple[int, int, int],
        now: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Create or refresh the one open request for ``(requester, kind)``.

        The partial unique index is the anti-spam guarantee.  ``BEGIN
        IMMEDIATE`` makes the lookup and update one writer-serialized SQLite
        operation, so separate controller processes cannot turn a persistent
        problem into an unbounded queue.
        """
        observed = time.time() if now is None else float(now)
        expires = observed + max(0.001, float(ttl_seconds))
        x, y, z = (int(value) for value in position)
        values = (
            str(detail), str(dimension), x, y, z,
            max(0, min(100, int(urgency))), expires, self.world_id,
            str(requester), str(kind),
        )
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                """SELECT request_id FROM aid_requests
                   WHERE world_id=? AND requester=? AND kind=?
                     AND resolution IS NULL""",
                (self.world_id, str(requester), str(kind)),
            ).fetchone()
            if existing is None:
                actual_request_id = str(request_id)
                db.execute(
                    """INSERT INTO aid_requests(
                           world_id, request_id, requester, kind, detail,
                           dimension, x, y, z, urgency, created_at, expires_at
                       ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        self.world_id, actual_request_id, str(requester),
                        str(kind), str(detail), str(dimension), x, y, z,
                        max(0, min(100, int(urgency))), observed, expires,
                    ),
                )
                event_type = "aid_request_raised"
            else:
                actual_request_id = str(existing["request_id"])
                db.execute(
                    """UPDATE aid_requests
                       SET detail=?, dimension=?, x=?, y=?, z=?, urgency=?, expires_at=?
                       WHERE world_id=? AND requester=? AND kind=?
                         AND resolution IS NULL""",
                    values,
                )
                event_type = "aid_request_updated"
            db.execute(
                """INSERT INTO storage_events(
                       world_id, dimension, x, y, z, event_type, event_time,
                       details_json
                   ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    self.world_id, str(dimension), x, y, z, event_type, observed,
                    json.dumps(
                        {
                            "detail": str(detail), "kind": str(kind),
                            "request_id": actual_request_id,
                            "requester": str(requester),
                            "urgency": max(0, min(100, int(urgency))),
                        },
                        sort_keys=True,
                    ),
                ),
            )
            row = db.execute(
                "SELECT * FROM aid_requests WHERE world_id=? AND request_id=?",
                (self.world_id, actual_request_id),
            ).fetchone()
        published = dict(row)
        # Callers need to distinguish a NEW need from a refreshed one. A bot
        # whose problem persists refreshes every tick; announcing that would
        # turn a standing difficulty into a stream of identical chatter.
        published["event"] = event_type
        published["is_new"] = event_type == "aid_request_raised"
        return published

    def expire_aid_requests(self, now: Optional[float] = None) -> int:
        """Mark every elapsed open aid request expired, exactly once."""
        observed = time.time() if now is None else float(now)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                """SELECT request_id, requester, kind, detail, dimension, x, y, z
                   FROM aid_requests
                   WHERE world_id=? AND resolution IS NULL AND expires_at <= ?""",
                (self.world_id, observed),
            ).fetchall()
            for row in rows:
                db.execute(
                    """UPDATE aid_requests
                       SET resolution='expired', resolved_at=?
                       WHERE world_id=? AND request_id=?
                         AND resolution IS NULL AND expires_at <= ?""",
                    (observed, self.world_id, row["request_id"], observed),
                )
                db.execute(
                    """INSERT INTO storage_events(
                           world_id, dimension, x, y, z, event_type, event_time,
                           details_json
                       ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        self.world_id, row["dimension"], row["x"], row["y"],
                        row["z"], "aid_request_expired", observed,
                        json.dumps(
                            {
                                "detail": row["detail"], "kind": row["kind"],
                                "request_id": row["request_id"],
                                "requester": row["requester"],
                            },
                            sort_keys=True,
                        ),
                    ),
                )
        return len(rows)

    def list_aid_requests(self, *, open_only: bool = True) -> list[Dict[str, Any]]:
        """Return aid facts for this world, never interpreting them as commands."""
        query = "SELECT * FROM aid_requests WHERE world_id=?"
        if open_only:
            query += " AND resolution IS NULL"
        query += " ORDER BY urgency DESC, created_at ASC, request_id ASC"
        with self._connect() as db:
            rows = db.execute(query, (self.world_id,)).fetchall()
        return [dict(row) for row in rows]
