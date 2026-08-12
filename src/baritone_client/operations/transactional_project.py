"""Restart-safe accounting for one reversible warehouse-to-project loop."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
import json
from pathlib import Path
from typing import Mapping, Protocol, Sequence


Position = tuple[int, int, int]


class ProjectPhase(str, Enum):
    """Durable boundaries of a delivery, build, audit, and salvage contract."""

    RESERVED = "RESERVED"
    PICKUP_IN_PROGRESS = "PICKUP_IN_PROGRESS"
    PICKED_UP = "PICKED_UP"
    BUILD_IN_PROGRESS = "BUILD_IN_PROGRESS"
    BUILT = "BUILT"
    AUDITED = "AUDITED"
    PARTIAL_RETURN = "PARTIAL_RETURN"
    SALVAGE_IN_PROGRESS = "SALVAGE_IN_PROGRESS"
    SALVAGED = "SALVAGED"
    RETURN_IN_PROGRESS = "RETURN_IN_PROGRESS"
    RETURNED = "RETURNED"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"
    COMMITTED = "COMMITTED"


ACTIVE_PHASES = frozenset(ProjectPhase) - {ProjectPhase.COMMITTED}


class Lease(Protocol):
    """Minimal catalog lease interface used by the project controller."""

    lease_keys: Sequence[str]

    def acquire(self, owner: str) -> bool: ...

    def release(self, owner: str) -> bool: ...


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _counts(value: Mapping[str, int]) -> dict[str, int]:
    return {str(key): int(count) for key, count in value.items() if int(count)}


@dataclass
class ProjectTransaction:
    """Persisted conservation boundary for one reversible project."""

    transaction_id: str
    phase: ProjectPhase
    worker: str
    project_id: str
    locker: Position
    project_blocks: tuple[Position, ...]
    material: str
    material_count: int
    kit: dict[str, int]
    carried_before: dict[str, int]
    locker_before: dict[str, int]
    worker_inventory_hash_before: str
    worker_position_before: tuple[float, float, float]
    worker_selected_slot_before: int
    started_at: str
    updated_at: str
    observations: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        value = asdict(self)
        value["phase"] = self.phase.value
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "ProjectTransaction":
        return cls(
            transaction_id=str(value["transaction_id"]),
            phase=ProjectPhase(str(value["phase"])),
            worker=str(value["worker"]),
            project_id=str(value["project_id"]),
            locker=tuple(int(axis) for axis in value["locker"]),
            project_blocks=tuple(
                tuple(int(axis) for axis in position)
                for position in value["project_blocks"]
            ),
            material=str(value["material"]),
            material_count=int(value["material_count"]),
            kit=_counts(value["kit"]),
            carried_before=_counts(value["carried_before"]),
            locker_before=_counts(value["locker_before"]),
            worker_inventory_hash_before=str(value["worker_inventory_hash_before"]),
            worker_position_before=tuple(
                float(axis) for axis in value["worker_position_before"]
            ),
            worker_selected_slot_before=int(value["worker_selected_slot_before"]),
            started_at=str(value["started_at"]),
            updated_at=str(value["updated_at"]),
            observations=dict(value.get("observations", {})),
        )


class ProjectJournal:
    """Atomic JSON journal for the single active S11 contract."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> ProjectTransaction | None:
        if not self.path.exists():
            return None
        return ProjectTransaction.from_dict(
            json.loads(self.path.read_text(encoding="utf-8"))
        )

    def save(self, transaction: ProjectTransaction) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(transaction.to_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.path)


