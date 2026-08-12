"""Restart-safe reconciliation for one metered furnace work order."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Protocol


class RefineryPhase(str, Enum):
    """Durable boundaries for a furnace batch."""

    PREPARED = "PREPARED"
    STAGED = "STAGED"
    RUNNING = "RUNNING"
    OUTPUT_VERIFICATION = "OUTPUT_VERIFICATION"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"
    COMMITTED = "COMMITTED"


ACTIVE_PHASES = {
    RefineryPhase.PREPARED,
    RefineryPhase.STAGED,
    RefineryPhase.RUNNING,
    RefineryPhase.OUTPUT_VERIFICATION,
    RefineryPhase.RECOVERY_REQUIRED,
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class RefineryCoordinates:
    """Source, buffers, furnace, and output endpoints."""

    raw_source: tuple[int, int, int]
    fuel_source: tuple[int, int, int]
    raw_staging: tuple[int, int, int]
    fuel_staging: tuple[int, int, int]
    furnace: tuple[int, int, int]
    output: tuple[int, int, int]


@dataclass
class RefineryTransaction:
    """Physical count boundaries for one exact smelting batch."""

    transaction_id: str
    phase: RefineryPhase
    coordinates: RefineryCoordinates
    input_item: str
    output_item: str
    batch_count: int
    fuel_item: str
    fuel_count: int
    counts: dict[str, int] = field(default_factory=dict)
    reason: str = ""
    updated_at: str = field(default_factory=_now)
    schema: str = "mcbaratone-refinery-transaction/v1"

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["phase"] = self.phase.value
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RefineryTransaction":
        coordinates = value["coordinates"]
        return cls(
            transaction_id=str(value["transaction_id"]),
            phase=RefineryPhase(str(value["phase"])),
            coordinates=RefineryCoordinates(
                **{
                    key: tuple(int(axis) for axis in coordinates[key])
                    for key in (
                        "raw_source",
                        "fuel_source",
                        "raw_staging",
                        "fuel_staging",
                        "furnace",
                        "output",
                    )
                }
            ),
            input_item=str(value["input_item"]),
            output_item=str(value["output_item"]),
            batch_count=int(value["batch_count"]),
            fuel_item=str(value["fuel_item"]),
            fuel_count=int(value["fuel_count"]),
            counts={str(key): int(count) for key, count in value.get("counts", {}).items()},
            reason=str(value.get("reason", "")),
            updated_at=str(value.get("updated_at", _now())),
            schema=str(value.get("schema", "mcbaratone-refinery-transaction/v1")),
        )


class RefineryJournal:
    """Atomically persist one refinery transaction."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> RefineryTransaction | None:
        if not self.path.exists():
            return None
        return RefineryTransaction.from_dict(
            json.loads(self.path.read_text(encoding="utf-8"))
        )

    def save(self, transaction: RefineryTransaction) -> None:
        transaction.updated_at = _now()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(transaction.to_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, self.path)


class RefineryLease(Protocol):
    def acquire(self, owner: str) -> bool: ...

    def release(self, owner: str) -> bool: ...


