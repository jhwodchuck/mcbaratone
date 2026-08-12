"""Durable, lease-backed reconciliation for one storage migration batch.

The Minecraft GUI moves a storage batch in two physical legs: source to the
carrier, then carrier to the destination.  This module journals the boundary
counts for those legs so a restarted operator can determine where every item
is before resuming.  It deliberately does not guess through an inconsistent
snapshot; mismatches remain in ``RECOVERY_REQUIRED`` for manual inspection.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence


Position = tuple[int, int, int]


class MigrationPhase(str, Enum):
    """Durable phases for one bounded category migration."""

    SOURCE_SNAPSHOTTED = "SOURCE_SNAPSHOTTED"
    WITHDRAWING = "WITHDRAWING"
    WITHDRAWAL_VERIFIED = "WITHDRAWAL_VERIFIED"
    DEPOSITING = "DEPOSITING"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"
    COMMITTED = "COMMITTED"


ACTIVE_PHASES = {
    MigrationPhase.SOURCE_SNAPSHOTTED,
    MigrationPhase.WITHDRAWING,
    MigrationPhase.WITHDRAWAL_VERIFIED,
    MigrationPhase.DEPOSITING,
    MigrationPhase.RECOVERY_REQUIRED,
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class MigrationCoordinates:
    """Immutable source members and destination for one transaction."""

    source: tuple[Position, ...]
    destination: Position


@dataclass
class MigrationTransaction:
    """Persisted batch identity and conservation counts."""

    transaction_id: str
    phase: MigrationPhase
    item_id: str
    expected_count: int
    coordinates: MigrationCoordinates
    counts: dict[str, int] = field(default_factory=dict)
    reason: str = ""
    updated_at: str = field(default_factory=_now)
    schema: str = "mcbaratone-storage-migration/v1"

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["phase"] = self.phase.value
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "MigrationTransaction":
        coordinates = value["coordinates"]
        return cls(
            transaction_id=str(value["transaction_id"]),
            phase=MigrationPhase(str(value["phase"])),
            item_id=str(value["item_id"]),
            expected_count=int(value["expected_count"]),
            coordinates=MigrationCoordinates(
                source=tuple(
                    tuple(int(axis) for axis in position)
                    for position in coordinates["source"]
                ),
                destination=tuple(
                    int(axis) for axis in coordinates["destination"]
                ),
            ),
            counts={
                str(key): int(count)
                for key, count in value.get("counts", {}).items()
            },
            reason=str(value.get("reason", "")),
            updated_at=str(value.get("updated_at", _now())),
            schema=str(value.get("schema", "mcbaratone-storage-migration/v1")),
        )


class MigrationJournal:
    """Atomically persist one migration transaction."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> MigrationTransaction | None:
        if not self.path.exists():
            return None
        return MigrationTransaction.from_dict(
            json.loads(self.path.read_text(encoding="utf-8"))
        )

    def save(self, transaction: MigrationTransaction) -> None:
        transaction.updated_at = _now()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(transaction.to_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, self.path)


class MigrationLease(Protocol):
    """Exclusive lease covering every source and destination container."""

    def acquire(self, owner: str) -> bool:
        """Claim or renew all members for ``owner``."""

    def release(self, owner: str) -> bool:
        """Release all members still owned by ``owner``."""


class CatalogMigrationLease:
    """Adapt StorageCatalog leases into one all-or-nothing lease set."""

    def __init__(
        self,
        catalog: Any,
        lease_keys: Sequence[str],
        *,
        ttl_seconds: float = 600.0,
    ) -> None:
        self.catalog = catalog
        self.lease_keys = tuple(sorted(dict.fromkeys(str(key) for key in lease_keys)))
        if not self.lease_keys:
            raise ValueError("at least one migration lease key is required")
        self.ttl_seconds = max(30.0, float(ttl_seconds))

    def acquire(self, owner: str) -> bool:
        acquired: list[str] = []
        for key in self.lease_keys:
            if not self.catalog.acquire_lease(
                key, str(owner), ttl_seconds=self.ttl_seconds
            ):
                for acquired_key in reversed(acquired):
                    self.catalog.release_lease(acquired_key, str(owner))
                return False
            acquired.append(key)
        return True

    def release(self, owner: str) -> bool:
        results = [
            self.catalog.release_lease(key, str(owner))
            for key in reversed(self.lease_keys)
        ]
        return all(results)


