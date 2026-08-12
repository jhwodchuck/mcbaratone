from pathlib import Path

import pytest

from baritone_client.operations.transactional_intake import (
    IntakeCoordinates,
    IntakePhase,
    TransactionJournal,
    TransactionalIntakeController,
)


COORDINATES = IntakeCoordinates(
    intake=(579, 73, -337),
    hopper=(579, 72, -337),
    destination=(579, 71, -337),
    control=(580, 72, -337),
)


class FakeDrainControl:
    def __init__(self) -> None:
        self.locked = False
        self.commands: list[str] = []

    def lock(self) -> None:
        self.commands.append("lock")
        self.locked = True

    def unlock(self) -> None:
        self.commands.append("unlock")
        self.locked = False


class FakeLease:
    def __init__(self) -> None:
        self.owner = None
        self.acquired: list[str] = []
        self.released: list[str] = []

    def acquire(self, owner: str) -> bool:
        self.acquired.append(owner)
        if self.owner not in (None, owner):
            return False
        self.owner = owner
        return True

    def release(self, owner: str) -> bool:
        self.released.append(owner)
        if self.owner != owner:
            return False
        self.owner = None
        return True


def controller(path: Path, drain: FakeDrainControl):
    journal = TransactionJournal(path)
    lease = FakeLease()
    return TransactionalIntakeController(journal, drain, lease), journal


def begin_known_batch(instance: TransactionalIntakeController):
    instance.begin(
        "batch-001",
        "minecraft:dirt",
        1,
        COORDINATES,
        carried_before=593,
        intake_before=0,
        destination_before=0,
    )
    instance.mark_depositing()


def test_known_batch_commits_only_after_full_conservation(tmp_path):
    drain = FakeDrainControl()
    instance, journal = controller(tmp_path / "transaction.json", drain)
    begin_known_batch(instance)

    deposited = instance.verify_deposit(
        carried_after=592,
        intake_after=1,
        pipeline_after=0,
    )
    assert deposited.phase is IntakePhase.DEPOSIT_VERIFIED
    instance.enable_drain()
    assert not drain.locked

    committed = instance.reconcile_and_commit(
        intake_final=0,
        pipeline_final=0,
        destination_after=1,
    )
    assert committed.phase is IntakePhase.COMMITTED
    assert drain.locked
    assert journal.load().counts["destination_after"] == 1
    assert instance.lease.owner is None
    assert instance.lease.released == ["batch-001"]


def test_restart_locks_before_reading_and_never_resumes_active_drain(tmp_path):
    path = tmp_path / "transaction.json"
    drain = FakeDrainControl()
    first, _journal = controller(path, drain)
    begin_known_batch(first)
    first.verify_deposit(carried_after=592, intake_after=1, pipeline_after=0)
    first.enable_drain()
    assert not drain.locked

    command_count = len(drain.commands)
    restarted, journal = controller(path, drain)
    recovered = restarted.startup_recover()

    assert drain.commands[command_count] == "lock"
    assert drain.locked
    assert recovered.phase is IntakePhase.RECOVERY_REQUIRED
    assert journal.load().phase is IntakePhase.RECOVERY_REQUIRED
    assert "unlock" not in drain.commands[command_count:]


def test_deposit_mismatch_enters_recovery_with_drain_locked(tmp_path):
    drain = FakeDrainControl()
    instance, journal = controller(tmp_path / "transaction.json", drain)
    begin_known_batch(instance)

    with pytest.raises(RuntimeError, match="deposit mismatch"):
        instance.verify_deposit(
            carried_after=592,
            intake_after=0,
            pipeline_after=0,
        )

    assert drain.locked
    assert journal.load().phase is IntakePhase.RECOVERY_REQUIRED


