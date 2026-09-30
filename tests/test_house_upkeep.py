from types import SimpleNamespace
import time

from baritone_client.automator import house_upkeep
from baritone_client.common import base


def _state(custom_data=None):
    return SimpleNamespace(custom_data=custom_data or {})


def _signals(**overrides):
    values = {"health": 20.0, "nearby_hostiles": 0}
    values.update(overrides)
    return SimpleNamespace(**values)


def _house_state(origin=(10, 64, 20), repaired=False):
    house = {"origin": list(origin)}
    if repaired:
        house["repaired"] = True
        house["structure_checked_at"] = time.time()
    return _state({"structures": {"starter_house": house}})


def test_select_returns_none_without_cooldown():
    assert house_upkeep.select_house_upkeep_opportunity(
        _house_state(), _signals(), cooldown_ready=False
    ) is None


def test_select_returns_none_below_health_floor():
    signals = _signals(health=10.0)
    assert house_upkeep.select_house_upkeep_opportunity(
        _house_state(), signals, cooldown_ready=True
    ) is None


def test_repair_waits_for_calm_but_lighting_does_not():
    # House repair keeps its calm-moment gate; lighting is the fix for
    # hostiles, so one or two nearby must not block it.
    offered = house_upkeep.select_house_upkeep_opportunity(
        _house_state(), _signals(nearby_hostiles=1), cooldown_ready=True
    )
    assert offered is not None and "dark spots" in offered.reason
    assert house_upkeep.select_house_upkeep_opportunity(
        _house_state(), _signals(nearby_hostiles=3), cooldown_ready=True
    ) is None


def test_select_returns_none_without_a_persisted_origin():
    assert house_upkeep.select_house_upkeep_opportunity(
        _state(), _signals(), cooldown_ready=True
    ) is None


def test_repaired_house_offers_lighting_only_when_due():
    state = _house_state(repaired=True)
    offered = house_upkeep.select_house_upkeep_opportunity(
        state, _signals(), cooldown_ready=True
    )
    assert offered is not None and "dark spots" in offered.reason

    state.custom_data["base_lighting"] = {"next_check": 4102444800.0}  # far future
    assert house_upkeep.select_house_upkeep_opportunity(
        state, _signals(), cooldown_ready=True
    ) is None


def test_select_offers_upkeep_when_eligible():
    state = _house_state(origin=(10, 64, 20))
    opportunity = house_upkeep.select_house_upkeep_opportunity(
        state, _signals(), cooldown_ready=True
    )
    assert opportunity is not None
    assert opportunity.location == (10, 64, 20)


def test_legacy_repaired_flag_cannot_skip_physical_recheck_forever():
    state = _house_state(repaired=True)
    del state.custom_data["structures"]["starter_house"]["structure_checked_at"]
    offered = house_upkeep.select_house_upkeep_opportunity(state, _signals(), True)
    assert offered is not None and "repair" in offered.reason


def test_repaired_house_is_resurveyed_after_bounded_interval(monkeypatch):
    monkeypatch.setattr(house_upkeep.time, "time", lambda: 10000.0)
    state = _house_state(repaired=True)
    state.custom_data["structures"]["starter_house"]["structure_checked_at"] = (
        10000.0 - house_upkeep.HOUSE_STRUCTURE_RECHECK_INTERVAL
    )
    offered = house_upkeep.select_house_upkeep_opportunity(state, _signals(), True)
    assert offered is not None and "repair" in offered.reason
    house_upkeep._mark_house_repaired(state)
    assert house_upkeep._house_repaired(state)


def test_run_house_upkeep_reports_no_origin():
    success, detail, before, after = house_upkeep.run_house_upkeep(
        SimpleNamespace(), _state()
    )
    assert not success
    assert before == after == 0
    assert "no persisted house origin" in detail


def test_run_house_upkeep_improves_and_marks_incomplete_house_unrepaired(monkeypatch):
    origin = (10, 64, 20)
    state = _house_state(origin=origin)
    client = SimpleNamespace(transport=SimpleNamespace(dispatch=lambda *_a, **_k: {}))

    # Three surveys: one from wherever the bot was standing (which may be an
    # unloaded chunk), then the authoritative baseline taken on arrival, then
    # the result. Only the last two may be compared for a delta.
    progress_calls = iter(
        [
            {"floor": 0, "shell": 0, "roof": 0, "floor_total": 49,
             "shell_total": 70, "roof_total": 49, "door_present": False},
            {"floor": 30, "shell": 40, "roof": 0, "floor_total": 49,
             "shell_total": 70, "roof_total": 49, "door_present": False},
            {"floor": 49, "shell": 68, "roof": 30, "floor_total": 49,
             "shell_total": 70, "roof_total": 49, "door_present": False},
        ]
    )
    monkeypatch.setattr(base, "summarize_house_progress", lambda *_a: next(progress_calls))
    monkeypatch.setattr(base, "build_good_house", lambda *_a: False)
    monkeypatch.setattr(
        "baritone_client.common.navigation.goto", lambda *_a, **_k: True
    )

    success, detail, before, after = house_upkeep.run_house_upkeep(client, state)

    assert success
    assert before == 30 + 40 + 0 + 0
    assert after == 49 + 68 + 30 + 0
    assert not house_upkeep._house_repaired(state)