class TransactionalMigrationController:
    """Reconcile a known item across source, carrier, and destination."""

    def __init__(self, journal: MigrationJournal, lease: MigrationLease) -> None:
        self.journal = journal
        self.lease = lease

    def startup_recover(self) -> MigrationTransaction | None:
        """Reacquire an unfinished transaction and require reconciliation."""
        transaction = self.journal.load()
        if transaction is not None and transaction.phase in ACTIVE_PHASES:
            self._acquire(transaction)
            transaction.phase = MigrationPhase.RECOVERY_REQUIRED
            transaction.reason = "controller restarted before migration commit"
            self.journal.save(transaction)
        return transaction

    def begin(
        self,
        transaction_id: str,
        item_id: str,
        expected_count: int,
        coordinates: MigrationCoordinates,
        *,
        source_before: int,
        carried_before: int,
        destination_before: int,
    ) -> MigrationTransaction:
        """Lease and snapshot one exact, positive migration batch."""
        current = self.journal.load()
        if current is not None and current.phase in ACTIVE_PHASES:
            raise RuntimeError(
                f"unfinished migration {current.transaction_id} requires recovery"
            )
        if expected_count <= 0:
            raise ValueError("expected_count must be positive")
        if source_before < expected_count:
            raise ValueError("source contains less than the expected batch")
        owner = str(transaction_id)
        if not self.lease.acquire(owner):
            raise RuntimeError("migration containers are leased by another owner")
        transaction = MigrationTransaction(
            transaction_id=owner,
            phase=MigrationPhase.SOURCE_SNAPSHOTTED,
            item_id=str(item_id),
            expected_count=int(expected_count),
            coordinates=coordinates,
            counts={
                "source_before": int(source_before),
                "carried_before": int(carried_before),
                "destination_before": int(destination_before),
            },
        )
        try:
            self.journal.save(transaction)
        except Exception:
            self.lease.release(owner)
            raise
        return transaction

    def mark_withdrawing(self) -> MigrationTransaction:
        transaction = self._require(MigrationPhase.SOURCE_SNAPSHOTTED)
        transaction.phase = MigrationPhase.WITHDRAWING
        self.journal.save(transaction)
        return transaction

    def verify_withdrawal(
        self,
        *,
        source_after: int,
        carried_after: int,
        destination_current: int,
    ) -> MigrationTransaction:
        transaction = self._require(MigrationPhase.WITHDRAWING)
        source_loss, carried_gain, destination_gain = self._deltas(
            transaction, source_after, carried_after, destination_current
        )
        expected = transaction.expected_count
        if (source_loss, carried_gain, destination_gain) != (expected, expected, 0):
            self._recovery(
                transaction,
                "withdrawal mismatch: "
                f"source={source_loss}, carried={carried_gain}, "
                f"destination={destination_gain}, expected={expected}",
            )
            raise RuntimeError(transaction.reason)
        transaction.counts.update(
            source_after=int(source_after),
            carried_after_withdrawal=int(carried_after),
            destination_after_withdrawal=int(destination_current),
        )
        transaction.phase = MigrationPhase.WITHDRAWAL_VERIFIED
        transaction.reason = ""
        self.journal.save(transaction)
        return transaction

    def mark_depositing(self) -> MigrationTransaction:
        transaction = self._require(MigrationPhase.WITHDRAWAL_VERIFIED)
        transaction.phase = MigrationPhase.DEPOSITING
        self.journal.save(transaction)
        return transaction

    def reconcile_recovery(
        self,
        *,
        source_current: int,
        carried_current: int,
        destination_current: int,
    ) -> MigrationTransaction:
        """Classify a restart only when all three physical counts conserve."""
        transaction = self._require(MigrationPhase.RECOVERY_REQUIRED)
        source_loss, carried_gain, destination_gain = self._deltas(
            transaction, source_current, carried_current, destination_current
        )
        expected = transaction.expected_count
        if source_loss == carried_gain == destination_gain == 0:
            transaction.phase = MigrationPhase.SOURCE_SNAPSHOTTED
        elif (
            source_loss == expected
            and carried_gain >= 0
            and destination_gain >= 0
            and carried_gain + destination_gain == expected
        ):
            transaction.phase = (
                MigrationPhase.WITHDRAWAL_VERIFIED
                if carried_gain > 0
                else MigrationPhase.DEPOSITING
            )
        else:
            self._recovery(
                transaction,
                "recovery mismatch: "
                f"source={source_loss}, carried={carried_gain}, "
                f"destination={destination_gain}, expected={expected}",
            )
            raise RuntimeError(transaction.reason)
        transaction.counts.update(
            recovery_source=int(source_current),
            recovery_carried=int(carried_current),
            recovery_destination=int(destination_current),
        )
        transaction.reason = ""
        self.journal.save(transaction)
        return transaction

    def reconcile_and_commit(
        self,
        *,
        source_final: int,
        carried_final: int,
        destination_after: int,
    ) -> MigrationTransaction:
        """Commit only a fully deposited, exactly conserved batch."""
        transaction = self.journal.load()
        if transaction is None or transaction.phase not in {
            MigrationPhase.DEPOSITING,
            MigrationPhase.RECOVERY_REQUIRED,
        }:
            actual = None if transaction is None else transaction.phase.value
            raise RuntimeError(f"cannot reconcile migration from {actual}")
        self._acquire(transaction)
        source_loss, carried_gain, destination_gain = self._deltas(
            transaction, source_final, carried_final, destination_after
        )
        expected = transaction.expected_count
        if (source_loss, carried_gain, destination_gain) != (expected, 0, expected):
            self._recovery(
                transaction,
                "commit mismatch: "
                f"source={source_loss}, carried={carried_gain}, "
                f"destination={destination_gain}, expected={expected}",
            )
            raise RuntimeError(transaction.reason)
        transaction.counts.update(
            source_final=int(source_final),
            carried_final=int(carried_final),
            destination_after=int(destination_after),
        )
        transaction.phase = MigrationPhase.COMMITTED
        transaction.reason = ""
        self.journal.save(transaction)
        if not self.lease.release(transaction.transaction_id):
            raise RuntimeError("committed migration did not release every lease")
        return transaction

    @staticmethod
    def _deltas(
        transaction: MigrationTransaction,
        source: int,
        carried: int,
        destination: int,
    ) -> tuple[int, int, int]:
        return (
            transaction.counts["source_before"] - int(source),
            int(carried) - transaction.counts["carried_before"],
            int(destination) - transaction.counts["destination_before"],
        )

    def _require(self, phase: MigrationPhase) -> MigrationTransaction:
        transaction = self.journal.load()
        if transaction is None or transaction.phase is not phase:
            actual = None if transaction is None else transaction.phase.value
            raise RuntimeError(f"expected migration phase {phase.value}, found {actual}")
        self._acquire(transaction)
        return transaction

    def _acquire(self, transaction: MigrationTransaction) -> None:
        if not self.lease.acquire(transaction.transaction_id):
            raise RuntimeError("migration containers are leased by another owner")

    def _recovery(self, transaction: MigrationTransaction, reason: str) -> None:
        transaction.phase = MigrationPhase.RECOVERY_REQUIRED
        transaction.reason = str(reason)
        self.journal.save(transaction)


__all__ = [
    "CatalogMigrationLease",
    "MigrationCoordinates",
    "MigrationJournal",
    "MigrationLease",
    "MigrationPhase",
    "MigrationTransaction",
    "TransactionalMigrationController",
]