def test_recovery_can_reconcile_a_batch_that_finished_during_restart(tmp_path):
    path = tmp_path / "transaction.json"
    drain = FakeDrainControl()
    first, _journal = controller(path, drain)
    begin_known_batch(first)
    first.verify_deposit(carried_after=592, intake_after=1, pipeline_after=0)
    first.enable_drain()

    restarted, journal = controller(path, drain)
    restarted.startup_recover()
    committed = restarted.reconcile_and_commit(
        intake_final=0,
        pipeline_final=0,
        destination_after=1,
    )

    assert committed.phase is IntakePhase.COMMITTED
    assert journal.load().phase is IntakePhase.COMMITTED
    assert drain.locked


def test_recovery_resumes_only_when_in_flight_counts_conserve(tmp_path):
    path = tmp_path / "transaction.json"
    drain = FakeDrainControl()
    first, _journal = controller(path, drain)
    begin_known_batch(first)
    first.verify_deposit(carried_after=592, intake_after=1, pipeline_after=0)
    first.enable_drain()

    restarted, journal = controller(path, drain)
    restarted.startup_recover()
    resumed = restarted.resume_recovery_drain(
        intake_current=1,
        pipeline_current=0,
        destination_current=0,
    )

    assert resumed.phase is IntakePhase.DRAIN_ENABLED
    assert journal.load().counts["recovery_intake"] == 1
    assert not drain.locked


def test_deposit_completed_during_restart_can_be_recovered(tmp_path):
    path = tmp_path / "transaction.json"
    drain = FakeDrainControl()
    first, _journal = controller(path, drain)
    begin_known_batch(first)

    restarted, journal = controller(path, drain)
    restarted.startup_recover()
    recovered = restarted.recover_deposit(
        carried_after=592,
        intake_after=1,
        pipeline_after=0,
    )

    assert recovered.phase is IntakePhase.DEPOSIT_VERIFIED
    assert journal.load().counts["intake_after"] == 1
    assert drain.locked


def test_interrupted_deposit_mismatch_remains_locked_for_recovery(tmp_path):
    path = tmp_path / "transaction.json"
    drain = FakeDrainControl()
    first, _journal = controller(path, drain)
    begin_known_batch(first)

    restarted, journal = controller(path, drain)
    restarted.startup_recover()
    with pytest.raises(RuntimeError, match="recovered deposit mismatch"):
        restarted.recover_deposit(
            carried_after=592,
            intake_after=0,
            pipeline_after=0,
        )

    assert journal.load().phase is IntakePhase.RECOVERY_REQUIRED
    assert drain.locked


def test_recovery_refuses_to_resume_when_counts_do_not_conserve(tmp_path):
    path = tmp_path / "transaction.json"
    drain = FakeDrainControl()
    first, _journal = controller(path, drain)
    begin_known_batch(first)
    first.verify_deposit(carried_after=592, intake_after=1, pipeline_after=0)
    first.enable_drain()

    restarted, journal = controller(path, drain)
    restarted.startup_recover()
    with pytest.raises(RuntimeError, match="recovery mismatch"):
        restarted.resume_recovery_drain(
            intake_current=0,
            pipeline_current=1,
            destination_current=1,
        )

    assert journal.load().phase is IntakePhase.RECOVERY_REQUIRED
    assert drain.locked


def test_corrupt_journal_still_gets_physically_locked_first(tmp_path):
    path = tmp_path / "transaction.json"
    path.write_text("not-json", encoding="utf-8")
    drain = FakeDrainControl()
    instance, _journal = controller(path, drain)

    with pytest.raises(ValueError):
        instance.startup_recover()

    assert drain.locked
    assert drain.commands[0] == "lock"


def test_competing_lease_owner_cannot_begin_a_transaction(tmp_path):
    drain = FakeDrainControl()
    instance, journal = controller(tmp_path / "transaction.json", drain)
    instance.lease.owner = "other-controller"

    with pytest.raises(RuntimeError, match="leased by another owner"):
        instance.begin(
            "batch-001",
            "minecraft:dirt",
            1,
            COORDINATES,
            carried_before=1,
            intake_before=0,
            destination_before=0,
        )

    assert journal.load() is None
    assert drain.locked
