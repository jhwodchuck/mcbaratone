"""Focused tests for the FOOD_AND_IRON durable-food step.

These pin the contract that partial bread and an immature persisted farm are
*bounded progress*, not a phase-bricking task failure.  T1204 remains the only
thing that gates actual completion (16 durable food + a shield), so the bake
step must never return False merely because the farm is still regrowing.
"""
from types import SimpleNamespace

from baritone_client.automator.phases import iron_age


def _handler(state_custom):
    handler = iron_age.FoodAndIronHandler()
    handler.state = SimpleNamespace(custom_data=state_custom)
    return handler


def test_bake_returns_true_on_partial_bread_progress(monkeypatch):
    """12 wheat -> 4 more bread (partial) must return True, not brick the phase."""
    handler = _handler(
        {"structures": {"food_source": {"verified": True, "location": [-429, 79, 2]}}}
    )
    counts = {"minecraft:bread": 11, "minecraft:wheat": 12}
    monkeypatch.setattr(iron_age, "count_item", lambda _c, i: counts.get(i, 0))
    monkeypatch.setattr(
        iron_age, "harvest_wheat_farm", lambda *_a, **_k: True
    )
    baked = []
    monkeypatch.setattr(
        iron_age,
        "ensure_supplies",
        lambda _c, req: baked.append(dict(req)) or SimpleNamespace(success=True),
    )

    assert handler._bake_durable_food(SimpleNamespace()) is True
    # It baked exactly what the 12 wheat could afford (4 loaves) toward 16.
    assert baked == [{"minecraft:bread": 15}]


def test_bake_skips_work_when_target_already_met(monkeypatch):
    handler = _handler({})
    monkeypatch.setattr(
        iron_age, "count_item", lambda _c, i: 16 if i == "minecraft:bread" else 0
    )
    calls = []
    monkeypatch.setattr(iron_age, "harvest_wheat_farm", lambda *_a, **_k: calls.append("harvest") or True)

    assert handler._bake_durable_food(SimpleNamespace()) is True
    assert not calls  # no harvest, no bake -- already at 16


def test_bake_immature_farm_is_bounded_wait_not_failure(monkeypatch):
    """0 wheat + a verified renewable source => True (bounded wait), not False."""
    handler = _handler(
        {"structures": {"food_source": {"verified": True, "location": [-429, 79, 2]}}}
    )
    counts = {"minecraft:bread": 11, "minecraft:wheat": 0}
    monkeypatch.setattr(iron_age, "count_item", lambda _c, i: counts.get(i, 0))
    monkeypatch.setattr(
        iron_age, "harvest_wheat_farm", lambda *_a, **_k: True
    )
    baked = []
    monkeypatch.setattr(
        iron_age,
        "ensure_supplies",
        lambda _c, req: baked.append(req) or SimpleNamespace(success=True),
    )

    assert handler._bake_durable_food(SimpleNamespace()) is True
    assert not baked  # nothing bakeable; must NOT call ensure_supplies


def test_bake_fails_closed_when_no_renewable_source_and_no_wheat(monkeypatch):
    """No verified renewable source AND no wheat => genuine no-avenue => False."""
    handler = _handler({"structures": {}})
    counts = {"minecraft:bread": 0, "minecraft:wheat": 0}
    monkeypatch.setattr(iron_age, "count_item", lambda _c, i: counts.get(i, 0))
    monkeypatch.setattr(
        iron_age, "harvest_wheat_farm", lambda *_a, **_k: True
    )

    assert handler._bake_durable_food(SimpleNamespace()) is False


def test_harvest_uses_farm_location_fallback(monkeypatch):
    """Missing food_source.location falls back to custom_data['farm_location']."""
    handler = _handler(
        {"farm_location": [-429, 79, 2], "structures": {}}
    )
    monkeypatch.setattr(
        iron_age, "count_item", lambda _c, i: 0 if i == "minecraft:wheat" else 0
    )
    harvests = []
    monkeypatch.setattr(
        iron_age,
        "harvest_wheat_farm",
        lambda _c, x, y, z, **kw: harvests.append((x, y, z, kw.get("range_"))) or True,
    )

    handler._harvest_persisted_crop_farm(SimpleNamespace())
    assert harvests == [(-429, 79, 2, 8)]


def test_harvest_reports_false_when_no_location(monkeypatch):
    handler = _handler({"structures": {}})
    monkeypatch.setattr(iron_age, "count_item", lambda _c, _i: 0)
    monkeypatch.setattr(
        iron_age,
        "harvest_wheat_farm",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("must not harvest")),
    )
    assert handler._harvest_persisted_crop_farm(SimpleNamespace()) is False
