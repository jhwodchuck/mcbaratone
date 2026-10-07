from types import SimpleNamespace

import pytest

from baritone_client.common import food_workstation


HOME = (10, 65, 10)


class _Client:
    def __init__(self, position=(80, 72, 80)):
        self.live = {
            "block_position": dict(zip(("x", "y", "z"), position)),
            "health": 20, "is_dead": False, "dimension": "minecraft:overworld",
        }
        self.transport = SimpleNamespace(dispatch=self.dispatch)

    def dispatch(self, route, _payload=None):
        if route == "get_state":
            return dict(self.live)
        raise AssertionError(f"unexpected route {route}")


@pytest.fixture
def state():
    return SimpleNamespace(custom_data={"base_location": list(HOME)})


def _set_home(client):
    client.live["block_position"] = {"x": HOME[0] + 1, "y": HOME[1], "z": HOME[2]}


def _call(client, state, *, survival_ready, return_home, craft):
    return food_workstation.craft_bread_at_saved_home(
        client, state, HOME, 2,
        survival_ready=survival_ready,
        return_home=return_home,
        craft=craft,
    )


def test_reachable_saved_home_uses_only_its_enclosed_table(monkeypatch, state):
    client = _Client()
    returns, crafts, table_checks = [], [], []
    survival_checks = []

    def return_home(_client, anchor):
        returns.append(tuple(anchor))
        _set_home(client)
        return True

    def observed_table(_client):
        table_checks.append(tuple(client.live["block_position"].values()))
        return True, (HOME[0] + 2, HOME[1], HOME[2])

    monkeypatch.setattr(
        "baritone_client.common.enclosed_workstation.sheltered_bread_table",
        observed_table,
    )

    assert _call(
        client, state,
        survival_ready=lambda _c: survival_checks.append(True) or True,
        return_home=return_home,
        craft=lambda _c, item, count: crafts.append((item, count)) or True,
    )
    assert returns == [HOME]
    assert table_checks == [(HOME[0] + 1, HOME[1], HOME[2])]
    assert crafts == [(food_workstation.BREAD, 2)]
    assert len(survival_checks) == 2
    assert client._protected_home_anchor == tuple(float(value) for value in HOME)


def test_blocked_home_travel_preserves_wheat_and_skips_crafting(monkeypatch, state):
    client = _Client()
    wheat = {"count": 7}
    crafts = []
    monkeypatch.setattr(
        "baritone_client.common.enclosed_workstation.sheltered_bread_table",
        lambda _c: pytest.fail("cannot inspect a workstation before reaching home"),
    )

    assert not _call(
        client, state, survival_ready=lambda _c: True,
        return_home=lambda *_a: False,
        craft=lambda *_a: crafts.append(True) or wheat.update(count=0) or True,
    )
    assert wheat["count"] == 7
    assert crafts == []


def test_unknown_fresh_state_fails_before_travel_or_crafting(state):
    client = _Client()
    client.live = {"success": False, "error": "state unavailable", "data": {
        "block_position": {"x": 80, "y": 72, "z": 80}, "health": 20,
        "is_dead": False, "dimension": "minecraft:overworld",
    }}
    returns, crafts = [], []

    assert not _call(
        client, state, survival_ready=lambda _c: True,
        return_home=lambda *a: returns.append(a) or True,
        craft=lambda *a: crafts.append(a) or True,
    )
    assert returns == [] and crafts == []


def test_missing_saved_home_never_invents_a_return_target():
    client = _Client()
    state_without_home = SimpleNamespace(custom_data={})
    returns, crafts = [], []

    assert not food_workstation.craft_bread_at_saved_home(
        client, state_without_home, None, 2,
        survival_ready=lambda _c: True,
        return_home=lambda *a: returns.append(a) or True,
        craft=lambda *a: crafts.append(a) or True,
    )
    assert returns == [] and crafts == []


def test_missing_home_table_fails_without_crafting_or_material_prep(monkeypatch, state):
    client = _Client()
    returns, crafts = [], []

    def return_home(_client, _anchor):
        returns.append(True)
        _set_home(client)
        return True

    monkeypatch.setattr(
        "baritone_client.common.enclosed_workstation.sheltered_bread_table",
        lambda _c: (True, None),
    )
    assert not _call(
        client, state, survival_ready=lambda _c: True,
        return_home=return_home,
        craft=lambda *a: crafts.append(a) or True,
    )
    assert returns == [True]
    assert crafts == []


@pytest.mark.parametrize("failure", ["survival", "height"])
def test_failed_posttravel_survival_or_height_proof_fails_closed(state, failure):
    client = _Client()
    returns, crafts = [], []

    def return_home(_client, _anchor):
        returns.append(True)
        client.live["block_position"] = {
            "x": HOME[0], "y": HOME[1] + (12 if failure == "height" else 0),
            "z": HOME[2],
        }
        return True

    survival_checks = []

    def survival_ready(_client):
        survival_checks.append(True)
        return not (failure == "survival" and len(survival_checks) == 2)

    assert not _call(
        client, state, survival_ready=survival_ready,
        return_home=return_home,
        craft=lambda *a: crafts.append(a) or True,
    )
    assert returns == [True]
    assert len(survival_checks) == 2
    assert crafts == []
