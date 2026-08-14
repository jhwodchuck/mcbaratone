"""Focused contracts for renewable FOOD_AND_IRON provisioning."""

from types import SimpleNamespace

import pytest

from baritone_client.automator.phases import iron_age
from baritone_client.common.tasks import (
    IncrementalProgressRequired,
    PacingHoldRequired,
)


def _handler(custom_data):
    handler = iron_age.FoodAndIronHandler()
    handler.state = SimpleNamespace(custom_data=custom_data)
    return handler


provisioning = iron_age.iron_age_provisioning


def test_partial_bread_is_incremental_progress(monkeypatch):
    handler = _handler(
        {"structures": {"food_source": {
            "verified": True,
            "location": [-429, 79, 2],
        }}}
    )
    counts = {"minecraft:bread": 11, "minecraft:wheat": 12}
    monkeypatch.setattr(provisioning, "count_item", lambda _client, item: counts.get(item, 0))
    monkeypatch.setattr(provisioning, "harvest_persisted_crop_farm", lambda *_args: False)
    requested = []
    monkeypatch.setattr(
        provisioning,
        "ensure_supplies",
        lambda _client, items: requested.append(items) or SimpleNamespace(success=True),
    )

    with pytest.raises(IncrementalProgressRequired, match="prepared 4 bread"):
        handler._bake_durable_food(SimpleNamespace())
    assert requested == [{"minecraft:bread": 15}]


def test_immature_verified_farm_is_pacing_hold(monkeypatch):
    handler = _handler(
        {"structures": {"food_source": {
            "verified": True,
            "location": [-429, 79, 2],
        }}}
    )
    counts = {"minecraft:bread": 11, "minecraft:wheat": 0}
    monkeypatch.setattr(provisioning, "count_item", lambda _client, item: counts.get(item, 0))
    monkeypatch.setattr(provisioning, "harvest_persisted_crop_farm", lambda *_args: False)

    with pytest.raises(PacingHoldRequired, match="renewable crop maturation"):
        handler._bake_durable_food(SimpleNamespace())


def test_other_durable_food_reduces_bread_requirement(monkeypatch):
    handler = _handler({})
    counts = {
        "minecraft:bread": 11,
        "minecraft:cooked_beef": 4,
        "minecraft:wheat": 3,
    }
    monkeypatch.setattr(provisioning, "count_item", lambda _client, item: counts.get(item, 0))
    monkeypatch.setattr(provisioning, "harvest_persisted_crop_farm", lambda *_args: False)
    requested = []
    monkeypatch.setattr(
        provisioning,
        "ensure_supplies",
        lambda _client, items: requested.append(items) or SimpleNamespace(success=True),
    )

    assert handler._bake_durable_food(SimpleNamespace()) is True
    assert requested == [{"minecraft:bread": 12}]


def test_no_renewable_source_and_no_wheat_fails_closed(monkeypatch):
    handler = _handler({"structures": {}})
    monkeypatch.setattr(provisioning, "count_item", lambda _client, _item: 0)
    monkeypatch.setattr(provisioning, "harvest_persisted_crop_farm", lambda *_args: False)

    assert handler._bake_durable_food(SimpleNamespace()) is False


def test_crafting_failure_fails_closed(monkeypatch):
    handler = _handler({})
    counts = {"minecraft:bread": 11, "minecraft:wheat": 15}
    monkeypatch.setattr(provisioning, "count_item", lambda _client, item: counts.get(item, 0))
    monkeypatch.setattr(
        provisioning,
        "ensure_supplies",
        lambda _client, _items: SimpleNamespace(success=False),
    )

    assert handler._bake_durable_food(SimpleNamespace()) is False


def test_missing_supply_chest_gathers_wood_and_retries(monkeypatch):
    handler = _handler({"structures": {}})
    handler._IRON_BANK_TARGET = 30
    restores = []
    handler._reestablish_supply_chest = (
        lambda _client, position: restores.append(position) or len(restores) > 1
    )
    handler._normalize_position = lambda _value: None
    withdrawals = []
    monkeypatch.setattr(
        provisioning,
        "withdraw_required_from_chest",
        lambda _client, position, _required, **_kwargs: (
            withdrawals.append(position) or (-1 if len(withdrawals) == 1 else 1)
        ),
    )
    monkeypatch.setattr(provisioning, "count_item", lambda _client, _item: 0)
    gathered = []
    monkeypatch.setattr(
        provisioning,
        "gather_wood",
        lambda _client, **kwargs: gathered.append(kwargs) or True,
    )
    monkeypatch.setattr(provisioning, "_ensure_raw_planks", lambda *_args: True)
    state = SimpleNamespace(custom_data={"structures": {}})

    assert provisioning.withdraw_banked_iron(
        handler,
        SimpleNamespace(),
        state,
        (1, 2, 3),
    )
    assert gathered == [{"count": 3, "timeout": 180}]
    assert withdrawals == [(1, 2, 3), (1, 2, 3)]


def test_shield_uses_plank_family_helper_and_fails_closed(monkeypatch):
    monkeypatch.setattr(provisioning, "count_item", lambda _client, _item: 0)
    ensured = []
    monkeypatch.setattr(
        provisioning,
        "_ensure_raw_planks",
        lambda _client, count: ensured.append(count) or True,
    )
    monkeypatch.setattr(
        provisioning,
        "ensure_supplies",
        lambda _client, items: SimpleNamespace(success=False, items=items),
    )

    assert provisioning.craft_shield(SimpleNamespace()) is False
    assert ensured == [6]
