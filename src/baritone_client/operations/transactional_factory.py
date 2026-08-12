"""Fail-closed control and reconciliation for one bounded Crafter batch.

The durable journal records intent before the normally-off power control is
energized.  On startup the controller removes and verifies power before it
reads the journal, then reconstructs an interrupted batch from the physical
Crafter and output-buffer inventories.  It never guesses through a mixed or
unconserved state.
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
SlotContents = Mapping[int, tuple[str, int]]
ItemTotals = Mapping[str, int]


class FactoryPhase(str, Enum):
    """Durable phases for one fixed-recipe production order."""

    PREPARED = "PREPARED"
    STAGED = "STAGED"
    RUNNING = "RUNNING"
    OUTPUT_VERIFICATION = "OUTPUT_VERIFICATION"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"
    COMMITTED = "COMMITTED"
    ABORTED = "ABORTED"


ACTIVE_PHASES = {
    FactoryPhase.PREPARED,
    FactoryPhase.STAGED,
    FactoryPhase.RUNNING,
    FactoryPhase.OUTPUT_VERIFICATION,
    FactoryPhase.RECOVERY_REQUIRED,
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class FactoryCoordinates:
    """Immutable endpoints and normally-air power coordinate for one cell."""

    staging: Position
    crafter: Position
    output: Position
    flush: Position
    control: Position


@dataclass(frozen=True)
class FixedRecipe:
    """Exact one-pulse recipe contract for a fixed Crafter grid."""

    recipe_id: str
    slots: dict[int, tuple[str, int]]
    output_item: str
    output_count: int

    def __post_init__(self) -> None:
        if not self.recipe_id:
            raise ValueError("recipe_id is required")
        if not self.slots:
            raise ValueError("at least one recipe slot is required")
        if any(slot < 0 or slot > 8 for slot in self.slots):
            raise ValueError("Crafter recipe slots must be between 0 and 8")
        if any(not item_id or count <= 0 for item_id, count in self.slots.values()):
            raise ValueError("recipe ingredients must have positive counts")
        if not self.output_item or self.output_count <= 0:
            raise ValueError("recipe output must have a positive count")


@dataclass
class FactoryTransaction:
    """One production order and its conservation observations."""

    transaction_id: str
    phase: FactoryPhase
    recipe: FixedRecipe
    coordinates: FactoryCoordinates
    counts: dict[str, int] = field(default_factory=dict)
    reason: str = ""
    updated_at: str = field(default_factory=_now)
    schema: str = "mcbaratone-factory-transaction/v1"

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["phase"] = self.phase.value
        value["recipe"]["slots"] = {
            str(slot): [item_id, count]
            for slot, (item_id, count) in self.recipe.slots.items()
        }
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "FactoryTransaction":
        recipe = value["recipe"]
        coordinates = value["coordinates"]
        return cls(
            transaction_id=str(value["transaction_id"]),
            phase=FactoryPhase(str(value["phase"])),
            recipe=FixedRecipe(
                recipe_id=str(recipe["recipe_id"]),
                slots={
                    int(slot): (str(item[0]), int(item[1]))
                    for slot, item in recipe["slots"].items()
                },
                output_item=str(recipe["output_item"]),
                output_count=int(recipe["output_count"]),
            ),
            coordinates=FactoryCoordinates(
                staging=tuple(int(axis) for axis in coordinates["staging"]),
                crafter=tuple(int(axis) for axis in coordinates["crafter"]),
                output=tuple(int(axis) for axis in coordinates["output"]),
                flush=tuple(int(axis) for axis in coordinates["flush"]),
                control=tuple(int(axis) for axis in coordinates["control"]),
            ),
            counts={str(key): int(count) for key, count in value.get("counts", {}).items()},
            reason=str(value.get("reason", "")),
            updated_at=str(value.get("updated_at", _now())),
            schema=str(value.get("schema", "mcbaratone-factory-transaction/v1")),
        )


class FactoryJournal:
    """Atomically persist one factory transaction."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> FactoryTransaction | None:
        if not self.path.exists():
            return None
        return FactoryTransaction.from_dict(
            json.loads(self.path.read_text(encoding="utf-8"))
        )

    def save(self, transaction: FactoryTransaction) -> None:
        transaction.updated_at = _now()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(transaction.to_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, self.path)


