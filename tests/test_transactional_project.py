from pathlib import Path

import pytest

from baritone_client.operations.transactional_project import (
    ProjectJournal,
    ProjectPhase,
    TransactionalProjectController,
)


KIT = {
    "minecraft:cobblestone": 16,
    "minecraft:stick": 8,
    "minecraft:wooden_pickaxe": 1,
}
BASE = {"minecraft:cobblestone": 11, "minecraft:stick": 33}
BLOCKS = tuple((580 + offset, 73, -332) for offset in range(4))


class FakeLease:
    lease_keys = ("project:s11", "worker:Bot16")

    def __init__(self):
        self.owner = None

    def acquire(self, owner):
        if self.owner is not None:
            return False
        self.owner = owner
        return True

    def release(self, owner):
        if self.owner != owner:
            return False
        self.owner = None
        return True


def controller(tmp_path: Path):
    lease = FakeLease()
    value = TransactionalProjectController(ProjectJournal(tmp_path / "project.json"), lease)
    value.begin(
        "s11-test",
        worker="Bot16",
        project_id="calibration-curb",
        locker=(583, 73, -333),
        project_blocks=BLOCKS,
        material="minecraft:cobblestone",
        material_count=4,
        kit=KIT,
        carried_before=BASE,
        locker_before=KIT,
        worker_inventory_hash_before="inventory-before",
        worker_position_before=(536.7, 79, -303.8),
        worker_selected_slot_before=1,
        project_material_blocks=0,
    )
    return value, lease


def test_complete_reversible_project(tmp_path):
    value, lease = controller(tmp_path)
    value.mark_pickup()
    value.verify_pickup(
        carried={"minecraft:cobblestone": 27, "minecraft:stick": 41, "minecraft:wooden_pickaxe": 1},
        locker={},
        project_blocks=0,
    )
    value.mark_build()
    value.verify_built(
        carried={"minecraft:cobblestone": 23, "minecraft:stick": 41, "minecraft:wooden_pickaxe": 1},
        locker={},
        project_blocks=4,
    )
    value.verify_audit(project_blocks=4, auditor="Bot07")
    value.verify_partial_return(
        carried={"minecraft:cobblestone": 23, "minecraft:stick": 33},
        locker={"minecraft:stick": 8, "minecraft:wooden_pickaxe": 1},
        project_blocks=4,
    )
    value.mark_salvage()
    value.verify_salvaged(
        carried={"minecraft:cobblestone": 27, "minecraft:stick": 33},
        locker={"minecraft:stick": 8, "minecraft:wooden_pickaxe": 1},
        project_blocks=0,
    )
    value.mark_return()
    returned = value.verify_return(
        carried=BASE,
        locker=KIT,
        project_blocks=0,
        worker_inventory_hash="inventory-before",
    )
    assert returned.phase is ProjectPhase.RETURNED
    committed = value.commit(worker_inventory_hash="inventory-before")
    assert committed.phase is ProjectPhase.COMMITTED
    assert lease.owner is None
    assert committed.observations["verified_blocks_placed"] == 4
    assert committed.observations["verified_blocks_salvaged"] == 4


@pytest.mark.parametrize(
    ("carried", "locker", "blocks", "phase"),
    [
        (BASE, KIT, 0, ProjectPhase.RESERVED),
        ({"minecraft:cobblestone": 27, "minecraft:stick": 41, "minecraft:wooden_pickaxe": 1}, {}, 0, ProjectPhase.PICKED_UP),
        ({"minecraft:cobblestone": 23, "minecraft:stick": 41, "minecraft:wooden_pickaxe": 1}, {}, 4, ProjectPhase.BUILT),
        ({"minecraft:cobblestone": 23, "minecraft:stick": 33}, {"minecraft:stick": 8, "minecraft:wooden_pickaxe": 1}, 4, ProjectPhase.PARTIAL_RETURN),
        ({"minecraft:cobblestone": 27, "minecraft:stick": 33}, {"minecraft:stick": 8, "minecraft:wooden_pickaxe": 1}, 0, ProjectPhase.SALVAGED),
        (BASE, KIT, 0, ProjectPhase.RETURNED),
    ],
)
def test_fresh_process_reconciles_conserved_boundaries(tmp_path, carried, locker, blocks, phase):
    value, lease = controller(tmp_path)
    transaction = value.journal.load()
    transaction.phase = phase
    value.journal.save(transaction)
    value.startup_recover()
    fresh = TransactionalProjectController(value.journal, lease)
    recovered = fresh.reconcile_recovery(
        carried=carried, locker=locker, project_blocks=blocks
    )
    assert recovered.phase is phase


def test_rejects_worker_as_independent_auditor(tmp_path):
    value, _lease = controller(tmp_path)
    transaction = value.journal.load()
    transaction.phase = ProjectPhase.BUILT
    value.journal.save(transaction)
    rejected = value.verify_audit(project_blocks=4, auditor="Bot16")
    assert rejected.phase is ProjectPhase.RECOVERY_REQUIRED


def test_conservation_mismatch_enters_recovery(tmp_path):
    value, _lease = controller(tmp_path)
    value.mark_pickup()
    with pytest.raises(RuntimeError, match="pickup conservation mismatch"):
        value.verify_pickup(carried=BASE, locker={}, project_blocks=0)
    assert value.journal.load().phase is ProjectPhase.RECOVERY_REQUIRED


def test_active_transaction_and_competing_lease_are_rejected(tmp_path):
    value, lease = controller(tmp_path)
    assert not lease.acquire("competitor")
    with pytest.raises(RuntimeError, match="active project transaction"):
        value.begin(
            "second",
            worker="Bot16",
            project_id="other",
            locker=(583, 73, -333),
            project_blocks=BLOCKS,
            material="minecraft:cobblestone",
            material_count=4,
            kit=KIT,
            carried_before=BASE,
            locker_before=KIT,
            worker_inventory_hash_before="inventory-before",
            worker_position_before=(0, 0, 0),
            worker_selected_slot_before=1,
            project_material_blocks=0,
        )
