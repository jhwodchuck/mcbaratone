from pathlib import Path

import pytest

from baritone_client.operations.transactional_provisioning import (
    ProvisioningCoordinates,
    ProvisioningJournal,
    ProvisioningPhase,
    TransactionalProvisioningController,
)


KIT = {
    "minecraft:cobblestone": 16,
    "minecraft:stick": 8,
    "minecraft:wooden_pickaxe": 1,
}
SOURCES = {
    "minecraft:cobblestone": (580, 79, -305),
    "minecraft:stick": (587, 76, -332),
    "minecraft:wooden_pickaxe": (573, 79, -305),
}
COORDINATES = ProvisioningCoordinates(
    sources=SOURCES,
    outbound=(583, 73, -335),
    returns=(583, 73, -333),
)
SOURCE_BEFORE = {
    "minecraft:cobblestone": 960,
    "minecraft:stick": 8,
    "minecraft:wooden_pickaxe": 3,
}
CARRIED_BEFORE = {
    "minecraft:cobblestone": 11,
    "minecraft:stick": 33,
    "minecraft:wooden_pickaxe": 0,
}
SOURCE_AFTER = {
    "minecraft:cobblestone": 944,
    "minecraft:stick": 0,
    "minecraft:wooden_pickaxe": 2,
}
CARRIED_WITH_KIT = {
    "minecraft:cobblestone": 27,
    "minecraft:stick": 41,
    "minecraft:wooden_pickaxe": 1,
}


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
    journal = ProvisioningJournal(tmp_path / "provisioning.json")
    lease = Lease()
    return TransactionalProvisioningController(journal, lease), journal, lease


def begin(value):
    return value.begin(
        "kit-001",
        worker="Bot16",
        profile="candidate_a_builder_resupply_v1",
        kit=KIT,
        coordinates=COORDINATES,
        source_before=SOURCE_BEFORE,
        carried_before=CARRIED_BEFORE,
        outbound_before={},
        return_before={},
        worker_inventory_hash_before="baseline",
    )


def stage(value):
    begin(value)
    return value.verify_staged(
        sources=SOURCE_AFTER,
        carried=CARRIED_BEFORE,
        outbound=KIT,
        returns={},
    )


def pickup(value):
    stage(value)
    value.mark_pickup()
    return value.verify_pickup(
        sources=SOURCE_AFTER,
        carried=CARRIED_WITH_KIT,
        outbound={},
        returns={},
    )


def returned(value):
    pickup(value)
    value.mark_return()
    return value.verify_return(
        sources=SOURCE_AFTER,
        carried=CARRIED_BEFORE,
        outbound={},
        returns=KIT,
    )


@pytest.mark.parametrize(
    ("physical", "phase"),
    [
        (
            (SOURCE_AFTER, CARRIED_BEFORE, KIT, {}),
            ProvisioningPhase.OUTBOUND_STAGED,
        ),
        (
            (SOURCE_AFTER, CARRIED_WITH_KIT, {}, {}),
            ProvisioningPhase.PICKED_UP,
        ),
        (
            (SOURCE_AFTER, CARRIED_BEFORE, {}, KIT),
            ProvisioningPhase.RETURNED,
        ),
    ],
)
def test_restart_recovers_each_physical_boundary(tmp_path, physical, phase):
    value, journal, lease = controller(tmp_path)
    if phase is ProvisioningPhase.OUTBOUND_STAGED:
        stage(value)
    elif phase is ProvisioningPhase.PICKED_UP:
        pickup(value)
    else:
        returned(value)
    restarted = TransactionalProvisioningController(journal, lease)
    restarted.startup_recover()
    result = restarted.reconcile_recovery(
        sources=physical[0],
        carried=physical[1],
        outbound=physical[2],
        returns=physical[3],
    )
    assert result.phase is phase


def test_commit_requires_exact_worker_inventory_hash(tmp_path):
    value, journal, lease = controller(tmp_path)
    returned(value)
    with pytest.raises(RuntimeError, match="inventory hash"):
        value.commit(worker_inventory_hash_current="changed")
    assert journal.load().phase is ProvisioningPhase.RECOVERY_REQUIRED
    assert lease.owner == "kit-001"


def test_complete_return_commits_and_releases_lease(tmp_path):
    value, journal, lease = controller(tmp_path)
    returned(value)
    result = value.commit(worker_inventory_hash_current="baseline")
    assert result.phase is ProvisioningPhase.COMMITTED
    assert journal.load().phase is ProvisioningPhase.COMMITTED
    assert lease.owner is None


def test_ambiguous_recovery_stays_fail_closed(tmp_path):
    value, journal, lease = controller(tmp_path)
    stage(value)
    value.startup_recover()
    with pytest.raises(RuntimeError, match="ambiguous"):
        value.reconcile_recovery(
            sources=SOURCE_AFTER,
            carried=CARRIED_BEFORE,
            outbound={"minecraft:cobblestone": 15},
            returns={},
        )
    assert journal.load().phase is ProvisioningPhase.RECOVERY_REQUIRED


def test_active_reservation_excludes_competing_owner(tmp_path):
    value, _journal, lease = controller(tmp_path)
    begin(value)
    assert lease.acquire("competitor") is False