class FactoryLease(Protocol):
    """Exclusive lease for the cell and its endpoint buffers."""

    def acquire(self, owner: str) -> bool:
        """Claim or renew the cell for ``owner``."""

    def release(self, owner: str) -> bool:
        """Release the cell only when ``owner`` still owns it."""


class FactoryControl(Protocol):
    """Explicit physical control supplied by an authorized runtime.

    The transaction layer intentionally contains no RCON or world-edit
    implementation.  A caller must provide a bounded control whose safety
    policy, identity checks, and postconditions are enforced at the edge.
    """

    def lock(self) -> None:
        """Put the cell in its verified fail-closed state."""

    def pulse(self) -> None:
        """Perform exactly one verified production pulse."""

    def energize(self) -> None:
        """Energize once for the explicit interruption acceptance boundary."""


class CatalogFactoryLease:
    """Adapt StorageCatalog leases into one all-or-nothing cell lease."""

    def __init__(
        self,
        catalog: Any,
        lease_keys: tuple[str, ...],
        *,
        ttl_seconds: float = 600.0,
    ) -> None:
        self.catalog = catalog
        self.lease_keys = tuple(
            sorted(dict.fromkeys(str(key) for key in lease_keys))
        )
        if not self.lease_keys:
            raise ValueError("at least one factory lease key is required")
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


def _normalize_slots(value: SlotContents) -> dict[int, tuple[str, int]]:
    return {
        int(slot): (str(item_id), int(count))
        for slot, (item_id, count) in value.items()
        if int(count) > 0
    }


def _normalize_totals(value: ItemTotals) -> dict[str, int]:
    return {
        str(item_id): int(count)
        for item_id, count in value.items()
        if int(count) > 0
    }


def _slot_totals(value: SlotContents) -> dict[str, int]:
    result: dict[str, int] = {}
    for item_id, count in _normalize_slots(value).values():
        result[item_id] = result.get(item_id, 0) + count
    return result


