from pathlib import Path

import pytest

from baritone_client.operations.transactional_migration import (
    CatalogMigrationLease,
    MigrationCoordinates,
    MigrationJournal,
    MigrationPhase,
    TransactionalMigrationController,
)


COORDINATES = MigrationCoordinates(
    source=((585, 79, -295), (586, 79, -295)),
    destination=(583, 74, -335),
)


class FakeLease:
    def __init__(self) -> None:
        self.owner = None

    def acquire(self, owner: str) -> bool:
        if self.owner not in (None, owner):
            return False
        self.owner = owner
        return True

    def release(self, owner: str) -> bool:
        if self.owner != owner:
            return False
        self.owner = None
        return True


def controller(tmp_path: Path):
    journal = MigrationJournal(tmp_path / "migration.json")
    lease = FakeLease()
    return TransactionalMigrationController(journal, lease), journal, lease


def begin(value: TransactionalMigrationController):
    return value.begin(
        "migration-1",
        "minecraft:raw_iron",
        64,
        COORDINATES,
        source_before=318,
        carried_before=0,
        destination_before=0,
    )


def test_happy_path_conserves_and_releases(tmp_path):
    value, journal, lease = controller(tmp_path)
    begin(value)
    value.mark_withdrawing()
    value.verify_withdrawal(
        source_after=254, carried_after=64, destination_current=0
    )
    value.mark_depositing()
    result = value.reconcile_and_commit(
        source_final=254, carried_final=0, destination_after=64
    )

    assert result.phase is MigrationPhase.COMMITTED
    assert journal.load().counts["destination_after"] == 64
    assert lease.owner is None


def test_restart_after_withdrawal_recovers_to_deposit_boundary(tmp_path):
    value, journal, lease = controller(tmp_path)
    begin(value)
    value.mark_withdrawing()
    value.verify_withdrawal(
        source_after=254, carried_after=64, destination_current=0
    )

    restarted = TransactionalMigrationController(journal, lease)
    assert restarted.startup_recover().phase is MigrationPhase.RECOVERY_REQUIRED
    recovered = restarted.reconcile_recovery(
        source_current=254, carried_current=64, destination_current=0
    )

    assert recovered.phase is MigrationPhase.WITHDRAWAL_VERIFIED


def test_restart_after_physical_deposit_can_commit(tmp_path):
    value, journal, lease = controller(tmp_path)
    begin(value)
    value.mark_withdrawing()
    value.verify_withdrawal(
        source_after=254, carried_after=64, destination_current=0
    )
    value.mark_depositing()

    restarted = TransactionalMigrationController(journal, lease)
    restarted.startup_recover()
    recovered = restarted.reconcile_recovery(
        source_current=254, carried_current=0, destination_current=64
    )
    assert recovered.phase is MigrationPhase.DEPOSITING
    assert restarted.reconcile_and_commit(
        source_final=254, carried_final=0, destination_after=64
    ).phase is MigrationPhase.COMMITTED


def test_recovery_refuses_missing_items(tmp_path):
    value, journal, lease = controller(tmp_path)
    begin(value)
    value.mark_withdrawing()
    value.startup_recover()

    with pytest.raises(RuntimeError, match="recovery mismatch"):
        value.reconcile_recovery(
            source_current=254, carried_current=60, destination_current=0
        )
    assert journal.load().phase is MigrationPhase.RECOVERY_REQUIRED
    assert lease.owner == "migration-1"


def test_begin_rejects_an_unavailable_source(tmp_path):
    value, _journal, lease = controller(tmp_path)
    with pytest.raises(ValueError, match="less than"):
        value.begin(
            "migration-1",
            "minecraft:raw_iron",
            64,
            COORDINATES,
            source_before=63,
            carried_before=0,
            destination_before=0,
        )
    assert lease.owner is None


class Catalog:
    def __init__(self) -> None:
        self.owners = {}
        self.blocked = set()

    def acquire_lease(self, key, owner, ttl_seconds):
        if key in self.blocked:
            return False
        current = self.owners.get(key)
        if current not in (None, owner):
            return False
        self.owners[key] = owner
        return True

    def release_lease(self, key, owner):
        if self.owners.get(key) != owner:
            return False
        del self.owners[key]
        return True


def test_catalog_lease_rolls_back_partial_acquisition():
    catalog = Catalog()
    catalog.blocked.add("b")
    lease = CatalogMigrationLease(catalog, ("a", "b", "c"))

    assert not lease.acquire("migration-1")
    assert catalog.owners == {}
