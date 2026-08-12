"""Restart-safe reservation, pickup, and return reconciliation for one kit."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Protocol


Position = tuple[int, int, int]


class ProvisioningPhase(str, Enum):
    """Durable boundaries for one outbound and returns-locker cycle."""

    RESERVED = "RESERVED"
    OUTBOUND_STAGED = "OUTBOUND_STAGED"
    PICKUP_IN_PROGRESS = "PICKUP_IN_PROGRESS"
    PICKED_UP = "PICKED_UP"
    RETURN_IN_PROGRESS = "RETURN_IN_PROGRESS"
    RETURNED = "RETURNED"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"
    COMMITTED = "COMMITTED"


ACTIVE_PHASES = set(ProvisioningPhase) - {ProvisioningPhase.COMMITTED}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _counts(value: Mapping[str, Any]) -> dict[str, int]:
    return {str(key): int(count) for key, count in value.items()}


@dataclass(frozen=True)
class ProvisioningCoordinates:
    """Immutable source mapping and worker-facing locker endpoints."""

    sources: dict[str, Position]
    outbound: Position
    returns: Position


@dataclass
class ProvisioningTransaction:
    """Persisted physical boundaries for one complete kit allocation."""

    transaction_id: str
    phase: ProvisioningPhase
    worker: str
    profile: str
    kit: dict[str, int]
    coordinates: ProvisioningCoordinates
    source_before: dict[str, int]
    carried_before: dict[str, int]
    outbound_before: dict[str, int]
    return_before: dict[str, int]
    worker_inventory_hash_before: str
    observations: dict[str, dict[str, int]] = field(default_factory=dict)
    reason: str = ""
    updated_at: str = field(default_factory=_now)
    schema: str = "mcbaratone-provisioning-transaction/v1"

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["phase"] = self.phase.value
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ProvisioningTransaction":
        coordinates = value["coordinates"]
        return cls(
            transaction_id=str(value["transaction_id"]),
            phase=ProvisioningPhase(str(value["phase"])),
            worker=str(value["worker"]),
            profile=str(value["profile"]),
            kit=_counts(value["kit"]),
            coordinates=ProvisioningCoordinates(
                sources={
                    str(item): tuple(int(axis) for axis in position)
                    for item, position in coordinates["sources"].items()
                },
                outbound=tuple(int(axis) for axis in coordinates["outbound"]),
                returns=tuple(int(axis) for axis in coordinates["returns"]),
            ),
            source_before=_counts(value["source_before"]),
            carried_before=_counts(value["carried_before"]),
            outbound_before=_counts(value["outbound_before"]),
            return_before=_counts(value["return_before"]),
            worker_inventory_hash_before=str(value["worker_inventory_hash_before"]),
            observations={
                str(label): _counts(counts)
                for label, counts in value.get("observations", {}).items()
            },
            reason=str(value.get("reason", "")),
            updated_at=str(value.get("updated_at", _now())),
            schema=str(value.get("schema", "mcbaratone-provisioning-transaction/v1")),
        )


class ProvisioningJournal:
    """Atomically persist one provisioning transaction."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> ProvisioningTransaction | None:
        if not self.path.exists():
            return None
        return ProvisioningTransaction.from_dict(
            json.loads(self.path.read_text(encoding="utf-8"))
        )

    def save(self, transaction: ProvisioningTransaction) -> None:
        transaction.updated_at = _now()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(transaction.to_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, self.path)


class ProvisioningLease(Protocol):
    def acquire(self, owner: str) -> bool: ...

    def release(self, owner: str) -> bool: ...


