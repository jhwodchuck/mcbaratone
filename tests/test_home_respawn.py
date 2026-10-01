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
        self.look = (0, 0)
        self.placements = []
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
            key = (payload["x"], payload["y"], payload["z"])
            ground = "minecraft:stone" if payload["y"] <= 70 else "minecraft:air"
            return {"id": self.blocks.get(key, ground)}
        if route == "look_at":
            self.look = (payload["x"], payload["z"])
            return {}
        if route == "place_block":
            # Like the game: the head goes one block in the facing direction.
            x, y, z = payload["x"], payload["y"], payload["z"]
            lx, lz = self.look
            dx = (lx > x + 1) - (lx < x)
            dz = (lz > z + 1) - (lz < z)
            self.blocks[(x, y, z)] = "minecraft:white_bed"
            self.blocks[(x + dx, y, z + dz)] = "minecraft:white_bed"
            self.placements.append(((x, y, z), (dx, dz)))
            return {"accepted": True}
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
        items[bed_id.replace("_bed", "_wool")] -= 3  # a bed eats three wool of its own colour
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
        lambda _client, _blocks, radius=50: next(
            (key for key, block in world["client"].blocks.items() if block.endswith("_bed")),
            None,
        ),
    )
    monkeypatch.setattr(
        "baritone_client.common.inventory.select_item",
        lambda _client, item, **_k: items.get(item, 0) > 0,
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
    assert client.placements == [((204, 71, -37), (1, 0))]
    assert client.blocks[(205, 71, -37)] == "minecraft:white_bed"
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
    client.blocks[(-1629, 63, -89)] = "minecraft:white_bed"
    monkeypatch.setattr(
        "baritone_client.common.navigation.find_nearby_block",
        lambda *_a, **_k: (-1629, 63, -89),
    )
    state = _state()

    assert home_respawn.secure_home_respawn(client, state, now=0.0)
    assert state.custom_data["home_respawn"]["bed"] == [204, 71, -37]


def test_a_phantom_bed_search_hit_is_not_used(home, monkeypatch):
    # The live search reported a bed the server had already rejected.
    client = _Client()
    home.world["client"] = client
    calls = {"n": 0}

    def phantom_then_real(_client, _blocks, radius=50):
        calls["n"] += 1
        if calls["n"] == 1:
            return (203, 71, -39)  # air in this world
        return None

    monkeypatch.setattr(
        "baritone_client.common.navigation.find_nearby_block", phantom_then_real
    )
    state = _state()

    assert home_respawn.secure_home_respawn(client, state, now=0.0)
    assert state.custom_data["home_respawn"]["bed"] == [204, 71, -37]


def test_bed_whose_head_is_rejected_moves_to_another_site(home):
    client = _Client()
    home.world["client"] = client
    original = client.dispatch

    def reject_first(route, payload):
        data = original(route, payload)
        if route == "place_block" and len(client.placements) == 1:
            # Server refused the bed: both halves revert to air.
            (x, y, z), (dx, dz) = client.placements[0]
            client.blocks.pop((x, y, z), None)
            client.blocks.pop((x + dx, y, z + dz), None)
        return data

    client.transport.dispatch = reject_first
    state = _state()

    assert home_respawn.secure_home_respawn(client, state, now=0.0)
    assert len(client.placements) == 2
    assert state.custom_data["home_respawn"]["bed"] != [204, 71, -37]


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


def _hunt_setup(home, monkeypatch, *, health=20.0, food=20, world_time=6000, sheep=True):
    client = _Client()
    home.world["client"] = client
    home.stock["minecraft:string"] = 0
    home.items["minecraft:bread"] = home_respawn.SHEEP_HUNT_PREPARED_RESERVE
    monkeypatch.setattr("baritone_client.common.inventory.get_inventory",
                        lambda _c: dict(home.items))
    base = client.dispatch

    def dispatch(route, payload):
        data = base(route, payload)
        if route == "get_state":
            data.update(health=health, food_level=food, world_time=world_time)
        return data

    client.transport.dispatch = dispatch
    hunts = []

    def hunt(_client, **kwargs):
        hunts.append(kwargs)
        if sheep:
            home.items["minecraft:white_wool"] = home.items.get("minecraft:white_wool", 0) + 1
        return SimpleNamespace(success=sheep)

    monkeypatch.setattr("baritone_client.common.combat.hunt_mobs", hunt)
    return client, hunts


def test_sheep_hunt_supplies_the_last_wool_and_returns_home(home, monkeypatch):
    client, hunts = _hunt_setup(home, monkeypatch)
    state = _state()

    assert home_respawn.secure_home_respawn(client, state, now=0.0)
    assert len(hunts) == 1
    hunt = hunts[0]
    assert hunt["mob_types"] == ["sheep"]
    assert hunt["required_loot"] == {"minecraft:white_wool": 1}
    assert hunt["abort_on_other_hostiles"] is True
    assert hunt["max_distance_from_origin"] == home_respawn.SHEEP_HUNT_RINGS[0]
    assert hunt["max_kills"] == 1 + home_respawn.SHEEP_HUNT_SPARE_KILLS
    assert tuple(BASE) in home.trips  # walked home afterwards
    record = state.custom_data[home_respawn.SHEEP_HUNT_KEY]
    assert record["failures"] == 0 and record["last_attempt"] > 0
    assert state.custom_data["home_respawn"]["bed"] == [204, 71, -37]


@pytest.mark.parametrize(
    ("health", "food", "world_time"),
    [(15.0, 20, 6000), (20.0, 16, 6000), (20.0, 20, 12500)],
)
def test_sheep_hunt_needs_strength_food_and_daylight(home, monkeypatch, health, food, world_time):
    client, hunts = _hunt_setup(
        home, monkeypatch, health=health, food=food, world_time=world_time
    )

    assert not home_respawn.secure_home_respawn(client, _state(), now=0.0)
    assert hunts == []


def test_failed_hunts_cool_down_then_widen(home, monkeypatch):
    client, hunts = _hunt_setup(home, monkeypatch, sheep=False)
    state = _state()
    clock = {"t": 10_000.0}
    monkeypatch.setattr(home_respawn.time, "time", lambda: clock["t"])

    for step in range(4):
        client._home_respawn_last = None
        home_respawn.secure_home_respawn(client, state, now=float(step))
        client._home_respawn_last = None
        home_respawn.secure_home_respawn(client, state, now=float(step) + 0.5)  # cooldown
        clock["t"] += home_respawn.SHEEP_HUNT_INTERVAL

    assert len(hunts) == 4
    radii = [hunt["max_distance_from_origin"] for hunt in hunts]
    assert radii[:3] == [home_respawn.SHEEP_HUNT_RINGS[0]] * 3
    assert radii[3] == home_respawn.SHEEP_HUNT_RINGS[1]
    centers = {hunt["exploration_center"] for hunt in hunts}
    assert len(centers) == 4
    assert state.custom_data[home_respawn.SHEEP_HUNT_KEY]["failures"] == 4


def test_hostile_interrupted_hunt_does_not_widen_the_ring(home, monkeypatch, capsys):
    client, hunts = _hunt_setup(home, monkeypatch, sheep=False)
    monkeypatch.setattr(
        "baritone_client.common.combat.hunt_mobs",
        lambda _client, **kwargs: hunts.append(kwargs)
        or SimpleNamespace(success=False, reason="Hostile minecraft:zombie entered passive-hunt radius"),
    )
    state = _state()

    assert not home_respawn.secure_home_respawn(client, state, now=0.0)
    assert len(hunts) == 1
    record = state.custom_data[home_respawn.SHEEP_HUNT_KEY]
    assert record.get("failures", 0) == 0
    assert record["last_attempt"] > 0
    assert "Hostile minecraft:zombie" in capsys.readouterr().out


# -- any colour of wool, and planks ----------------------------------------
def test_a_bed_is_made_from_three_wool_of_any_one_colour(home):
    """Live A1 held black and gray wool and kept failing on 'white wool'."""
    client = _Client()
    home.world["client"] = client
    home.stock.clear()  # nothing useful in storage
    home.items["minecraft:black_wool"] = 3

    assert home_respawn.secure_home_respawn(client, _state(), now=0.0)

    assert ("craft_bed", "minecraft:black_bed") in home.calls
    assert not [call for call in home.calls if call[0] == "craft"]  # no string detour
    assert client.blocks[(205, 71, -37)].endswith("_bed")


@pytest.mark.parametrize(
    ("carried", "expected"),
    [
        ({}, ("minecraft:white_wool", 0)),
        ({"minecraft:white_wool": 1, "minecraft:black_wool": 1}, ("minecraft:white_wool", 1)),
        ({"minecraft:black_wool": 1}, ("minecraft:white_wool", 0)),  # a stray one is not a plan
        ({"minecraft:black_wool": 2}, ("minecraft:black_wool", 2)),
        ({"minecraft:white_wool": 1, "minecraft:black_wool": 3}, ("minecraft:black_wool", 3)),
        ({"minecraft:gray_wool": 2, "minecraft:black_wool": 1}, ("minecraft:gray_wool", 2)),
        ({"minecraft:white_wool": 2, "minecraft:gray_wool": 2}, ("minecraft:white_wool", 2)),
    ],
)
def test_best_wool_is_the_colour_closest_to_three_with_white_favoured(home, carried, expected):
    home.items.update(carried)
    assert home_respawn._best_wool(None) == expected
    assert home_respawn._missing_wool(None) == 3 - expected[1]


def test_storage_wool_of_another_colour_is_withdrawn_when_white_is_not_there(home):
    client = _Client()
    home.world["client"] = client
    home.stock.clear()
    home.chests["minecraft:green_wool"] = [(202, 71, -39)]
    home.stock["minecraft:green_wool"] = 5
    home.chests["minecraft:string"] = []

    assert home_respawn.secure_home_respawn(client, _state(), now=0.0)

    assert ("craft_bed", "minecraft:green_bed") in home.calls


def test_the_sheep_hunt_asks_for_the_colour_already_carried(home, monkeypatch):
    home.items["minecraft:black_wool"] = 2
    asked = {}

    def hunt(_client, **kwargs):
        asked.update(kwargs)
        return SimpleNamespace(reason="done")

    monkeypatch.setattr("baritone_client.common.combat.hunt_mobs", hunt)
    monkeypatch.setattr(home_respawn, "_hunt_ready", lambda *_a: True)
    monkeypatch.setattr(home_respawn, "_hunt_food_ready", lambda _c: True)
    client = _Client()
    home.world["client"] = client

    home_respawn._hunt_sheep_for_wool(client, _state(), tuple(BASE))

    assert asked["required_loot"] == {"minecraft:black_wool": 1}


class _Healthy(_Client):
    def dispatch(self, route, payload):
        live = super().dispatch(route, payload)
        if route == "get_state":
            live.update(health=20.0, food_level=20, world_time=2000)
        return live


def test_a_bed_with_wool_but_no_planks_fetches_one_log_and_makes_them(home, monkeypatch):
    home.items["minecraft:oak_planks"] = 0
    home.items["minecraft:white_wool"] = 3
    trips = []

    def gather(_client, count=16, timeout=180, **kwargs):
        trips.append((count, timeout, kwargs))
        home.items["minecraft:oak_log"] = 1
        return True

    def make_planks(_client, required):
        home.items["minecraft:oak_log"] -= 1
        home.items["minecraft:oak_planks"] += 4
        return True

    monkeypatch.setattr("baritone_client.common.resources.gather_wood", gather)
    monkeypatch.setattr("baritone_client.common.inventory._ensure_raw_planks", make_planks)
    client = _Healthy()
    home.world["client"] = client

    assert home_respawn.secure_home_respawn(client, _state(), now=0.0)

    (count, timeout, kwargs), = trips
    assert count == 1 and timeout == home_respawn.PLANK_TRIP_TIMEOUT
    assert kwargs["abort_on_threats"] is True
    assert kwargs["max_distance_from_origin"] == home_respawn.PLANK_TRIP_RADIUS
    assert ("craft_bed", "minecraft:white_bed") in home.calls


@pytest.mark.parametrize("live", [{"health": 9.0, "food_level": 20, "world_time": 2000},
                                  {"health": 20.0, "food_level": 8, "world_time": 2000},
                                  {"health": 20.0, "food_level": 20, "world_time": 13000}])
def test_no_tree_trip_when_hurt_hungry_or_after_dark(home, monkeypatch, live):
    home.items["minecraft:oak_planks"] = 0
    home.items["minecraft:white_wool"] = 3
    monkeypatch.setattr(
        "baritone_client.common.resources.gather_wood",
        lambda *_a, **_k: pytest.fail("must not wander for wood in this state"),
    )

    class Client(_Client):
        def dispatch(self, route, payload):
            data = super().dispatch(route, payload)
            if route == "get_state":
                data.update(live)
            return data

    client = Client()
    home.world["client"] = client
    assert not home_respawn.secure_home_respawn(client, _state(), now=0.0)
    assert not [call for call in home.calls if call[0] in {"craft_bed", "place"}]