class TransactionalRefineryController:
    """Reconcile sources, staging, furnace, and output for one work order."""

    def __init__(self, journal: RefineryJournal, lease: RefineryLease) -> None:
        self.journal = journal
        self.lease = lease

    def startup_recover(self) -> RefineryTransaction | None:
        transaction = self.journal.load()
        if transaction is not None and transaction.phase in ACTIVE_PHASES:
            self._acquire(transaction)
            transaction.phase = RefineryPhase.RECOVERY_REQUIRED
            transaction.reason = "controller restarted before refinery commit"
            self.journal.save(transaction)
        return transaction

    def begin(
        self,
        transaction_id: str,
        coordinates: RefineryCoordinates,
        *,
        input_item: str,
        output_item: str,
        batch_count: int,
        fuel_item: str,
        fuel_count: int,
        raw_source_before: int,
        fuel_source_before: int,
        output_before: int,
    ) -> RefineryTransaction:
        current = self.journal.load()
        if current is not None and current.phase in ACTIVE_PHASES:
            raise RuntimeError(
                f"unfinished refinery order {current.transaction_id} requires recovery"
            )
        if batch_count <= 0 or fuel_count <= 0:
            raise ValueError("refinery batch and fuel counts must be positive")
        if raw_source_before < batch_count or fuel_source_before < fuel_count:
            raise ValueError("refinery sources cannot satisfy the work order")
        owner = str(transaction_id)
        if not self.lease.acquire(owner):
            raise RuntimeError("refinery endpoints are leased by another owner")
        transaction = RefineryTransaction(
            transaction_id=owner,
            phase=RefineryPhase.PREPARED,
            coordinates=coordinates,
            input_item=str(input_item),
            output_item=str(output_item),
            batch_count=int(batch_count),
            fuel_item=str(fuel_item),
            fuel_count=int(fuel_count),
            counts={
                "raw_source_before": int(raw_source_before),
                "fuel_source_before": int(fuel_source_before),
                "output_before": int(output_before),
            },
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
        raw_source_current: int,
        fuel_source_current: int,
        raw_staging: int,
        fuel_staging: int,
        furnace_input: int,
        furnace_fuel: int,
        furnace_output: int,
        output_current: int,
    ) -> RefineryTransaction:
        transaction = self._require(RefineryPhase.PREPARED)
        observed = self._deltas(
            transaction,
            raw_source_current=raw_source_current,
            fuel_source_current=fuel_source_current,
            output_current=output_current,
        )
        expected = (transaction.batch_count, transaction.fuel_count, 0)
        if (
            observed != expected
            or int(raw_staging) != transaction.batch_count
            or int(fuel_staging) != transaction.fuel_count
            or any(int(value) for value in (furnace_input, furnace_fuel, furnace_output))
        ):
            self._recovery(
                transaction,
                "refinery staging mismatch: "
                f"deltas={observed}, raw_staging={raw_staging}, "
                f"fuel_staging={fuel_staging}, furnace="
                f"{(furnace_input, furnace_fuel, furnace_output)}",
            )
            raise RuntimeError(transaction.reason)
        transaction.phase = RefineryPhase.STAGED
        transaction.reason = ""
        self.journal.save(transaction)
        return transaction

    def mark_running(self) -> RefineryTransaction:
        transaction = self._require(RefineryPhase.STAGED)
        transaction.phase = RefineryPhase.RUNNING
        self.journal.save(transaction)
        return transaction

    def reconcile_recovery(
        self,
        *,
        raw_source_current: int,
        fuel_source_current: int,
        raw_staging: int,
        fuel_staging: int,
        furnace_input: int,
        furnace_fuel: int,
        furnace_output: int,
        output_current: int,
        lit_time_remaining: int,
    ) -> RefineryTransaction:
        transaction = self._require(RefineryPhase.RECOVERY_REQUIRED)
        deltas = self._deltas(
            transaction,
            raw_source_current=raw_source_current,
            fuel_source_current=fuel_source_current,
            output_current=output_current,
        )
        expected = (transaction.batch_count, transaction.fuel_count, 0)
        total_input = int(raw_staging) + int(furnace_input) + int(furnace_output)
        if deltas != expected or total_input != transaction.batch_count:
            self._recovery(
                transaction,
                "refinery recovery mismatch: "
                f"deltas={deltas}, conserved_input={total_input}, "
                f"fuel_staging={fuel_staging}, furnace_fuel={furnace_fuel}",
            )
            raise RuntimeError(transaction.reason)
        if (
            int(raw_staging) == transaction.batch_count
            and int(fuel_staging) == transaction.fuel_count
            and furnace_input == furnace_fuel == furnace_output == 0
            and int(lit_time_remaining) == 0
        ):
            transaction.phase = RefineryPhase.STAGED
        elif (
            int(raw_staging) == 0
            and int(fuel_staging) == 0
            and int(furnace_input) + int(furnace_output) == transaction.batch_count
            and (int(furnace_fuel) >= 0)
            and (int(lit_time_remaining) > 0 or int(furnace_input) == 0)
        ):
            transaction.phase = (
                RefineryPhase.OUTPUT_VERIFICATION
                if int(furnace_input) == 0
                else RefineryPhase.RUNNING
            )
        else:
            self._recovery(
                transaction,
                "refinery recovery boundary is ambiguous: "
                f"raw_staging={raw_staging}, fuel_staging={fuel_staging}, "
                f"furnace={(furnace_input, furnace_fuel, furnace_output)}, "
                f"lit={lit_time_remaining}",
            )
            raise RuntimeError(transaction.reason)
        transaction.counts.update(
            recovery_furnace_input=int(furnace_input),
            recovery_furnace_output=int(furnace_output),
            recovery_lit_time=int(lit_time_remaining),
        )
        transaction.reason = ""
        self.journal.save(transaction)
        return transaction

    def mark_output_verification(self) -> RefineryTransaction:
        transaction = self.journal.load()
        if transaction is None or transaction.phase not in {
            RefineryPhase.RUNNING,
            RefineryPhase.RECOVERY_REQUIRED,
        }:
            actual = None if transaction is None else transaction.phase.value
            raise RuntimeError(f"cannot verify refinery output from {actual}")
        self._acquire(transaction)
        transaction.phase = RefineryPhase.OUTPUT_VERIFICATION
        transaction.reason = ""
        self.journal.save(transaction)
        return transaction

    def reconcile_and_commit(
        self,
        *,
        raw_source_current: int,
        fuel_source_current: int,
        raw_staging: int,
        fuel_staging: int,
        furnace_input: int,
        furnace_output: int,
        output_after: int,
    ) -> RefineryTransaction:
        transaction = self.journal.load()
        if transaction is None or transaction.phase not in {
            RefineryPhase.OUTPUT_VERIFICATION,
            RefineryPhase.RECOVERY_REQUIRED,
        }:
            actual = None if transaction is None else transaction.phase.value
            raise RuntimeError(f"cannot commit refinery order from {actual}")
        self._acquire(transaction)
        deltas = self._deltas(
            transaction,
            raw_source_current=raw_source_current,
            fuel_source_current=fuel_source_current,
            output_current=output_after,
        )
        expected = (
            transaction.batch_count,
            transaction.fuel_count,
            transaction.batch_count,
        )
        if (
            deltas != expected
            or any(
                int(value)
                for value in (
                    raw_staging,
                    fuel_staging,
                    furnace_input,
                    furnace_output,
                )
            )
        ):
            self._recovery(
                transaction,
                "refinery commit mismatch: "
                f"deltas={deltas}, staging={(raw_staging, fuel_staging)}, "
                f"furnace={(furnace_input, furnace_output)}",
            )
            raise RuntimeError(transaction.reason)
        transaction.counts.update(
            raw_source_after=int(raw_source_current),
            fuel_source_after=int(fuel_source_current),
            output_after=int(output_after),
        )
        transaction.phase = RefineryPhase.COMMITTED
        transaction.reason = ""
        self.journal.save(transaction)
        if not self.lease.release(transaction.transaction_id):
            raise RuntimeError("committed refinery order did not release its lease")
        return transaction

    @staticmethod
    def _deltas(
        transaction: RefineryTransaction,
        *,
        raw_source_current: int,
        fuel_source_current: int,
        output_current: int,
    ) -> tuple[int, int, int]:
        return (
            transaction.counts["raw_source_before"] - int(raw_source_current),
            transaction.counts["fuel_source_before"] - int(fuel_source_current),
            int(output_current) - transaction.counts["output_before"],
        )

    def _require(self, phase: RefineryPhase) -> RefineryTransaction:
        transaction = self.journal.load()
        if transaction is None or transaction.phase is not phase:
            actual = None if transaction is None else transaction.phase.value
            raise RuntimeError(f"expected refinery phase {phase.value}, found {actual}")
        self._acquire(transaction)
        return transaction

    def _acquire(self, transaction: RefineryTransaction) -> None:
        if not self.lease.acquire(transaction.transaction_id):
            raise RuntimeError("refinery endpoints are leased by another owner")

    def _recovery(self, transaction: RefineryTransaction, reason: str) -> None:
        transaction.phase = RefineryPhase.RECOVERY_REQUIRED
        transaction.reason = str(reason)
        self.journal.save(transaction)


__all__ = [
    "RefineryCoordinates",
    "RefineryJournal",
    "RefineryPhase",
    "RefineryTransaction",
    "TransactionalRefineryController",
]