class TransactionalProvisioningController:
    """Reconcile a reserved kit across sources, worker, and both lockers."""

    def __init__(self, journal: ProvisioningJournal, lease: ProvisioningLease) -> None:
        self.journal = journal
        self.lease = lease

    def begin(
        self,
        transaction_id: str,
        *,
        worker: str,
        profile: str,
        kit: Mapping[str, int],
        coordinates: ProvisioningCoordinates,
        source_before: Mapping[str, int],
        carried_before: Mapping[str, int],
        outbound_before: Mapping[str, int],
        return_before: Mapping[str, int],
        worker_inventory_hash_before: str,
    ) -> ProvisioningTransaction:
        current = self.journal.load()
        if current is not None and current.phase in ACTIVE_PHASES:
            raise RuntimeError(
                f"unfinished provisioning {current.transaction_id} requires recovery"
            )
        requested = _counts(kit)
        if not requested or any(count <= 0 for count in requested.values()):
            raise ValueError("provisioning kit counts must be positive")
        if set(requested) != set(coordinates.sources):
            raise ValueError("every kit item must have one registered source")
        sources = _counts(source_before)
        if any(sources.get(item, 0) < count for item, count in requested.items()):
            raise ValueError("registered sources cannot satisfy the kit")
        owner = str(transaction_id)
        if not self.lease.acquire(owner):
            raise RuntimeError("provisioning endpoints are leased by another owner")
        transaction = ProvisioningTransaction(
            transaction_id=owner,
            phase=ProvisioningPhase.RESERVED,
            worker=str(worker),
            profile=str(profile),
            kit=requested,
            coordinates=coordinates,
            source_before=sources,
            carried_before=_counts(carried_before),
            outbound_before=_counts(outbound_before),
            return_before=_counts(return_before),
            worker_inventory_hash_before=str(worker_inventory_hash_before),
        )
        try:
            self.journal.save(transaction)
        except Exception:
            self.lease.release(owner)
            raise
        return transaction

    def verify_staged(
        self,
        *,
        sources: Mapping[str, int],
        carried: Mapping[str, int],
        outbound: Mapping[str, int],
        returns: Mapping[str, int],
    ) -> ProvisioningTransaction:
        transaction = self._require(ProvisioningPhase.RESERVED)
        self._expect(transaction, sources, transaction.carried_before, transaction.kit, transaction.return_before, carried, outbound, returns, "staging")
        transaction.phase = ProvisioningPhase.OUTBOUND_STAGED
        transaction.reason = ""
        self.journal.save(transaction)
        return transaction

    def mark_pickup(self) -> ProvisioningTransaction:
        transaction = self._require(ProvisioningPhase.OUTBOUND_STAGED)
        transaction.phase = ProvisioningPhase.PICKUP_IN_PROGRESS
        self.journal.save(transaction)
        return transaction

    def verify_pickup(
        self,
        *,
        sources: Mapping[str, int],
        carried: Mapping[str, int],
        outbound: Mapping[str, int],
        returns: Mapping[str, int],
    ) -> ProvisioningTransaction:
        transaction = self._require(ProvisioningPhase.PICKUP_IN_PROGRESS)
        expected_carried = self._add(transaction.carried_before, transaction.kit)
        self._expect(transaction, sources, expected_carried, {}, transaction.return_before, carried, outbound, returns, "pickup")
        transaction.phase = ProvisioningPhase.PICKED_UP
        transaction.observations["carried_after_pickup"] = _counts(carried)
        transaction.reason = ""
        self.journal.save(transaction)
        return transaction

    def mark_return(self) -> ProvisioningTransaction:
        transaction = self._require(ProvisioningPhase.PICKED_UP)
        transaction.phase = ProvisioningPhase.RETURN_IN_PROGRESS
        self.journal.save(transaction)
        return transaction

    def verify_return(
        self,
        *,
        sources: Mapping[str, int],
        carried: Mapping[str, int],
        outbound: Mapping[str, int],
        returns: Mapping[str, int],
    ) -> ProvisioningTransaction:
        transaction = self._require(ProvisioningPhase.RETURN_IN_PROGRESS)
        expected_returns = self._add(transaction.return_before, transaction.kit)
        self._expect(transaction, sources, transaction.carried_before, {}, expected_returns, carried, outbound, returns, "return")
        transaction.phase = ProvisioningPhase.RETURNED
        transaction.observations["returns_after"] = _counts(returns)
        transaction.reason = ""
        self.journal.save(transaction)
        return transaction

    def startup_recover(self) -> ProvisioningTransaction | None:
        transaction = self.journal.load()
        if transaction is not None and transaction.phase in ACTIVE_PHASES:
            self._acquire(transaction)
            transaction.phase = ProvisioningPhase.RECOVERY_REQUIRED
            transaction.reason = "controller restarted before provisioning commit"
            self.journal.save(transaction)
        return transaction

    def reconcile_recovery(
        self,
        *,
        sources: Mapping[str, int],
        carried: Mapping[str, int],
        outbound: Mapping[str, int],
        returns: Mapping[str, int],
    ) -> ProvisioningTransaction:
        transaction = self._require(ProvisioningPhase.RECOVERY_REQUIRED)
        patterns = (
            (
                ProvisioningPhase.OUTBOUND_STAGED,
                transaction.carried_before,
                transaction.kit,
                transaction.return_before,
            ),
            (
                ProvisioningPhase.PICKED_UP,
                self._add(transaction.carried_before, transaction.kit),
                {},
                transaction.return_before,
            ),
            (
                ProvisioningPhase.RETURNED,
                transaction.carried_before,
                {},
                self._add(transaction.return_before, transaction.kit),
            ),
        )
        for phase, expected_carried, expected_outbound, expected_returns in patterns:
            if self._matches(
                transaction,
                sources,
                expected_carried,
                expected_outbound,
                expected_returns,
                carried,
                outbound,
                returns,
            ):
                transaction.phase = phase
                transaction.reason = ""
                transaction.observations[f"recovered_{phase.value.lower()}"] = _counts(carried)
                self.journal.save(transaction)
                return transaction
        self._recovery(transaction, "provisioning recovery boundary is ambiguous")
        raise RuntimeError(transaction.reason)

    def commit(self, *, worker_inventory_hash_current: str) -> ProvisioningTransaction:
        transaction = self._require(ProvisioningPhase.RETURNED)
        if str(worker_inventory_hash_current) != transaction.worker_inventory_hash_before:
            self._recovery(transaction, "worker inventory hash did not return to baseline")
            raise RuntimeError(transaction.reason)
        transaction.phase = ProvisioningPhase.COMMITTED
        transaction.reason = ""
        self.journal.save(transaction)
        if not self.lease.release(transaction.transaction_id):
            raise RuntimeError("committed provisioning did not release every lease")
        return transaction

    @staticmethod
    def _add(left: Mapping[str, int], right: Mapping[str, int]) -> dict[str, int]:
        keys = set(left) | set(right)
        return {key: int(left.get(key, 0)) + int(right.get(key, 0)) for key in keys}

    @staticmethod
    def _expected_sources(transaction: ProvisioningTransaction) -> dict[str, int]:
        return {
            item: transaction.source_before[item] - transaction.kit[item]
            for item in transaction.kit
        }

    def _matches(
        self,
        transaction: ProvisioningTransaction,
        sources: Mapping[str, int],
        expected_carried: Mapping[str, int],
        expected_outbound: Mapping[str, int],
        expected_returns: Mapping[str, int],
        carried: Mapping[str, int],
        outbound: Mapping[str, int],
        returns: Mapping[str, int],
    ) -> bool:
        return (
            _counts(sources) == self._expected_sources(transaction)
            and _counts(carried) == _counts(expected_carried)
            and _counts(outbound) == _counts(expected_outbound)
            and _counts(returns) == _counts(expected_returns)
        )

    def _expect(
        self,
        transaction: ProvisioningTransaction,
        sources: Mapping[str, int],
        expected_carried: Mapping[str, int],
        expected_outbound: Mapping[str, int],
        expected_returns: Mapping[str, int],
        carried: Mapping[str, int],
        outbound: Mapping[str, int],
        returns: Mapping[str, int],
        label: str,
    ) -> None:
        if not self._matches(
            transaction,
            sources,
            expected_carried,
            expected_outbound,
            expected_returns,
            carried,
            outbound,
            returns,
        ):
            self._recovery(transaction, f"provisioning {label} conservation mismatch")
            raise RuntimeError(transaction.reason)

    def _require(self, phase: ProvisioningPhase) -> ProvisioningTransaction:
        transaction = self.journal.load()
        if transaction is None or transaction.phase is not phase:
            actual = None if transaction is None else transaction.phase.value
            raise RuntimeError(f"expected provisioning phase {phase.value}, found {actual}")
        self._acquire(transaction)
        return transaction

    def _acquire(self, transaction: ProvisioningTransaction) -> None:
        if not self.lease.acquire(transaction.transaction_id):
            raise RuntimeError("provisioning endpoints are leased by another owner")

    def _recovery(self, transaction: ProvisioningTransaction, reason: str) -> None:
        transaction.phase = ProvisioningPhase.RECOVERY_REQUIRED
        transaction.reason = str(reason)
        self.journal.save(transaction)


__all__ = [
    "ProvisioningCoordinates",
    "ProvisioningJournal",
    "ProvisioningPhase",
    "ProvisioningTransaction",
    "TransactionalProvisioningController",
]