def test_run_house_upkeep_marks_repaired_once_fully_complete(monkeypatch):
    origin = (10, 64, 20)
    state = _house_state(origin=origin)
    client = SimpleNamespace(transport=SimpleNamespace(dispatch=lambda *_a, **_k: {}))
    complete_progress = {
        "floor": 49, "shell": 70, "roof": 49, "floor_total": 49,
        "shell_total": 70, "roof_total": 49, "door_present": True,
    }
    monkeypatch.setattr(base, "summarize_house_progress", lambda *_a: complete_progress)
    monkeypatch.setattr(base, "build_good_house", lambda *_a: True)
    monkeypatch.setattr(
        "baritone_client.common.navigation.goto", lambda *_a, **_k: True
    )

    success, _detail, before, after = house_upkeep.run_house_upkeep(client, state)

    assert before == after == 49 + 70 + 49 + 1
    assert not success  # no delta this call: it was already complete
    assert house_upkeep._house_repaired(state)


def test_run_house_upkeep_reports_unreachable_site(monkeypatch):
    origin = (10, 64, 20)
    state = _house_state(origin=origin)
    client = SimpleNamespace(transport=SimpleNamespace(dispatch=lambda *_a, **_k: {}))
    monkeypatch.setattr(
        base, "summarize_house_progress",
        lambda *_a: {"floor": 10, "shell": 0, "roof": 0, "floor_total": 49,
                      "shell_total": 70, "roof_total": 49, "door_present": False},
    )
    monkeypatch.setattr(
        "baritone_client.common.navigation.goto", lambda *_a, **_k: False
    )

    success, detail, before, after = house_upkeep.run_house_upkeep(client, state)

    assert not success
    assert before == after == 10
    assert "could not reach" in detail


def test_summarize_house_progress_counts_by_role():
    origin = (10, 64, 20)
    plan = base._good_house_plan(*origin)
    blocks = {}
    for x, y, z, role in plan:
        if role == "floor":
            blocks[(x, y, z)] = "minecraft:cobblestone"
        elif role == "shell":
            blocks[(x, y, z)] = "minecraft:oak_planks"
        # leave roof entirely unbuilt (air)
    door = (origin[0] + 3, origin[1] + 1, origin[2])
    blocks[door] = "minecraft:oak_door"

    class Transport:
        def dispatch(self, command, payload):
            if command == "get_block":
                key = (payload["x"], payload["y"], payload["z"])
                return {"id": blocks.get(key, "minecraft:air"), "state": {}}
            return {}

    client = SimpleNamespace(transport=Transport())
    progress = base.summarize_house_progress(client, *origin)

    assert progress["floor"] == progress["floor_total"] == 49
    assert progress["shell"] == progress["shell_total"] == 70
    assert progress["roof"] == 0
    assert progress["roof_total"] == 49
    assert progress["door_present"] is True


def test_a_house_surveyed_from_an_unloaded_chunk_is_not_reported_repaired(monkeypatch):
    """THE bug: the baseline was measured before walking to the house.

    From across the map every block of the house reads void_air, so the
    "before" survey scored 0 of 169. The house was then found intact on
    arrival and the closing `after > before` test reported a successful
    repair for a building nobody had touched -- inflating upkeep progress
    with work that never happened.
    """
    state = _house_state(origin=(10, 64, 20))
    client = SimpleNamespace(transport=SimpleNamespace(dispatch=lambda *_a, **_k: {}))
    intact = {
        "floor": 49, "shell": 70, "roof": 49, "floor_total": 49,
        "shell_total": 70, "roof_total": 49, "door_present": True,
    }
    unloaded = {
        "floor": 0, "shell": 0, "roof": 0, "floor_total": 49,
        "shell_total": 70, "roof_total": 49, "door_present": False,
    }
    surveys = iter([unloaded, intact, intact])
    monkeypatch.setattr(base, "summarize_house_progress", lambda *_a: next(surveys))
    monkeypatch.setattr(base, "build_good_house", lambda *_a: True)
    monkeypatch.setattr(
        "baritone_client.common.navigation.goto", lambda *_a, **_k: True
    )

    success, _detail, before, after = house_upkeep.run_house_upkeep(client, state)

    assert before == after, (
        f"before={before} after={after}; the baseline came from the unloaded "
        "survey, so an untouched house looks like 169 blocks of repair"
    )
    assert not success, "claimed a repair on a house that was already intact"
