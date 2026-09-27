"""The home base becomes the respawn point through a verified bed."""

from types import SimpleNamespace

import pytest

from baritone_client.automator import objective_survival
from baritone_client.common import home_respawn


BASE = [200, 70, -40]
HOUSE = [200, 70, -40]


def _state(**extra):
    custom = {
        "base_location": list(BASE),
        "structures": {"starter_house": {"origin": list(HOUSE), "bed": [-1629, 63, -89]}},
    }
    custom.update(extra)
    return SimpleNamespace(custom_data=custom)


class _Client:
    def __init__(self, *, position=(202, 71, -38), chat=("Respawn point set",), dead=False):
        self.position = position
        self.chat = list(chat)
        self.dead = dead
        self.blocks = {}
        self.routes = []
        self.transport = SimpleNamespace(dispatch=self.dispatch)

    def dispatch(self, route, payload):
        self.routes.append(route)
        if route == "get_state":
            x, y, z = self.position
            return {
                "is_dead": self.dead,
                "dimension": "minecraft:overworld",
                "block_position": {"x": x, "y": y, "z": z},
            }
        if route == "get_block":
            return {"id": self.blocks.get((payload["x"], payload["y"], payload["z"]), "minecraft:air")}
        if route == "get_events":
            if payload.get("after_seq") == 0:
                return {"events": [], "latest_seq": 10}
            return {
                "events": [
                    {"event_seq": 11 + i, "type": "chat", "data": {"message": text}}
                    for i, text in enumerate(self.chat)
                ],
                "latest_seq": 10 + len(self.chat),
            }
        if route == "interact_block":
            return {"accepted": True}
        return {}


@pytest.fixture
def home(monkeypatch):
    items = {"minecraft:oak_planks": 5}
    calls = []
    monkeypatch.setattr(home_respawn.time, "sleep", lambda _s: None)
    monkeypatch.setattr(
        "baritone_client.common.combat.scan_for_threats", lambda *_a, **_k: []
    )
    monkeypatch.setattr(
        "baritone_client.common.inventory.count_item",
        lambda _client, item: items.get(item, 0),
    )
    stock = {"minecraft:white_wool": 2, "minecraft:string": 14}
    far = {}
    trips = []

    def withdraw(_client, requirements, **kwargs):
        calls.append(
            (
                "withdraw",
                dict(requirements),
                kwargs.get("max_travel_distance"),
                kwargs.get("allow_recovery_access"),
            )
        )
        source = stock if kwargs.get("allow_recovery_access") else far
        for item, wanted in requirements.items():
            take = min(source.get(item, 0), max(0, wanted - items.get(item, 0)))
            source[item] = source.get(item, 0) - take
            items[item] = items.get(item, 0) + take
        return 1

    monkeypatch.setattr(
        "baritone_client.common.inventory.withdraw_required_from_catalog", withdraw
    )
    from baritone_client.common import harness_ops

    monkeypatch.setattr(harness_ops, "ensure_crafting_table_open", lambda _c: True)
    monkeypatch.setattr(harness_ops, "count_any_planks", lambda _c: items.get("minecraft:oak_planks", 0))
    monkeypatch.setattr(
        harness_ops,
        "move_near",
        lambda _client, x, y, z, **_k: calls.append(("move_near", (x, y, z))) or True,
    )
    chests = {
        "minecraft:white_wool": [(200, 71, -30)],
        "minecraft:string": [(600, 70, 30), (250, 20, -40), (202, 71, -39), (300, 72, 60)],
    }

    class _Catalog:
        def find_item(self, item):
            return [
                {"dimension": "minecraft:overworld", "x": x, "y": y, "z": z, "count": 9}
                for x, y, z in chests.get(item, [])
            ]

    monkeypatch.setattr(
        "baritone_client.common.storage_catalog.catalog_for", lambda *_a, **_k: _Catalog()
    )

    def craft_recipe(_client, result, placements, crafts=1, output_per_recipe=1):
        calls.append(("craft", result, list(placements), crafts))
        items["minecraft:string"] -= 4 * crafts
        items[result] = items.get(result, 0) + crafts
        return True

    def craft_bed(_client, bed_id):
        calls.append(("craft_bed", bed_id))
        items["minecraft:white_wool"] -= 3
        items["minecraft:oak_planks"] -= 3
        items[bed_id] = 1
        return True

    monkeypatch.setattr(harness_ops, "craft_recipe_manual", craft_recipe)
    monkeypatch.setattr(harness_ops, "craft_bed_manual", craft_bed)
    placed = []
    world = {"client": None}

    def place(_client, x, y, z, block, allow_break=True):
        calls.append(("place", (x, y, z), block))
        placed.append((x, y, z))
        world["client"].blocks[(x, y, z)] = block
        items[block] -= 1
        return True

    monkeypatch.setattr(harness_ops, "place_block_exact", place)
    monkeypatch.setattr(
        "baritone_client.common.navigation.find_nearby_block",
        lambda _client, _blocks, radius=50: placed[-1] if placed else None,
    )
    monkeypatch.setattr(
        "baritone_client.common.navigation.goto",
        lambda _client, x, y, z, **_k: trips.append((x, y, z)) or True,
    )
    return SimpleNamespace(
        items=items, calls=calls, stock=stock, far=far, world=world, trips=trips,
        chests=chests,
    )


