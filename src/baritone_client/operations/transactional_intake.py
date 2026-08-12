"""Fail-closed journal and physical drain control for one intake bay.

The controller deliberately separates durable transaction evidence from the
Minecraft mechanism.  On every startup it places and verifies the physical
lock *before* reading the journal.  An interrupted transaction can therefore
never resume a hopper merely because its last persisted state said RUNNING.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Protocol


Position = tuple[int, int, int]


class IntakePhase(str, Enum):
    """Durable phases for one bounded intake transaction."""

    LEASED = "LEASED"
    SOURCE_SNAPSHOTTED = "SOURCE_SNAPSHOTTED"
    DEPOSITING = "DEPOSITING"
    DEPOSIT_VERIFIED = "DEPOSIT_VERIFIED"
    DRAIN_ENABLED = "DRAIN_ENABLED"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"
    COMMITTED = "COMMITTED"


ACTIVE_PHASES = {
    IntakePhase.LEASED,
    IntakePhase.SOURCE_SNAPSHOTTED,
    IntakePhase.DEPOSITING,
    IntakePhase.DEPOSIT_VERIFIED,
    IntakePhase.DRAIN_ENABLED,
    IntakePhase.RECOVERY_REQUIRED,
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class IntakeCoordinates:
    """Immutable physical coordinates for one transactional intake."""

    intake: Position
    hopper: Position
    destination: Position
    control: Position


@dataclass
class IntakeTransaction:
    """One persisted batch and the counts needed for reconciliation."""

    transaction_id: str
    phase: IntakePhase
    item_id: str
    expected_count: int
    coordinates: IntakeCoordinates
    counts: dict[str, int] = field(default_factory=dict)
    reason: str = ""
    updated_at: str = field(default_factory=_now)
    schema: str = "mcbaratone-intake-transaction/v1"

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["phase"] = self.phase.value
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "IntakeTransaction":
        coordinates = value["coordinates"]
        return cls(
            transaction_id=str(value["transaction_id"]),
            phase=IntakePhase(str(value["phase"])),
            item_id=str(value["item_id"]),
            expected_count=int(value["expected_count"]),
            coordinates=IntakeCoordinates(
                intake=tuple(int(part) for part in coordinates["intake"]),
                hopper=tuple(int(part) for part in coordinates["hopper"]),
                destination=tuple(
                    int(part) for part in coordinates["destination"]
                ),
                control=tuple(int(part) for part in coordinates["control"]),
            ),
            counts={str(key): int(count) for key, count in value.get("counts", {}).items()},
            reason=str(value.get("reason", "")),
            updated_at=str(value.get("updated_at", _now())),
            schema=str(value.get("schema", "mcbaratone-intake-transaction/v1")),
        )


class TransactionJournal:
    """Atomically persist one intake transaction."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> IntakeTransaction | None:
        if not self.path.exists():
            return None
        value = json.loads(self.path.read_text(encoding="utf-8"))
        return IntakeTransaction.from_dict(value)

    def save(self, transaction: IntakeTransaction) -> None:
        transaction.updated_at = _now()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(transaction.to_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, self.path)


class IntakeLease(Protocol):
    """Exclusive world-scoped lease used by one intake transaction."""

    def acquire(self, owner: str) -> bool:
        """Claim or renew the lease for ``owner``."""

    def release(self, owner: str) -> bool:
        """Release the lease only when ``owner`` still owns it."""


class DrainControl(Protocol):
    """Bounded physical drain control supplied by an authorized runtime.

    The durable transaction layer deliberately does not embed RCON or block
    replacement commands.  The injected edge must enforce identity, bounds,
    authorization, and physical postconditions.
    """

    def lock(self) -> None:
        """Put the intake in its verified fail-closed state."""

    def unlock(self) -> None:
        """Enable only the already-verified bounded drain."""


class CatalogIntakeLease:
    """Adapt a :class:`StorageCatalog` resource lease to the controller."""

    def __init__(
        self,
        catalog: Any,
        lease_key: str,
        *,
        ttl_seconds: float = 600.0,
    ) -> None:
        self.catalog = catalog
        self.lease_key = str(lease_key)
        self.ttl_seconds = max(30.0, float(ttl_seconds))

    def acquire(self, owner: str) -> bool:
        return bool(
            self.catalog.acquire_lease(
                self.lease_key,
                str(owner),
                ttl_seconds=self.ttl_seconds,
            )
        )

    def release(self, owner: str) -> bool:
        return bool(self.catalog.release_lease(self.lease_key, str(owner)))