class TransactionalFactoryController:
    """Run one fixed recipe only after exact staging and durable intent."""

    def __init__(
        self,
        journal: FactoryJournal,
        control: FactoryControl,
        lease: FactoryLease,
    ) -> None:
        self.journal = journal
        self.control = control
        self.lease = lease

    def startup_recover(self) -> FactoryTransaction | None:
        """Remove power before reading or reconstructing persisted state."""
        self.control.lock()
        transaction = self.journal.load()
        if transaction is not None and transaction.phase in ACTIVE_PHASES:
            self._acquire(transaction)
            transaction.phase = FactoryPhase.RECOVERY_REQUIRED
            transaction.reason = "controller restarted before factory commit"
            self.journal.save(transaction)
        return transaction

    def begin(
        self,
        transaction_id: str,
        recipe: FixedRecipe,
        coordinates: FactoryCoordinates,
        *,
        output_before: int,
        flush_before: int,
    ) -> FactoryTransaction:
        """Lock and reserve an empty cell for one production order."""
        self.control.lock()
        current = self.journal.load()
        if current is not None and current.phase in ACTIVE_PHASES:
            raise RuntimeError(
                f"unfinished factory order {current.transaction_id} requires recovery"
            )
        if output_before < 0 or flush_before < 0:
            raise ValueError("buffer counts cannot be negative")
        owner = str(transaction_id)
        if not self.lease.acquire(owner):
            raise RuntimeError("factory cell is leased by another owner")
        transaction = FactoryTransaction(
            transaction_id=owner,
            phase=FactoryPhase.PREPARED,
            recipe=recipe,
            coordinates=coordinates,
            counts={
                "output_before": int(output_before),
                "flush_before": int(flush_before),
            },
        )
        try:
            self.journal.save(transaction)
        except Exception:
            self.lease.release(owner)
            raise
        return transaction

    def verify_staged(
        self, *, input_slots: SlotContents, output_current: int, flush_current: int
    ) -> FactoryTransaction:
        """Accept only the exact immutable grid and unchanged buffers."""
        transaction = self._require(FactoryPhase.PREPARED)
        expected = _normalize_slots(transaction.recipe.slots)
        observed = _normalize_slots(input_slots)
        if (
            observed != expected
            or int(output_current) != transaction.counts["output_before"]
            or int(flush_current) != transaction.counts["flush_before"]
        ):
            self._recovery(
                transaction,
                "staging mismatch: "
                f"expected={expected}, observed={observed}, "
                f"output={output_current}, flush={flush_current}",
            )
            raise RuntimeError(transaction.reason)
        transaction.phase = FactoryPhase.STAGED
        transaction.counts["input_count"] = sum(
            count for _item_id, count in expected.values()
        )
        transaction.reason = ""
        self.journal.save(transaction)
        return transaction

    def run_once(self) -> FactoryTransaction:
        """Persist RUNNING before the single physical pulse."""
        transaction = self._require(FactoryPhase.STAGED)
        transaction.phase = FactoryPhase.RUNNING
        self.journal.save(transaction)
        try:
            self.control.pulse()
        except Exception as error:
            self._recovery(transaction, f"factory pulse failed: {error}")
            raise
        transaction.phase = FactoryPhase.OUTPUT_VERIFICATION
        transaction.reason = ""
        self.journal.save(transaction)
        return transaction

    def interrupt_while_powered(self) -> FactoryTransaction:
        """Leave one rising-edge craft powered to prove startup fail-closed.

        This is an acceptance-test boundary, not a normal production path.
        The next process must call :meth:`startup_recover`, which removes
        power before it reads the persisted RUNNING transaction.
        """
        transaction = self._require(FactoryPhase.STAGED)
        transaction.phase = FactoryPhase.RUNNING
        self.journal.save(transaction)
        try:
            self.control.energize()
        except Exception as error:
            self._recovery(transaction, f"factory interruption pulse failed: {error}")
            raise
        return transaction

    def reconcile_recovery(
        self, *, input_slots: SlotContents, output_current: int, flush_current: int
    ) -> FactoryTransaction:
        """Classify a restart only at an exact staged or exact output boundary."""
        transaction = self._require(FactoryPhase.RECOVERY_REQUIRED)
        expected_slots = _normalize_slots(transaction.recipe.slots)
        observed_slots = _normalize_slots(input_slots)
        output_delta = int(output_current) - transaction.counts["output_before"]
        flush_delta = int(flush_current) - transaction.counts["flush_before"]
        if observed_slots == expected_slots and output_delta == flush_delta == 0:
            transaction.phase = FactoryPhase.STAGED
        elif (
            not observed_slots
            and output_delta == transaction.recipe.output_count
            and flush_delta == 0
        ):
            transaction.phase = FactoryPhase.OUTPUT_VERIFICATION
        else:
            self._recovery(
                transaction,
                "recovery mismatch: "
                f"input={observed_slots}, output_delta={output_delta}, "
                f"flush_delta={flush_delta}",
            )
            raise RuntimeError(transaction.reason)
        transaction.counts.update(
            recovery_output=int(output_current),
            recovery_flush=int(flush_current),
        )
        transaction.reason = ""
        self.journal.save(transaction)
        return transaction

    def abort_after_flush(
        self,
        *,
        rejected_slots: SlotContents,
        input_after: SlotContents,
        output_current: int,
        flush_items_before: ItemTotals,
        flush_items_after: ItemTotals,
    ) -> FactoryTransaction:
        """Release a rejected order only after every staged item reaches flush."""
        transaction = self.journal.load()
        if transaction is None or transaction.phase not in {
            FactoryPhase.PREPARED,
            FactoryPhase.RECOVERY_REQUIRED,
        }:
            actual = None if transaction is None else transaction.phase.value
            raise RuntimeError(f"cannot abort factory order from {actual}")
        self._acquire(transaction)
        self.control.lock()
        before = _normalize_totals(flush_items_before)
        after = _normalize_totals(flush_items_after)
        expected_after = dict(before)
        for item_id, count in _slot_totals(rejected_slots).items():
            expected_after[item_id] = expected_after.get(item_id, 0) + count
        if (
            _normalize_slots(input_after)
            or int(output_current) != transaction.counts["output_before"]
            or after != expected_after
        ):
            self._recovery(
                transaction,
                "flush mismatch: "
                f"input_after={_normalize_slots(input_after)}, "
                f"output={output_current}, expected_flush={expected_after}, "
                f"observed_flush={after}",
            )
            raise RuntimeError(transaction.reason)
        transaction.phase = FactoryPhase.ABORTED
        transaction.reason = "rejected grid conserved in flush buffer"
        self.journal.save(transaction)
        if not self.lease.release(transaction.transaction_id):
            raise RuntimeError("aborted factory order did not release its lease")
        return transaction

    def reconcile_and_commit(
        self, *, input_slots: SlotContents, output_after: int, flush_after: int
    ) -> FactoryTransaction:
        """Commit only an empty grid and the exact one-batch output delta."""
        self.control.lock()
        transaction = self.journal.load()
        if transaction is None or transaction.phase not in {
            FactoryPhase.OUTPUT_VERIFICATION,
            FactoryPhase.RECOVERY_REQUIRED,
        }:
            actual = None if transaction is None else transaction.phase.value
            raise RuntimeError(f"cannot reconcile factory order from {actual}")
        self._acquire(transaction)
        output_delta = int(output_after) - transaction.counts["output_before"]
        flush_delta = int(flush_after) - transaction.counts["flush_before"]
        if (
            _normalize_slots(input_slots)
            or output_delta != transaction.recipe.output_count
            or flush_delta != 0
        ):
            self._recovery(
                transaction,
                "factory reconciliation mismatch: "
                f"input={_normalize_slots(input_slots)}, "
                f"output_delta={output_delta}, flush_delta={flush_delta}",
            )
            raise RuntimeError(transaction.reason)
        transaction.counts.update(
            output_after=int(output_after),
            flush_after=int(flush_after),
        )
        transaction.phase = FactoryPhase.COMMITTED
        transaction.reason = ""
        self.journal.save(transaction)
        if not self.lease.release(transaction.transaction_id):
            raise RuntimeError("committed factory order did not release its lease")
        return transaction

    def _require(self, phase: FactoryPhase) -> FactoryTransaction:
        transaction = self.journal.load()
        if transaction is None or transaction.phase is not phase:
            actual = None if transaction is None else transaction.phase.value
            raise RuntimeError(f"expected factory phase {phase.value}, found {actual}")
        self._acquire(transaction)
        return transaction

    def _acquire(self, transaction: FactoryTransaction) -> None:
        if not self.lease.acquire(transaction.transaction_id):
            raise RuntimeError("factory cell is leased by another owner")

    def _recovery(self, transaction: FactoryTransaction, reason: str) -> None:
        self.control.lock()
        transaction.phase = FactoryPhase.RECOVERY_REQUIRED
        transaction.reason = str(reason)
        self.journal.save(transaction)


__all__ = [
    "CatalogFactoryLease",
    "FactoryCoordinates",
    "FactoryControl",
    "FactoryJournal",
    "FactoryLease",
    "FactoryPhase",
    "FactoryTransaction",
    "FixedRecipe",
    "TransactionalFactoryController",
]