def test_bed_is_crafted_from_home_wool_and_string_placed_used_and_recorded(home):
    client = _Client()
    home.world["client"] = client
    state = _state()

    assert home_respawn.secure_home_respawn(client, state, now=0.0)

    withdrawals = [call for call in home.calls if call[0] == "withdraw"]
    # A wounded player may only open storage within reach: walk beside each
    # home chest first, then withdraw from there. Distant chests are ignored.
    assert all(call[2] == 6.0 and call[3] is True for call in withdrawals)
    # String from the house supply chest covers all three wool, so the chest
    # by the farm is never opened; the distant chest is never considered.
    assert [call[1] for call in withdrawals] == [{"minecraft:string": 12}]
    moves = [call[1] for call in home.calls if call[0] == "move_near"]
    assert moves[0] == (202, 71, -39)
    assert (200, 71, -30) not in moves and (600, 70, 30) not in moves
    craft = next(call for call in home.calls if call[0] == "craft")
    assert craft[1] == "minecraft:white_wool" and craft[3] == 3
    assert [slot for _item, slot in craft[2]] == [1, 2, 4, 5]
    assert ("craft_bed", "minecraft:white_bed") in home.calls
    bed = [204, 71, -37]
    assert ("place", tuple(bed), "minecraft:white_bed") in home.calls
    assert "interact_block" in client.routes
    assert state.custom_data["home_respawn"]["bed"] == bed
    assert state.custom_data["home_respawn"]["evidence"] == "chat"
    assert state.custom_data["structures"]["starter_house"]["bed"] == bed


def test_unconfirmed_bed_use_is_not_recorded(home):
    client = _Client(chat=("You can sleep only at night",))
    home.world["client"] = client
    state = _state()

    assert not home_respawn.secure_home_respawn(client, state, now=0.0)
    assert "home_respawn" not in state.custom_data


def test_existing_recorded_bed_needs_no_work(home):
    client = _Client()
    client.blocks[(204, 71, -37)] = "minecraft:white_bed"
    state = _state(home_respawn={"bed": [204, 71, -37]})

    assert home_respawn.secure_home_respawn(client, state, now=0.0)
    assert "interact_block" not in client.routes
    assert not home.calls


def test_missing_recorded_bed_is_replaced(home):
    client = _Client()
    home.world["client"] = client
    state = _state(home_respawn={"bed": [199, 71, -39]})

    assert home_respawn.secure_home_respawn(client, state, now=0.0)
    assert state.custom_data["home_respawn"]["bed"] == [204, 71, -37]


def test_away_from_home_hostiles_and_cooldown_do_nothing(home, monkeypatch):
    client = _Client(position=(600, 70, 30))
    home.world["client"] = client
    state = _state()
    assert not home_respawn.secure_home_respawn(client, state, now=0.0)
    assert client.routes == ["get_state"]

    client.position = (202, 71, -38)
    assert not home_respawn.secure_home_respawn(client, state, now=100.0)
    assert client.routes == ["get_state"]

    monkeypatch.setattr(
        "baritone_client.common.combat.scan_for_threats",
        lambda *_a, **_k: [{"distance": 4.0}],
    )
    monkeypatch.setattr(
        "baritone_client.common.combat._threat_can_reach_player", lambda *_a, **_k: True
    )
    assert not home_respawn.secure_home_respawn(client, state, now=400.0)
    assert not home.calls


def test_a_bed_far_from_home_is_not_accepted(home, monkeypatch):
    client = _Client()
    home.world["client"] = client
    monkeypatch.setattr(
        "baritone_client.common.navigation.find_nearby_block",
        lambda *_a, **_k: (-1629, 63, -89),
    )

    assert not home_respawn.secure_home_respawn(client, _state(), now=0.0)
    assert "interact_block" not in client.routes


