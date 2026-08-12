from pathlib import Path

import pytest

from baritone_client.operations.transactional_factory import (
    CatalogFactoryLease,
    FactoryCoordinates,
    FactoryJournal,
    FactoryPhase,
    FixedRecipe,
    TransactionalFactoryController,
)


COORDINATES = FactoryCoordinates(
    staging=(583, 73, -329),
    crafter=(581, 73, -329),
    output=(581, 73, -330),
    flush=(583, 73, -327),
    control=(582, 73, -329),
)
RECIPE = FixedRecipe(
    recipe_id="minecraft:stick",
    slots={
        1: ("minecraft:oak_planks", 1),
        4: ("minecraft:oak_planks", 1),
    },
    output_item="minecraft:stick",
    output_count=4,
)


class FakeFactoryControl:
    def __init__(self) -> None:
        self.powered = False
        self.commands: list[str] = []

    def lock(self) -> None:
        self.commands.append("lock")
        self.powered = False

    def energize(self) -> None:
        self.commands.append("energize")
        self.powered = True

    def pulse(self) -> None:
        self.commands.append("pulse")
        self.powered = False


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


def make_controller(
    path: Path, control: FakeFactoryControl, lease: FakeLease | None = None
):
    journal = FactoryJournal(path)
    factory_lease = lease or FakeLease()
    return (
        TransactionalFactoryController(
            journal,
            control,
            factory_lease,
        ),
        journal,
        factory_lease,
    )


def prepare(controller: TransactionalFactoryController):
    controller.begin(
        "sticks-001",
        RECIPE,
        COORDINATES,
        output_before=8,
        flush_before=0,
    )


def stage(controller: TransactionalFactoryController):
    prepare(controller)
    return controller.verify_staged(
        input_slots=RECIPE.slots,
        output_current=8,
        flush_current=0,
    )


def test_one_pulse_commits_exact_output_and_releases_lease(tmp_path):
    control = FakeFactoryControl()
    controller, journal, lease = make_controller(tmp_path / "factory.json", control)
    stage(controller)

    assert controller.run_once().phase is FactoryPhase.OUTPUT_VERIFICATION
    result = controller.reconcile_and_commit(
        input_slots={}, output_after=12, flush_after=0
    )

    assert result.phase is FactoryPhase.COMMITTED
    assert journal.load().counts["output_after"] == 12
    assert lease.owner is None
    assert not control.powered


def test_wrong_grid_fails_closed_without_a_pulse(tmp_path):
    control = FakeFactoryControl()
    controller, journal, _lease = make_controller(tmp_path / "factory.json", control)
    prepare(controller)

    with pytest.raises(RuntimeError, match="staging mismatch"):
        controller.verify_staged(
            input_slots={
                1: ("minecraft:oak_planks", 1),
                4: ("minecraft:dirt", 1),
            },
            output_current=8,
            flush_current=0,
        )

    assert journal.load().phase is FactoryPhase.RECOVERY_REQUIRED
    assert not control.powered
    assert "pulse" not in control.commands


def test_rejected_grid_can_abort_only_after_exact_flush(tmp_path):
    control = FakeFactoryControl()
    controller, journal, lease = make_controller(tmp_path / "factory.json", control)
    prepare(controller)
    rejected = {
        1: ("minecraft:oak_planks", 1),
        4: ("minecraft:dirt", 1),
    }
    with pytest.raises(RuntimeError):
        controller.verify_staged(
            input_slots=rejected, output_current=8, flush_current=0
        )

    result = controller.abort_after_flush(
        rejected_slots=rejected,
        input_after={},
        output_current=8,
        flush_items_before={},
        flush_items_after={"minecraft:oak_planks": 1, "minecraft:dirt": 1},
    )

    assert result.phase is FactoryPhase.ABORTED
    assert journal.load().phase is FactoryPhase.ABORTED
    assert lease.owner is None


def test_flush_refuses_to_hide_a_missing_item(tmp_path):
    control = FakeFactoryControl()
    controller, journal, lease = make_controller(tmp_path / "factory.json", control)
    prepare(controller)
    rejected = {
        1: ("minecraft:oak_planks", 1),
        4: ("minecraft:dirt", 1),
    }
    with pytest.raises(RuntimeError):
        controller.verify_staged(
            input_slots=rejected, output_current=8, flush_current=0
        )

    with pytest.raises(RuntimeError, match="flush mismatch"):
        controller.abort_after_flush(
            rejected_slots=rejected,
            input_after={},
            output_current=8,
            flush_items_before={},
            flush_items_after={"minecraft:oak_planks": 1},
        )

    assert journal.load().phase is FactoryPhase.RECOVERY_REQUIRED
    assert lease.owner == "sticks-001"


def test_startup_removes_power_before_reading_corrupt_journal(tmp_path):
    path = tmp_path / "factory.json"
    path.write_text("not-json", encoding="utf-8")
    control = FakeFactoryControl()
    control.powered = True
    controller, _journal, _lease = make_controller(path, control)

    with pytest.raises(ValueError):
        controller.startup_recover()

    assert not control.powered
    assert control.commands[0] == "lock"


def test_restart_from_staged_reconstructs_staged_boundary(tmp_path):
    path = tmp_path / "factory.json"
    control = FakeFactoryControl()
    first, journal, lease = make_controller(path, control)
    stage(first)

    restarted = TransactionalFactoryController(
        journal, control, lease
    )
    assert restarted.startup_recover().phase is FactoryPhase.RECOVERY_REQUIRED
    result = restarted.reconcile_recovery(
        input_slots=RECIPE.slots, output_current=8, flush_current=0
    )

    assert result.phase is FactoryPhase.STAGED
    assert not control.powered


def test_restart_from_running_reconstructs_completed_output(tmp_path):
    path = tmp_path / "factory.json"
    control = FakeFactoryControl()
    first, journal, lease = make_controller(path, control)
    stage(first)
    transaction = journal.load()
    transaction.phase = FactoryPhase.RUNNING
    journal.save(transaction)
    control.powered = True

    restarted = TransactionalFactoryController(
        journal, control, lease
    )
    restarted.startup_recover()
    result = restarted.reconcile_recovery(
        input_slots={}, output_current=12, flush_current=0
    )

    assert result.phase is FactoryPhase.OUTPUT_VERIFICATION
    assert not control.powered


def test_intentional_powered_interruption_persists_running(tmp_path):
    control = FakeFactoryControl()
    controller, journal, lease = make_controller(tmp_path / "factory.json", control)
    stage(controller)

    result = controller.interrupt_while_powered()

    assert result.phase is FactoryPhase.RUNNING
    assert journal.load().phase is FactoryPhase.RUNNING
    assert lease.owner == "sticks-001"
    assert control.powered


def test_recovery_refuses_partial_or_duplicate_output(tmp_path):
    path = tmp_path / "factory.json"
    control = FakeFactoryControl()
    first, journal, lease = make_controller(path, control)
    stage(first)
    restarted = TransactionalFactoryController(
        journal, control, lease
    )
    restarted.startup_recover()

    with pytest.raises(RuntimeError, match="recovery mismatch"):
        restarted.reconcile_recovery(
            input_slots={}, output_current=16, flush_current=0
        )

    assert journal.load().phase is FactoryPhase.RECOVERY_REQUIRED
    assert lease.owner == "sticks-001"


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


def test_catalog_factory_lease_rolls_back_partial_acquisition():
    catalog = Catalog()
    catalog.blocked.add("output")
    lease = CatalogFactoryLease(catalog, ("cell", "output", "source"))

    assert not lease.acquire("sticks-001")
    assert catalog.owners == {}