class TransactionalProjectController:
    """Validate conservation at every reversible project boundary."""

    def __init__(self, journal: ProjectJournal, lease: Lease) -> None:
        self.journal = journal
        self.lease = lease

    def begin(
        self,
        transaction_id: str,
        *,
        worker: str,
        project_id: str,
        locker: Position,
        project_blocks: Sequence[Position],
        material: str,
        material_count: int,
        kit: Mapping[str, int],
        carried_before: Mapping[str, int],
        locker_before: Mapping[str, int],
        worker_inventory_hash_before: str,
        worker_position_before: Sequence[float],
        worker_selected_slot_before: int,
        project_material_blocks: int,
    ) -> ProjectTransaction:
        current = self.journal.load()
        if current is not None and current.phase in ACTIVE_PHASES:
            raise RuntimeError(f"active project transaction {current.transaction_id}")
        if project_material_blocks:
            raise RuntimeError("project box must be empty before reservation")
        normalized_kit = _counts(kit)
        if _counts(locker_before) != normalized_kit:
            raise RuntimeError("locker does not contain the approved project kit")
        if normalized_kit.get(material, 0) < material_count or material_count < 1:
            raise RuntimeError("project material is absent or insufficient")
        if len(tuple(project_blocks)) != material_count:
            raise RuntimeError("project block count does not match the contract")
        if not self.lease.acquire(transaction_id):
            raise RuntimeError("project lease is already owned")
        now = _utc_now()
        transaction = ProjectTransaction(
            transaction_id=transaction_id,
            phase=ProjectPhase.RESERVED,
            worker=worker,
            project_id=project_id,
            locker=locker,
            project_blocks=tuple(tuple(int(axis) for axis in p) for p in project_blocks),
            material=material,
            material_count=int(material_count),
            kit=normalized_kit,
            carried_before=_counts(carried_before),
            locker_before=_counts(locker_before),
            worker_inventory_hash_before=worker_inventory_hash_before,
            worker_position_before=tuple(float(axis) for axis in worker_position_before),
            worker_selected_slot_before=int(worker_selected_slot_before),
            started_at=now,
            updated_at=now,
        )
        self.journal.save(transaction)
        return transaction

    def mark_pickup(self) -> ProjectTransaction:
        return self._advance(ProjectPhase.RESERVED, ProjectPhase.PICKUP_IN_PROGRESS)

    def verify_pickup(
        self, *, carried: Mapping[str, int], locker: Mapping[str, int], project_blocks: int
    ) -> ProjectTransaction:
        transaction = self._require(ProjectPhase.PICKUP_IN_PROGRESS)
        self._expect(
            transaction,
            carried=self._add(transaction.carried_before, transaction.kit),
            locker={},
            project_blocks=0,
            actual_carried=carried,
            actual_locker=locker,
            actual_project_blocks=project_blocks,
            boundary="pickup",
        )
        return self._save(transaction, ProjectPhase.PICKED_UP)

    def mark_build(self) -> ProjectTransaction:
        return self._advance(ProjectPhase.PICKED_UP, ProjectPhase.BUILD_IN_PROGRESS)

    def verify_built(
        self, *, carried: Mapping[str, int], locker: Mapping[str, int], project_blocks: int
    ) -> ProjectTransaction:
        transaction = self._require(ProjectPhase.BUILD_IN_PROGRESS)
        expected = self._add(transaction.carried_before, transaction.kit)
        expected[transaction.material] -= transaction.material_count
        self._expect(
            transaction,
            carried=expected,
            locker={},
            project_blocks=transaction.material_count,
            actual_carried=carried,
            actual_locker=locker,
            actual_project_blocks=project_blocks,
            boundary="build",
        )
        transaction.observations["verified_blocks_placed"] = transaction.material_count
        return self._save(transaction, ProjectPhase.BUILT)

    def verify_audit(self, *, project_blocks: int, auditor: str) -> ProjectTransaction:
        transaction = self._require(ProjectPhase.BUILT)
        if project_blocks != transaction.material_count or auditor == transaction.worker:
            return self._recovery(transaction, "independent build audit failed")
        transaction.observations["independent_auditor"] = auditor
        transaction.observations["audited_blocks"] = project_blocks
        return self._save(transaction, ProjectPhase.AUDITED)

    def verify_partial_return(
        self, *, carried: Mapping[str, int], locker: Mapping[str, int], project_blocks: int
    ) -> ProjectTransaction:
        transaction = self._require(ProjectPhase.AUDITED)
        returned = dict(transaction.kit)
        returned.pop(transaction.material, None)
        expected = dict(transaction.carried_before)
        expected[transaction.material] = (
            expected.get(transaction.material, 0)
            + transaction.kit[transaction.material]
            - transaction.material_count
        )
        self._expect(
            transaction,
            carried=expected,
            locker=returned,
            project_blocks=transaction.material_count,
            actual_carried=carried,
            actual_locker=locker,
            actual_project_blocks=project_blocks,
            boundary="partial return",
        )
        return self._save(transaction, ProjectPhase.PARTIAL_RETURN)

    def mark_salvage(self) -> ProjectTransaction:
        return self._advance(ProjectPhase.PARTIAL_RETURN, ProjectPhase.SALVAGE_IN_PROGRESS)

    def verify_salvaged(
        self, *, carried: Mapping[str, int], locker: Mapping[str, int], project_blocks: int
    ) -> ProjectTransaction:
        transaction = self._require(ProjectPhase.SALVAGE_IN_PROGRESS)
        returned = dict(transaction.kit)
        returned.pop(transaction.material, None)
        expected = dict(transaction.carried_before)
        expected[transaction.material] = (
            expected.get(transaction.material, 0) + transaction.kit[transaction.material]
        )
        self._expect(
            transaction,
            carried=expected,
            locker=returned,
            project_blocks=0,
            actual_carried=carried,
            actual_locker=locker,
            actual_project_blocks=project_blocks,
            boundary="salvage",
        )
        transaction.observations["verified_blocks_salvaged"] = transaction.material_count
        return self._save(transaction, ProjectPhase.SALVAGED)

    def mark_return(self) -> ProjectTransaction:
        return self._advance(ProjectPhase.SALVAGED, ProjectPhase.RETURN_IN_PROGRESS)

    def verify_return(
        self,
        *,
        carried: Mapping[str, int],
        locker: Mapping[str, int],
        project_blocks: int,
        worker_inventory_hash: str,
    ) -> ProjectTransaction:
        transaction = self._require(ProjectPhase.RETURN_IN_PROGRESS)
        self._expect(
            transaction,
            carried=transaction.carried_before,
            locker=transaction.kit,
            project_blocks=0,
            actual_carried=carried,
            actual_locker=locker,
            actual_project_blocks=project_blocks,
            boundary="return",
        )
        if worker_inventory_hash != transaction.worker_inventory_hash_before:
            return self._recovery(transaction, "worker inventory hash was not restored")
        return self._save(transaction, ProjectPhase.RETURNED)

    def startup_recover(self) -> ProjectTransaction | None:
        transaction = self.journal.load()
        if transaction is not None and transaction.phase in ACTIVE_PHASES:
            transaction.observations["interrupted_phase"] = transaction.phase.value
            return self._save(transaction, ProjectPhase.RECOVERY_REQUIRED)
        return transaction

    def reconcile_recovery(
        self, *, carried: Mapping[str, int], locker: Mapping[str, int], project_blocks: int
    ) -> ProjectTransaction:
        transaction = self._require(ProjectPhase.RECOVERY_REQUIRED)
        patterns = [
            (ProjectPhase.RETURNED, transaction.carried_before, transaction.kit, 0),
            (ProjectPhase.SALVAGED, self._salvaged_carried(transaction), self._nonmaterial_kit(transaction), 0),
            (ProjectPhase.PARTIAL_RETURN, self._partial_return_carried(transaction), self._nonmaterial_kit(transaction), transaction.material_count),
            (ProjectPhase.BUILT, self._built_carried(transaction), {}, transaction.material_count),
            (ProjectPhase.PICKED_UP, self._add(transaction.carried_before, transaction.kit), {}, 0),
            (ProjectPhase.RESERVED, transaction.carried_before, transaction.kit, 0),
        ]
        interrupted = transaction.observations.get("interrupted_phase")
        if interrupted:
            preferred = next(
                (row for row in patterns if row[0].value == interrupted), None
            )
            if preferred is not None:
                patterns.remove(preferred)
                patterns.insert(0, preferred)
        for phase, expected_carried, expected_locker, expected_blocks in patterns:
            if self._matches(expected_carried, expected_locker, expected_blocks, carried, locker, project_blocks):
                transaction.observations[f"recovered_{phase.value.lower()}"] = True
                return self._save(transaction, phase)
        return self._recovery(transaction, "physical state matches no conserved boundary")

    def commit(self, *, worker_inventory_hash: str) -> ProjectTransaction:
        transaction = self._require(ProjectPhase.RETURNED)
        if worker_inventory_hash != transaction.worker_inventory_hash_before:
            return self._recovery(transaction, "commit inventory hash mismatch")
        if not self.lease.release(transaction.transaction_id):
            return self._recovery(transaction, "project lease release failed")
        return self._save(transaction, ProjectPhase.COMMITTED)

    @staticmethod
    def _add(left: Mapping[str, int], right: Mapping[str, int]) -> dict[str, int]:
        keys = set(left) | set(right)
        return _counts({key: int(left.get(key, 0)) + int(right.get(key, 0)) for key in keys})

    @staticmethod
    def _nonmaterial_kit(transaction: ProjectTransaction) -> dict[str, int]:
        result = dict(transaction.kit)
        result.pop(transaction.material, None)
        return result

    @classmethod
    def _built_carried(cls, transaction: ProjectTransaction) -> dict[str, int]:
        result = cls._add(transaction.carried_before, transaction.kit)
        result[transaction.material] = (
            result[transaction.material] - transaction.material_count
        )
        return _counts(result)

    @classmethod
    def _partial_return_carried(cls, transaction: ProjectTransaction) -> dict[str, int]:
        result = dict(transaction.carried_before)
        result[transaction.material] = (
            result.get(transaction.material, 0)
            + transaction.kit[transaction.material]
            - transaction.material_count
        )
        return _counts(result)

    @classmethod
    def _salvaged_carried(cls, transaction: ProjectTransaction) -> dict[str, int]:
        result = dict(transaction.carried_before)
        result[transaction.material] = (
            result.get(transaction.material, 0) + transaction.kit[transaction.material]
        )
        return _counts(result)

    @staticmethod
    def _matches(expected_carried, expected_locker, expected_blocks, carried, locker, blocks) -> bool:
        return (
            _counts(carried) == _counts(expected_carried)
            and _counts(locker) == _counts(expected_locker)
            and int(blocks) == int(expected_blocks)
        )

    def _expect(
        self,
        transaction: ProjectTransaction,
        *,
        carried,
        locker,
        project_blocks,
        actual_carried,
        actual_locker,
        actual_project_blocks,
        boundary: str,
    ) -> None:
        if not self._matches(
            carried, locker, project_blocks, actual_carried, actual_locker, actual_project_blocks
        ):
            self._recovery(transaction, f"{boundary} conservation mismatch")
            raise RuntimeError(f"{boundary} conservation mismatch")

    def _advance(self, expected: ProjectPhase, target: ProjectPhase) -> ProjectTransaction:
        transaction = self._require(expected)
        return self._save(transaction, target)

    def _require(self, phase: ProjectPhase) -> ProjectTransaction:
        transaction = self.journal.load()
        actual = None if transaction is None else transaction.phase.value
        if transaction is None or transaction.phase is not phase:
            raise RuntimeError(f"expected project phase {phase.value}, found {actual}")
        return transaction

    def _save(self, transaction: ProjectTransaction, phase: ProjectPhase) -> ProjectTransaction:
        transaction.phase = phase
        transaction.updated_at = _utc_now()
        self.journal.save(transaction)
        return transaction

    def _recovery(self, transaction: ProjectTransaction, reason: str) -> ProjectTransaction:
        transaction.observations["recovery_reason"] = reason
        return self._save(transaction, ProjectPhase.RECOVERY_REQUIRED)


__all__ = [
    "ProjectJournal",
    "ProjectPhase",
    "ProjectTransaction",
    "TransactionalProjectController",
]