def test_survival_gate_secures_respawn_even_while_critical(monkeypatch):
    live = {"health": 6.8, "food_level": 15, "is_dead": False}
    client = SimpleNamespace(
        transport=SimpleNamespace(dispatch=lambda _route, _payload: dict(live))
    )
    calls = []
    monkeypatch.setattr(
        objective_survival,
        "secure_home_respawn",
        lambda *_a: calls.append("respawn") or (_ for _ in ()).throw(RuntimeError("boom")),
    )
    monkeypatch.setattr(
        objective_survival, "_has_carried_emergency_bread_materials", lambda _c: False
    )
    monkeypatch.setattr(objective_survival, "tend_local_farm_for_food", lambda *_a: False)
    monkeypatch.setattr(objective_survival, "_attempt_survival_recovery_food", lambda *_a: False)
    monkeypatch.setattr(
        objective_survival, "_acquire_checkpointed_emergency_food", lambda *_a: False
    )

    assert not objective_survival.recover_survival_before_objective(client, _state())
    assert calls == ["respawn"]


def test_wool_tops_up_string_and_a_failing_container_is_skipped(home, monkeypatch):
    client = _Client()
    home.world["client"] = client
    home.stock["minecraft:string"] = 4
    from baritone_client.common import harness_ops

    moves = []

    def move(_client, x, y, z, **_k):
        moves.append((x, y, z))
        if (x, y, z) == (202, 71, -39) and len(moves) > 1:
            raise RuntimeError("Observed effect deadline exceeded")
        return True

    monkeypatch.setattr(harness_ops, "move_near", move)
    state = _state()

    assert home_respawn.secure_home_respawn(client, state, now=0.0)
    withdrawals = [call[1] for call in home.calls if call[0] == "withdraw"]
    assert withdrawals == [{"minecraft:string": 12}, {"minecraft:white_wool": 3}]
    assert state.custom_data["home_respawn"]["bed"] == [204, 71, -37]


def test_string_beyond_home_finishes_a_bed_home_cannot(home):
    client = _Client()
    home.world["client"] = client
    home.stock["minecraft:string"] = 0
    home.far["minecraft:string"] = 6
    home.chests["minecraft:string"] = [(600, 70, 30), (250, 20, -40), (300, 72, 60)]
    state = _state()

    assert home_respawn.secure_home_respawn(client, state, now=0.0)
    withdrawals = [(call[1], call[2], call[3]) for call in home.calls if call[0] == "withdraw"]
    # Home first with recovery access, then a wider trip without it, so the
    # storage layer still refuses while survival is unsafe.
    assert ({"minecraft:string": 4}, 6.0, False) in withdrawals
    crafts = [call for call in home.calls if call[0] == "craft"]
    assert crafts and crafts[-1][3] == 1
    # The wider trip goes to the surface chest in range; the cave chest
    # (51 blocks down) and the out-of-range chest are never travelled to.
    assert (300, 72, 60) in home.trips
    assert (250, 20, -40) not in home.trips and (600, 70, 30) not in home.trips
    assert state.custom_data["home_respawn"]["bed"] == [204, 71, -37]


def test_bed_is_not_attempted_without_enough_wool_anywhere(home):
    client = _Client()
    home.world["client"] = client
    home.stock["minecraft:string"] = 0
    state = _state()

    assert not home_respawn.secure_home_respawn(client, state, now=0.0)
    assert not [call for call in home.calls if call[0] in {"craft_bed", "place"}]
    assert "home_respawn" not in state.custom_data


def test_wider_storage_waits_until_travel_is_safe(home):
    client = _Client()
    home.world["client"] = client
    home.stock["minecraft:string"] = 0
    home.far["minecraft:string"] = 6
    wounded = client.dispatch

    def dispatch(route, payload):
        data = wounded(route, payload)
        if route == "get_state":
            data.update(health=6.8, food_level=15)
        return data

    client.transport.dispatch = dispatch

    assert not home_respawn.secure_home_respawn(client, _state(), now=0.0)
    assert not home.trips
    assert home.far["minecraft:string"] == 6


def test_bed_step_movement_is_marked_as_recovery_navigation(home, monkeypatch):
    client = _Client()
    home.world["client"] = client
    from baritone_client.common import harness_ops

    depths = []
    monkeypatch.setattr(
        harness_ops,
        "move_near",
        lambda c, *_a, **_k: depths.append(getattr(c, "_safe_recovery_navigation_depth", 0)) or True,
    )

    assert home_respawn.secure_home_respawn(client, _state(), now=0.0)
    assert depths and all(depth == 1 for depth in depths)
