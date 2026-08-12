from pathlib import Path

import pytest

from baritone_client.operations.transactional_refinery import (
    RefineryCoordinates,
    RefineryJournal,
    RefineryPhase,
    TransactionalRefineryController,
)


COORDINATES = RefineryCoordinates(
    raw_source=(583, 74, -335),
    fuel_source=(573, 79, -302),
    raw_staging=(589, 76, -333),
    fuel_staging=(589, 76, -332),
    furnace=(587, 76, -333),
    output=(589, 76, -330),
)


class Lease:
    def __init__(self):
        self.owner = None

    def acquire(self, owner):
        if self.owner not in (None, owner):
            return False
        self.owner = owner
        return True

    def release(self, owner):
        if self.owner != owner:
            return False
        self.owner = None
        return True


def controller(tmp_path: Path):
    journal = RefineryJournal(tmp_path / "refinery.json")
    lease = Lease()
    return TransactionalRefineryController(journal, lease), journal, lease


def begin(value):
    return value.begin(
        "iron-001",
        COORDINATES,
        input_item="minecraft:raw_iron",
        output_item="minecraft:iron_ingot",
        batch_count=5,
        fuel_item="minecraft:coal",
        fuel_count=1,
        raw_source_before=64,
        fuel_source_before=110,
        output_before=0,
    )


def stage(value):
    begin(value)
    return value.verify_staged(
        raw_source_current=59,
        fuel_source_current=109,
        raw_staging=5,
        fuel_staging=1,
        furnace_input=0,
        furnace_fuel=0,
        furnace_output=0,
        output_current=0,
    )


def test_staged_restart_recovers_exact_boundary(tmp_path):
    value, journal, lease = controller(tmp_path)
    stage(value)
    restarted = TransactionalRefineryController(journal, lease)
    restarted.startup_recover()
    result = restarted.reconcile_recovery(
        raw_source_current=59,
        fuel_source_current=109,
        raw_staging=5,
        fuel_staging=1,
        furnace_input=0,
        furnace_fuel=0,
        furnace_output=0,
        output_current=0,
        lit_time_remaining=0,
    )
    assert result.phase is RefineryPhase.STAGED


def test_running_restart_accepts_partial_conserved_progress(tmp_path):
    value, journal, lease = controller(tmp_path)
    stage(value)
    value.mark_running()
    restarted = TransactionalRefineryController(journal, lease)
    restarted.startup_recover()
    result = restarted.reconcile_recovery(
        raw_source_current=59,
        fuel_source_current=109,
        raw_staging=0,
        fuel_staging=0,
        furnace_input=3,
        furnace_fuel=0,
        furnace_output=2,
        output_current=0,
        lit_time_remaining=1100,
    )
    assert result.phase is RefineryPhase.RUNNING


def test_completed_restart_commits_after_output_transfer(tmp_path):
    value, journal, lease = controller(tmp_path)
    stage(value)
    value.mark_running()
    restarted = TransactionalRefineryController(journal, lease)
    restarted.startup_recover()
    assert restarted.reconcile_recovery(
        raw_source_current=59,
        fuel_source_current=109,
        raw_staging=0,
        fuel_staging=0,
        furnace_input=0,
        furnace_fuel=0,
        furnace_output=5,
        output_current=0,
        lit_time_remaining=590,
    ).phase is RefineryPhase.OUTPUT_VERIFICATION
    result = restarted.reconcile_and_commit(
        raw_source_current=59,
        fuel_source_current=109,
        raw_staging=0,
        fuel_staging=0,
        furnace_input=0,
        furnace_output=0,
        output_after=5,
    )
    assert result.phase is RefineryPhase.COMMITTED
    assert lease.owner is None


def test_recovery_refuses_a_missing_ingot(tmp_path):
    value, journal, lease = controller(tmp_path)
    stage(value)
    value.mark_running()
    value.startup_recover()
    with pytest.raises(RuntimeError, match="recovery mismatch"):
        value.reconcile_recovery(
            raw_source_current=59,
            fuel_source_current=109,
            raw_staging=0,
            fuel_staging=0,
            furnace_input=2,
            furnace_fuel=0,
            furnace_output=2,
            output_current=0,
            lit_time_remaining=900,
        )
    assert journal.load().phase is RefineryPhase.RECOVERY_REQUIRED
    assert lease.owner == "iron-001"


def test_commit_refuses_output_that_remains_in_furnace(tmp_path):
    value, journal, lease = controller(tmp_path)
    stage(value)
    value.mark_running()
    value.startup_recover()
    with pytest.raises(RuntimeError, match="commit mismatch"):
        value.reconcile_and_commit(
            raw_source_current=59,
            fuel_source_current=109,
            raw_staging=0,
            fuel_staging=0,
            furnace_input=0,
            furnace_output=1,
            output_after=4,
        )
    assert journal.load().phase is RefineryPhase.RECOVERY_REQUIRED
