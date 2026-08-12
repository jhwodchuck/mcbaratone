from baritone_client.operations.renewable_reserves import (
    RenewableReservePolicy,
    RenewableSnapshot,
    audit_renewable_reserves,
)


POLICY = RenewableReservePolicy(
    mature_crop_minimums={"minecraft:wheat": 100},
    minimum_food_items=2,
    minimum_source_logs=96,
    minimum_total_logs=128,
    minimum_total_saplings=4,
    minimum_source_saplings={
        "minecraft:oak_sapling": 1,
        "minecraft:birch_sapling": 1,
    },
)


def snapshot(**changes):
    values = {
        "mature_crops": {"minecraft:wheat": 104},
        "food_source": {"minecraft:wheat": 0},
        "food_working": {"minecraft:wheat": 1},
        "food_reserve": {"minecraft:wheat": 1},
        "forestry_source": {
            "minecraft:oak_log": 71,
            "minecraft:birch_log": 40,
            "minecraft:oak_sapling": 1,
            "minecraft:birch_sapling": 2,
        },
        "forestry_working": {"minecraft:oak_log": 16},
        "forestry_reserve": {
            "minecraft:oak_log": 16,
            "minecraft:oak_sapling": 1,
        },
    }
    values.update(changes)
    return RenewableSnapshot(**values)


def test_integrated_snapshot_passes_every_floor():
    audit = audit_renewable_reserves(snapshot(), POLICY, require_integrated=True)

    assert audit.accepted
    assert audit.integrated
    assert audit.totals["total_logs"] == 143
    assert audit.totals["total_saplings"] == 4


def test_pre_delivery_snapshot_can_pass_without_integrated_outputs():
    value = snapshot(
        food_source={"minecraft:wheat": 2},
        food_working={},
        food_reserve={},
        forestry_source={
            "minecraft:oak_log": 103,
            "minecraft:birch_log": 40,
            "minecraft:oak_sapling": 2,
            "minecraft:birch_sapling": 2,
        },
        forestry_working={},
        forestry_reserve={},
    )

    assert audit_renewable_reserves(value, POLICY).accepted
    assert not audit_renewable_reserves(value, POLICY).integrated
    assert not audit_renewable_reserves(
        value, POLICY, require_integrated=True
    ).accepted


def test_mature_crop_floor_fails_closed():
    value = snapshot(mature_crops={"minecraft:wheat": 99})

    audit = audit_renewable_reserves(value, POLICY)
    assert not audit.accepted
    assert "mature minecraft:wheat 99 is below 100" in audit.reasons


def test_food_floor_counts_source_working_and_reserve():
    value = snapshot(food_working={}, food_reserve={})

    assert not audit_renewable_reserves(value, POLICY).accepted


def test_source_log_floor_blocks_excess_delivery():
    value = snapshot(
        forestry_source={
            "minecraft:oak_log": 55,
            "minecraft:birch_log": 40,
            "minecraft:oak_sapling": 1,
            "minecraft:birch_sapling": 2,
        },
        forestry_working={"minecraft:oak_log": 32},
        forestry_reserve={"minecraft:oak_log": 16, "minecraft:oak_sapling": 1},
    )

    audit = audit_renewable_reserves(value, POLICY)
    assert not audit.accepted
    assert any("source logs" in reason for reason in audit.reasons)


def test_total_log_floor_detects_loss_not_merely_distribution():
    value = snapshot(
        forestry_source={
            "minecraft:oak_log": 60,
            "minecraft:birch_log": 40,
            "minecraft:oak_sapling": 1,
            "minecraft:birch_sapling": 2,
        },
        forestry_working={"minecraft:oak_log": 10},
        forestry_reserve={"minecraft:oak_log": 10, "minecraft:oak_sapling": 1},
    )

    audit = audit_renewable_reserves(value, POLICY)
    assert not audit.accepted
    assert any("total logs" in reason for reason in audit.reasons)


def test_each_source_sapling_type_must_survive():
    source = dict(snapshot().forestry_source)
    source["minecraft:oak_sapling"] = 0
    value = snapshot(forestry_source=source)

    audit = audit_renewable_reserves(value, POLICY)
    assert not audit.accepted
    assert any("source minecraft:oak_sapling" in reason for reason in audit.reasons)