class TransactionalIntakeController:
    """Advance a known batch only when its physical counts reconcile."""

    def __init__(
        self,
        journal: TransactionJournal,
        drain: DrainControl,
        lease: IntakeLease,
    ) -> None:
        self.journal = journal
        self.drain = drain
        self.lease = lease

    def startup_recover(self) -> IntakeTransaction | None:
        """Lock first; then convert any unfinished journal to recovery state."""
        self.drain.lock()
        transaction = self.journal.load()
        if transaction is not None and transaction.phase in ACTIVE_PHASES:
            self._acquire(transaction)
            transaction.phase = IntakePhase.RECOVERY_REQUIRED
            transaction.reason = "controller restarted before commit; drain locked"
            self.journal.save(transaction)
        return transaction

    def begin(
        self,
        transaction_id: str,
        item_id: str,
        expected_count: int,
        coordinates: IntakeCoordinates,
        *,
        carried_before: int,
        intake_before: int,
        destination_before: int,
    ) -> IntakeTransaction:
        """Create a locked journal for a single known batch."""
        self.drain.lock()
        current = self.journal.load()
        if current is not None and current.phase in ACTIVE_PHASES:
            raise RuntimeError(
                f"unfinished intake transaction {current.transaction_id} requires recovery"
            )
        if expected_count <= 0:
            raise ValueError("expected_count must be positive")
        owner = str(transaction_id)
        if not self.lease.acquire(owner):
            raise RuntimeError("transactional intake bay is leased by another owner")
        transaction = IntakeTransaction(
            transaction_id=owner,
            phase=IntakePhase.SOURCE_SNAPSHOTTED,
            item_id=str(item_id),
            expected_count=int(expected_count),
            coordinates=coordinates,
            counts={
                "carried_before": int(carried_before),
                "intake_before": int(intake_before),
                "destination_before": int(destination_before),
            },
        )
        try:
            self.journal.save(transaction)
        except Exception:
            self.lease.release(owner)
            raise
        return transaction

    def mark_depositing(self) -> IntakeTransaction:
        transaction = self._require(IntakePhase.SOURCE_SNAPSHOTTED)
        transaction.phase = IntakePhase.DEPOSITING
        self.journal.save(transaction)
        return transaction

    def verify_deposit(
        self,
        *,
        carried_after: int,
        intake_after: int,
        pipeline_after: int,
    ) -> IntakeTransaction:
        transaction = self._require(IntakePhase.DEPOSITING)
        carried_delta = transaction.counts["carried_before"] - int(carried_after)
        intake_delta = int(intake_after) - transaction.counts["intake_before"]
        expected = transaction.expected_count
        if carried_delta != expected or intake_delta != expected or pipeline_after != 0:
            self._recovery(
                transaction,
                "deposit mismatch: "
                f"carried={carried_delta}, intake={intake_delta}, "
                f"pipeline={pipeline_after}, expected={expected}",
            )
            raise RuntimeError(transaction.reason)
        transaction.counts.update(
            carried_after=int(carried_after),
            intake_after=int(intake_after),
            pipeline_after_deposit=int(pipeline_after),
        )
        transaction.phase = IntakePhase.DEPOSIT_VERIFIED
        transaction.reason = ""
        self.journal.save(transaction)
        return transaction

    def recover_deposit(
        self,
        *,
        carried_after: int,
        intake_after: int,
        pipeline_after: int,
    ) -> IntakeTransaction:
        """Accept a completed deposit observed after a controller interruption."""
        self.drain.lock()
        transaction = self._require(IntakePhase.RECOVERY_REQUIRED)
        carried_delta = transaction.counts["carried_before"] - int(carried_after)
        intake_delta = int(intake_after) - transaction.counts["intake_before"]
        expected = transaction.expected_count
        if carried_delta != expected or intake_delta != expected or pipeline_after != 0:
            self._recovery(
                transaction,
                "recovered deposit mismatch: "
                f"carried={carried_delta}, intake={intake_delta}, "
                f"pipeline={pipeline_after}, expected={expected}",
            )
            raise RuntimeError(transaction.reason)
        transaction.counts.update(
            carried_after=int(carried_after),
            intake_after=int(intake_after),
            pipeline_after_deposit=int(pipeline_after),
        )
        transaction.phase = IntakePhase.DEPOSIT_VERIFIED
        transaction.reason = ""
        self.journal.save(transaction)
        return transaction

    def enable_drain(self) -> IntakeTransaction:
        """Persist intent before unlocking so restart recovery fails closed."""
        transaction = self._require(IntakePhase.DEPOSIT_VERIFIED)
        transaction.phase = IntakePhase.DRAIN_ENABLED
        self.journal.save(transaction)
        try:
            self.drain.unlock()
        except Exception as exc:
            self._recovery(transaction, f"drain enable failed: {exc}")
            raise
        return transaction

    def resume_recovery_drain(
        self,
        *,
        intake_current: int,
        pipeline_current: int,
        destination_current: int,
    ) -> IntakeTransaction:
        """Resume only after observed in-flight and destination counts conserve."""
        transaction = self._require(IntakePhase.RECOVERY_REQUIRED)
        intake_delta = int(intake_current) - transaction.counts["intake_before"]
        destination_delta = (
            int(destination_current) - transaction.counts["destination_before"]
        )
        remaining = intake_delta + int(pipeline_current)
        if remaining <= 0:
            raise RuntimeError(
                "no in-flight items remain; reconcile the locked transaction"
            )
        if remaining + destination_delta != transaction.expected_count:
            self._recovery(
                transaction,
                "recovery mismatch: "
                f"intake={intake_delta}, destination={destination_delta}, "
                f"pipeline={pipeline_current}, expected={transaction.expected_count}",
            )
            raise RuntimeError(transaction.reason)
        transaction.counts.update(
            recovery_intake=int(intake_current),
            recovery_pipeline=int(pipeline_current),
            recovery_destination=int(destination_current),
        )
        transaction.phase = IntakePhase.DRAIN_ENABLED
        transaction.reason = ""
        self.journal.save(transaction)
        try:
            self.drain.unlock()
        except Exception as exc:
            self._recovery(transaction, f"recovery drain enable failed: {exc}")
            raise
        return transaction

    def reconcile_and_commit(
        self,
        *,
        intake_final: int,
        pipeline_final: int,
        destination_after: int,
    ) -> IntakeTransaction:
        """Lock, reconcile the whole batch, and commit only an empty pipeline."""
        self.drain.lock()
        transaction = self.journal.load()
        if transaction is None or transaction.phase not in {
            IntakePhase.DRAIN_ENABLED,
            IntakePhase.RECOVERY_REQUIRED,
        }:
            actual = None if transaction is None else transaction.phase.value
            raise RuntimeError(f"cannot reconcile intake transaction from {actual}")
        self._acquire(transaction)
        destination_delta = (
            int(destination_after) - transaction.counts["destination_before"]
        )
        intake_delta = int(intake_final) - transaction.counts["intake_before"]
        conserved = destination_delta + int(pipeline_final)
        expected = transaction.expected_count
        if intake_delta != 0 or pipeline_final != 0 or conserved != expected:
            self._recovery(
                transaction,
                "reconciliation mismatch: "
                f"intake={intake_delta}, destination={destination_delta}, "
                f"pipeline={pipeline_final}, expected={expected}",
            )
            raise RuntimeError(transaction.reason)
        transaction.counts.update(
            intake_final=int(intake_final),
            pipeline_final=int(pipeline_final),
            destination_after=int(destination_after),
        )
        transaction.phase = IntakePhase.COMMITTED
        transaction.reason = ""
        self.journal.save(transaction)
        if not self.lease.release(transaction.transaction_id):
            raise RuntimeError("committed intake transaction did not release its lease")
        return transaction

    def _require(self, expected: IntakePhase) -> IntakeTransaction:
        transaction = self.journal.load()
        if transaction is None or transaction.phase is not expected:
            actual = None if transaction is None else transaction.phase.value
            raise RuntimeError(
                f"expected intake phase {expected.value}, found {actual}"
            )
        self._acquire(transaction)
        return transaction

    def _acquire(self, transaction: IntakeTransaction) -> None:
        if not self.lease.acquire(transaction.transaction_id):
            raise RuntimeError(
                "transactional intake bay is leased by another owner"
            )

    def _recovery(self, transaction: IntakeTransaction, reason: str) -> None:
        self.drain.lock()
        transaction.phase = IntakePhase.RECOVERY_REQUIRED
        transaction.reason = str(reason)
        self.journal.save(transaction)


__all__ = [
    "CatalogIntakeLease",
    "DrainControl",
    "IntakeCoordinates",
    "IntakeLease",
    "IntakePhase",
    "IntakeTransaction",
    "TransactionJournal",
    "TransactionalIntakeController",
]
