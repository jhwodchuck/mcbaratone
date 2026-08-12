from types import SimpleNamespace

from baritone_client.common import emergency_food as emergency_food_mod
from baritone_client.common import inventory as inventory_mod
from baritone_client.common import resources as resources_mod
from baritone_client.operations import survival_provisioning as provisioning_mod


def test_carried_wheat_fills_partial_emergency_reserve(monkeypatch):
    counts = {
        "minecraft:rotten_flesh": 6,
        "minecraft:wheat": 6,
        "minecraft:bread": 0,
    }

    monkeypatch.setattr(
        inventory_mod,
        "count_item",
        lambda _client, item_id: counts.get(item_id, 0),
    )

    def craft(_client, item_id, target):
        assert item_id == "minecraft:bread"
        counts[item_id] = target
        counts["minecraft:wheat"] -= target * 3
        return True

    monkeypatch.setattr(resources_mod, "_craft_with_table", craft)
    closed = []
    client = SimpleNamespace(
        transport=SimpleNamespace(
            dispatch=lambda route, payload: closed.append((route, payload)) or {}
        )
    )

    assert emergency_food_mod.craft_emergency_bread_from_carried_wheat(
        client,
        maximum_bread=8,
        minimum_reserve=8,
    ) is True
    assert counts["minecraft:bread"] == 2
    assert counts["minecraft:wheat"] == 0
    assert closed == [("close_screen", {})]


def test_storage_food_provisioning_converts_wheat_after_direct_food_shortfall(
    monkeypatch,
):
    counts = {
        "minecraft:rotten_flesh": 0,
        "minecraft:wheat": 0,
        "minecraft:bread": 0,
    }
    withdrawals = []

    monkeypatch.setattr(
        provisioning_mod,
        "count_item",
        lambda _client, item_id: counts.get(item_id, 0),
    )
    monkeypatch.setattr(
        provisioning_mod,
        "emergency_food_count",
        lambda _client: counts["minecraft:rotten_flesh"]
        + counts["minecraft:bread"],
    )
    monkeypatch.setattr(
        provisioning_mod.harness_ops,
        "move_near",
        lambda *_args, **_kwargs: True,
    )

    def withdraw(_client, chest, accepted, *, target_total, **_kwargs):
        withdrawals.append((chest, tuple(accepted), target_total))
        if "minecraft:wheat" in accepted:
            counts["minecraft:wheat"] = target_total
        elif chest == (1, 2, 3):
            counts["minecraft:rotten_flesh"] = 6
        return 1

    monkeypatch.setattr(provisioning_mod, "withdraw_bounded_food", withdraw)

    def craft(_client, *, maximum_bread, minimum_reserve):
        assert maximum_bread == 2
        assert minimum_reserve == 8
        counts["minecraft:bread"] = 2
        return True

    monkeypatch.setattr(
        provisioning_mod,
        "craft_emergency_bread_from_carried_wheat",
        craft,
    )

    client = SimpleNamespace(
        transport=SimpleNamespace(
            dispatch=lambda route, _payload: {"id": "minecraft:crafting_table"}
            if route == "get_block"
            else {}
        )
    )
    assert provisioning_mod.provision_food_reserve(
        client,
        storage_sources=((1, 2, 3), (4, 5, 6)),
        minimum_reserve=8,
        crafting_table=(7, 8, 9),
    ) == (True, "food reserve crafted from wheat (8/8)")
    assert withdrawals
    assert "minecraft:rotten_flesh" in withdrawals[0][1]
    assert "minecraft:golden_apple" not in withdrawals[0][1]


def test_storage_food_provisioning_accepts_ready_food_without_crafting(monkeypatch):
    reserve = {"count": 0}
    monkeypatch.setattr(
        provisioning_mod,
        "emergency_food_count",
        lambda _client: reserve["count"],
    )
    monkeypatch.setattr(
        provisioning_mod.harness_ops,
        "move_near",
        lambda *_args, **_kwargs: True,
    )

    def withdraw(*_args, **_kwargs):
        reserve["count"] = 12
        return 1

    monkeypatch.setattr(provisioning_mod, "withdraw_bounded_food", withdraw)

    assert provisioning_mod.provision_food_reserve(
        SimpleNamespace(),
        storage_sources=((1, 2, 3),),
        minimum_reserve=8,
    ) == (True, "food reserve withdrawn (12/8)")


def test_survival_material_requirements_use_one_stick_per_torch_batch():
    assert provisioning_mod._survival_material_requirements(9, 13) == {
        "minecraft:raw_iron": 9,
        "minecraft:coal": 6,
        "minecraft:stick": 4,
    }


def test_raw_crops_are_available_as_last_resort_emergency_food():
    for item_id in (
        "minecraft:potato",
        "minecraft:carrot",
        "minecraft:beetroot",
    ):
        assert item_id in emergency_food_mod.EMERGENCY_FOOD_ITEMS
