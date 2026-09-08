import unittest.mock as mock
from types import SimpleNamespace

import pytest

from baritone_client.automator.phases import nether_prep
from baritone_client.automator.resource_manager import ResourceManager
from baritone_client.automator.state_manager import Phase, StateManager
from baritone_client.common import blaze_spawners, nether
from baritone_client.common.tasks import PlayerDeathDetected, TaskResult


def _goto_stopped(client, *_args, **_kwargs):
    client._last_navigation_cancel = {"cancel_status": "stopped"}
    return True


class MissionStub:
    def __init__(self):
        self.checkpoints = []

    def macro(self, name, params):
        assert name == "enter_nether"
        return {"result": {"ready": params["obsidian"] >= 10}}

    def checkpoint(self, phase, note=None):
        self.checkpoints.append((phase, note))


class PortalTransport:
    def __init__(self):
        self.dimension = "minecraft:overworld"
        self.blocks = {}
        self.calls = []
        self.position = {"x": 0, "y": 64, "z": 0}
        self.found_blocks = []

    def dispatch(self, route, payload, **_kwargs):
        self.calls.append((route, dict(payload)))
        if route == "get_state":
            return {
                "dimension": self.dimension,
                "block_position": dict(self.position),
            }
        if route == "get_block":
            key = (payload["x"], payload["y"], payload["z"])
            return {"id": self.blocks.get(key, "minecraft:air")}
        if route == "get_inventory":
            return {"inventory": [
                {"id": "minecraft:obsidian", "count": 10},
                {"id": "minecraft:cobblestone", "count": 4},
            ], "armor": [], "offhand": []}
        if route == "place_fire":
            x, y, z = payload["x"], payload["y"], payload["z"]
            for dx in (0, 1):
                for dy in (0, 1, 2):
                    self.blocks[(x + dx, y + dy, z)] = "minecraft:nether_portal"
                    # A lit portal is visible to a find_blocks sweep, which is
                    # how the client locates the interior it should stand in.
                    self.found_blocks.append({"x": x + dx, "y": y + dy, "z": z})
            return {"ignited": True}
        if route == "goto":
            self.position = {
                "x": payload["x"],
                "y": payload["y"],
                "z": payload["z"],
            }
            # You only change dimension by standing in a portal block. The
            # stub used to flip on every goto, which hid the difference
            # between an approach and an actual entry.
            destination = (payload["x"], payload["y"], payload["z"])
            if self.blocks.get(destination) == "minecraft:nether_portal":
                self.dimension = (
                    "minecraft:the_nether"
                    if "nether" not in self.dimension
                    else "minecraft:overworld"
                )
            return {"started": True}
        if route == "find_blocks":
            return {"found": list(self.found_blocks)}
        return {}


def test_build_ignite_verify_and_enter_portal(monkeypatch):
    transport = PortalTransport()
    client = SimpleNamespace(transport=transport, mission=MissionStub())
    portal = (3, 64, 0)
    # A buildable site needs a floor: the bottom pair has to anchor to
    # something, or the build is refused before it spends any obsidian.
    for floor_x in range(3, 7):
        for floor_z in (-1, 0, 1):
            transport.blocks[(floor_x, 63, floor_z)] = "minecraft:stone"

    def place(_client, x, y, z, item_id):
        assert item_id in {"minecraft:obsidian", "minecraft:cobblestone"}
        transport.blocks[(x, y, z)] = item_id
        return True

    monkeypatch.setattr("baritone_client.common.portal_construction.place_block", place)
    monkeypatch.setattr("baritone_client.common.portal_construction.goto", _goto_stopped)
    monkeypatch.setattr("baritone_client.common.portal_construction.time.sleep", lambda _seconds: None)

    assert nether.build_nether_portal(client, *portal)
    assert nether.verify_portal(client, portal, require_active=False)
    assert not nether.verify_portal(client, portal, require_active=True)
    monkeypatch.setattr("baritone_client.common.inventory.select_item", lambda *_a, **_k: True)
    monkeypatch.setattr("baritone_client.common.portal_construction.prepare_ignition_pose", lambda *_a: True)
    assert nether.ignite_portal(client, portal)
    assert nether.verify_portal(client, portal, require_active=True)
    assert nether.enter_portal(
        client, portal, target_dimension="minecraft:the_nether", timeout=1
    )
    goto = [payload for route, payload in transport.calls if route == "goto"][-1]
    # The lowest lit interior block, found by scanning rather than guessed.
    # The old (x+1, y+1, z) offset happened to match this geometry, which is
    # why it survived; see the frame-block test below for one it does not.
    assert goto == {"x": 4, "y": 65, "z": 0, "radius": 0}


def test_find_nearest_portal_uses_supported_unwrapped_find_blocks_route():
    class FindTransport(PortalTransport):
        def dispatch(self, route, payload, **kwargs):
            if route == "find_blocks":
                assert payload["blocks"] == ["minecraft:nether_portal"]
                return {
                    "found": [
                        {"x": 20, "y": 70, "z": 20, "distance": 30.0},
                        {"x": 3, "y": 65, "z": 4, "distance": 5.0},
                    ]
                }
            return super().dispatch(route, payload, **kwargs)

    transport = FindTransport()
    client = SimpleNamespace(transport=transport)
    assert nether.find_nearest_portal(client) == (3, 65, 4)


def test_fortress_detection_uses_find_blocks_and_unwrapped_response(monkeypatch):
    class FortressTransport(PortalTransport):
        def dispatch(self, route, payload, **kwargs):
            if route == "find_blocks":
                assert "minecraft:nether_bricks" in payload["blocks"]
                return {
                    "found": [
                        {
                            "x": 100 + index,
                            "y": 64,
                            "z": 200,
                            "block": "minecraft:nether_bricks",
                        }
                        for index in range(40)
                    ]
                }
            return super().dispatch(route, payload, **kwargs)

    transport = FortressTransport()
    transport.dimension = "minecraft:the_nether"
    client = SimpleNamespace(transport=transport)
    monkeypatch.setattr(nether.time, "sleep", lambda _seconds: None)
    assert nether.find_nether_fortress(client, timeout=1) == (119, 64, 200)
    assert not any(route == "scan_blocks" for route, _ in transport.calls)


def test_nether_handler_persists_portal_pair_and_fortress(monkeypatch, tmp_path):
    transport = PortalTransport()
    client = SimpleNamespace(transport=transport, mission=MissionStub())
    resources = ResourceManager(client)
    state = StateManager(checkpoint_dir=tmp_path)
    handler = nether_prep.NetherAndBlazeHandler()
    rods = {"count": 0}

    monkeypatch.setattr(nether_prep, "_ensure_raw_planks", lambda *_args: True)
    monkeypatch.setattr(handler, "_ensure_nether_readiness", lambda *_args: True)
    monkeypatch.setattr(
        nether_prep,
        "ensure_supplies",
        lambda *_args, **_kwargs: TaskResult.ok(),
    )
    monkeypatch.setattr(
        nether_prep,
        "_read_state_with_retry",
        lambda *_args, **_kwargs: (
            {"block_position": {"x": 0, "y": 64, "z": 0}},
            None,
        ),
    )
    monkeypatch.setattr(nether_prep, "build_nether_portal", lambda *_args: True)
    monkeypatch.setattr(nether_prep, "ignite_portal", lambda *_args: True)
    monkeypatch.setattr(
        nether_prep,
        "verify_portal",
        lambda *_args, **_kwargs: True,
    )

    def enter(_client, _portal, *, target_dimension, timeout):
        transport.dimension = target_dimension
        return True

    monkeypatch.setattr(nether_prep, "enter_portal", enter)
    monkeypatch.setattr(
        nether_prep,
        "find_nearest_portal",
        lambda *_args: (100, 65, 100),
    )
    monkeypatch.setattr(
        nether_prep,
        "find_nether_fortress",
        lambda *_args: (240, 70, -80),
    )

    def hunt(_client, target_count, *, state):
        assert target_count == 6
        assert state is not None
        rods["count"] = 6
        return 6

    monkeypatch.setattr(nether_prep, "hunt_blazes", hunt)
    monkeypatch.setattr(
        nether_prep,
        "count_item",
        lambda *_args: rods["count"],
    )

    result = handler.execute(client, resources, state)
    assert result.success
    assert transport.dimension == "minecraft:overworld"
    portals = state.get_locations("nether_portal")["nether_portal"]
    assert {entry["dimension"] for entry in portals} == {
        "overworld",
        "the_nether",
    }
    fortress = state.get_locations("nether_fortress")["nether_fortress"]
    assert (fortress[0]["x"], fortress[0]["y"], fortress[0]["z"]) == (
        240,
        70,
        -80,
    )
    assert state.get_phase_payload(Phase.NETHER_AND_BLAZE)["blaze_rods"] == 6


def test_nether_handler_records_existing_overworld_blaze_supply_without_rearming(
    monkeypatch, tmp_path
):
    handler, client, state = _portal_reuse_handler(tmp_path)
    monkeypatch.setattr(nether_prep, "count_item", lambda *_args: 11)
    monkeypatch.setattr(
        handler,
        "_ensure_nether_readiness",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("completed blaze supply must not trigger another rearm")
        ),
    )
    monkeypatch.setattr(
        handler,
        "_return_to_overworld",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("an Overworld bot must not enter or leave a portal")
        ),
    )

    result = handler.execute(client, ResourceManager(client), state)

    assert result.success
    assert result.data["blaze_rods"] == 11
    assert state.get_phase_payload(Phase.NETHER_AND_BLAZE)["blaze_rods"] == 11


def test_nether_handler_returns_existing_blaze_supply_from_nether(monkeypatch, tmp_path):
    handler, client, state = _portal_reuse_handler(tmp_path)
    client.transport.dimension = "minecraft:the_nether"
    returned = []
    monkeypatch.setattr(nether_prep, "count_item", lambda *_args: 6)
    monkeypatch.setattr(
        handler,
        "_return_to_overworld",
        lambda *_args: returned.append(True) or True,
    )

    result = handler.execute(client, ResourceManager(client), state)

    assert result.success
    assert returned == [True]
    assert state.get_phase_payload(Phase.NETHER_AND_BLAZE)["blaze_rods"] == 6


def test_nether_readiness_blocks_a_naked_checkpoint_resume(monkeypatch, tmp_path):
    handler, client, state = _portal_reuse_handler(tmp_path)
    client.transport.dimension = "minecraft:the_nether"
    returned = []

    monkeypatch.setattr(nether_prep, "has_full_armor", lambda *_a, **_k: False)
    monkeypatch.setattr(nether_prep, "count_item", lambda *_a, **_k: 0)
    monkeypatch.setattr(nether_prep, "_emergency_food_count", lambda *_a: 0)
    monkeypatch.setattr(nether_prep, "equip_best_weapon", lambda *_a: False)
    monkeypatch.setattr(
        handler,
        "_return_to_overworld",
        lambda *_a: returned.append(True) or True,
    )

    assert handler._ensure_nether_readiness(client, state) is False
    assert returned == [True], "an under-equipped Nether bot must retreat to rearm"


def test_nether_rearm_provisions_and_equips_armor_incrementally(monkeypatch, tmp_path):
    handler, client, state = _portal_reuse_handler(tmp_path)
    calls = []
    events = []
    counts = {}
    readiness = iter((False, True))
    monkeypatch.setattr(handler, "_nether_loadout_ready", lambda *_a: next(readiness))
    monkeypatch.setattr(
        nether_prep,
        "count_item",
        lambda _client, item_id: counts.get(item_id, 0),
    )
    monkeypatch.setattr(nether_prep, "_emergency_food_count", lambda *_a: 6)
    monkeypatch.setattr(nether_prep, "armor_piece_is_durable", lambda *_a: True)
    monkeypatch.setattr(
        nether_prep,
        "equip_best_armor",
        lambda *_a: events.append("equip_armor") or 4,
    )
    monkeypatch.setattr(
        nether_prep,
        "equip_best_weapon",
        lambda *_a: events.append("equip_weapon") or True,
    )
    monkeypatch.setattr(
        nether_prep,
        "eat_until_hunger",
        lambda *_a, **_k: events.append("eat") or True,
    )
    monkeypatch.setattr(
        nether_prep,
        "recover_health",
        lambda *_a, **_k: events.append("recover") or True,
    )

    def supplies(_client, required, **_kwargs):
        events.append("supplies")
        calls.append(required)
        item_id, quantity = next(iter(required.items()))
        counts[item_id] = quantity
        if item_id != "minecraft:iron_ingot":
            costs = {
                "minecraft:iron_boots": 4,
                "minecraft:iron_helmet": 5,
                "minecraft:iron_leggings": 7,
                "minecraft:iron_sword": 2,
                "minecraft:iron_chestplate": 8,
                "minecraft:shield": 1,
            }
            counts["minecraft:iron_ingot"] = max(
                0, counts.get("minecraft:iron_ingot", 0) - costs[item_id]
            )
        return TaskResult.ok()

    monkeypatch.setattr(nether_prep, "ensure_supplies", supplies)

    assert handler._ensure_nether_readiness(client, state) is True
    # Armor now comes first: equipping is free and crafting spends only iron
    # already carried, so neither may sit behind the hunger gate. A fleet at
    # zero food otherwise never gets armored at all (2026-08-06 deadlock).
    assert events[:2] == ["equip_armor", "equip_weapon"]
    assert events.index("equip_armor") < events.index("eat")
    assert "eat" in events and "recover" in events
    assert calls[:6] == [
        {"minecraft:iron_ingot": 4},
        {"minecraft:iron_boots": 1},
        {"minecraft:iron_ingot": 5},
        {"minecraft:iron_helmet": 1},
        {"minecraft:iron_ingot": 7},
        {"minecraft:iron_leggings": 1},
    ]
    assert calls[6:] == [
        {"minecraft:iron_ingot": 2},
        {"minecraft:iron_sword": 1},
        {"minecraft:iron_ingot": 8},
        {"minecraft:iron_chestplate": 1},
        {"minecraft:iron_ingot": 1},
        {"minecraft:shield": 1},
    ]


def test_nether_rearm_requests_a_six_item_food_reserve(
    monkeypatch, tmp_path, advancing_clock
):
    handler, client, state = _portal_reuse_handler(tmp_path)
    readiness = iter((False, True))
    requested = []
    monkeypatch.setattr(
        "baritone_client.common.inventory.time", advancing_clock()
    )
    monkeypatch.setattr(handler, "_nether_loadout_ready", lambda *_a: next(readiness))
    monkeypatch.setattr(
        handler, "_provision_iron_gear", lambda *_a, **_k: True
    )
    monkeypatch.setattr(nether_prep, "equip_best_armor", lambda *_a: 4)
    monkeypatch.setattr(nether_prep, "equip_best_weapon", lambda *_a: True)
    monkeypatch.setattr(nether_prep, "eat_until_hunger", lambda *_a, **_k: True)
    monkeypatch.setattr(nether_prep, "recover_health", lambda *_a, **_k: True)
    monkeypatch.setattr(nether_prep, "_emergency_food_count", lambda *_a: 0)
    monkeypatch.setattr(
        nether_prep,
        "acquire_emergency_food",
        lambda *_a, **kwargs: requested.append(kwargs) or True,
    )

    assert handler._ensure_nether_readiness(client, state)
    assert requested[0]["minimum_reserve"] == 6


def test_nether_rearm_falls_back_to_known_food_source_when_local_search_fails(
    monkeypatch, tmp_path
):
    """The bounded local hunt can never succeed in an animal-sparse biome --
    it retries the same empty area forever. Live A1 2026-09-06: rearm
    rotated through a dozen empty 64-block sweeps across several failed
    attempts (and two deaths) while a farm or herd FOOD_AND_IRON had
    already verified sat unused in this same checkpoint. The rearm gate
    must try that known source before giving up, the same fallback
    FOOD_AND_IRON's own hunger gate already uses.
    """
    handler, client, state = _portal_reuse_handler(tmp_path)
    readiness = iter((False, True))
    monkeypatch.setattr(handler, "_nether_loadout_ready", lambda *_a: next(readiness))
    monkeypatch.setattr(handler, "_provision_iron_gear", lambda *_a, **_k: True)
    monkeypatch.setattr(nether_prep, "equip_best_armor", lambda *_a: 4)
    monkeypatch.setattr(nether_prep, "equip_best_weapon", lambda *_a: True)
    monkeypatch.setattr(nether_prep, "recover_health", lambda *_a, **_k: True)
    monkeypatch.setattr(nether_prep, "eat_until_hunger", lambda *_a, **_k: False)
    monkeypatch.setattr(nether_prep, "acquire_emergency_food", lambda *_a, **_k: False)
    monkeypatch.setattr(nether_prep, "_emergency_food_count", lambda *_a: 6)

    fallback_calls = []

    def fallback(_client, _state, **kwargs):
        fallback_calls.append(kwargs)
        return True

    monkeypatch.setattr(nether_prep, "recover_food_from_known_sources", fallback)

    assert handler._ensure_nether_readiness(client, state)
    assert fallback_calls == [{"minimum_food": 18}]


def test_nether_rearm_still_pauses_when_no_known_food_source_helps(
    monkeypatch, tmp_path
):
    """Unchanged safety behavior when nothing -- local search or known
    source -- can restore hunger: rearm must still pause, not proceed
    naked."""
    handler, client, state = _portal_reuse_handler(tmp_path)
    monkeypatch.setattr(handler, "_nether_loadout_ready", lambda *_a: False)
    monkeypatch.setattr(nether_prep, "equip_best_armor", lambda *_a: 4)
    monkeypatch.setattr(nether_prep, "equip_best_weapon", lambda *_a: True)
    monkeypatch.setattr(nether_prep, "eat_until_hunger", lambda *_a, **_k: False)
    monkeypatch.setattr(nether_prep, "acquire_emergency_food", lambda *_a, **_k: False)
    monkeypatch.setattr(
        nether_prep, "recover_food_from_known_sources", lambda *_a, **_k: False
    )

    assert handler._ensure_nether_readiness(client, state) is False


def test_nether_rearm_equips_recovered_armor_before_any_mining(monkeypatch, tmp_path):
    handler, client, state = _portal_reuse_handler(tmp_path)
    events = []
    counts = {
        "minecraft:iron_boots": 1,
        "minecraft:iron_helmet": 1,
        "minecraft:iron_leggings": 1,
        "minecraft:iron_sword": 1,
    }
    readiness = iter((False, True))
    monkeypatch.setattr(handler, "_nether_loadout_ready", lambda *_a: next(readiness))
    monkeypatch.setattr(
        nether_prep,
        "count_item",
        lambda _client, item_id: counts.get(item_id, 0),
    )
    monkeypatch.setattr(nether_prep, "_emergency_food_count", lambda *_a: 6)
    monkeypatch.setattr(nether_prep, "armor_piece_is_durable", lambda *_a: True)
    monkeypatch.setattr(
        nether_prep,
        "equip_best_armor",
        lambda *_a: events.append("equip_armor") or 3,
    )
    monkeypatch.setattr(
        nether_prep,
        "equip_best_weapon",
        lambda *_a: events.append("equip_weapon") or True,
    )
    monkeypatch.setattr(nether_prep, "eat_until_hunger", lambda *_a, **_k: True)
    monkeypatch.setattr(nether_prep, "recover_health", lambda *_a, **_k: True)

    def supplies(_client, required, **_kwargs):
        events.append(("supplies", required))
        item_id, quantity = next(iter(required.items()))
        counts[item_id] = quantity
        if item_id == "minecraft:iron_chestplate":
            counts["minecraft:iron_ingot"] = 0
        return TaskResult.ok()

    monkeypatch.setattr(nether_prep, "ensure_supplies", supplies)

    assert handler._ensure_nether_readiness(client, state) is True
    first_supply = next(
        index for index, event in enumerate(events) if isinstance(event, tuple)
    )
    assert events.index("equip_armor") < first_supply
    assert events[first_supply] == ("supplies", {"minecraft:iron_ingot": 8})


def test_nether_rearm_crafts_a_replacement_for_worn_armor(monkeypatch, tmp_path):
    handler, client, state = _portal_reuse_handler(tmp_path)
    counts = {
        "minecraft:iron_chestplate": 1,
        "minecraft:iron_ingot": 8,
    }
    requested = []
    monkeypatch.setattr(
        nether_prep,
        "count_item",
        lambda _client, item_id: counts.get(item_id, 0),
    )
    monkeypatch.setattr(
        nether_prep,
        "ensure_supplies",
        lambda _client, required, **_kwargs: requested.append(required)
        or TaskResult.ok(),
    )

    assert handler._provision_iron_gear(
        client,
        state,
        "minecraft:iron_chestplate",
        8,
        force_replacement=True,
    )
    assert requested == [{"minecraft:iron_chestplate": 2}]


def test_provision_iron_gear_withdraws_banked_ingots_before_gathering(
    monkeypatch, tmp_path
):
    """Already-banked iron must be withdrawn before mining more from scratch.

    ensure_supplies deliberately never touches storage (see resources.py's
    own comment on _missing_requirements), so an explicit, checkpointed
    objective like Nether rearm has to ask a catalog withdrawal itself, the
    same way villager.py's bread provisioning already does. Live A1
    2026-09-08: this gather-from-scratch step alone took ~10 minutes per
    Nether-readiness attempt even when the home chest already held spare
    iron.
    """
    handler, client, state = _portal_reuse_handler(tmp_path)
    counts = {"minecraft:iron_ingot": 0}
    withdrawals = []

    def fake_withdraw(_client, requirements, **kwargs):
        counts["minecraft:iron_ingot"] += requirements["minecraft:iron_ingot"]
        withdrawals.append((requirements, kwargs.get("state")))
        return 1

    gathered = []
    monkeypatch.setattr(
        nether_prep, "count_item", lambda _client, item_id: counts.get(item_id, 0)
    )
    monkeypatch.setattr(nether_prep, "withdraw_required_from_catalog", fake_withdraw)
    monkeypatch.setattr(
        nether_prep,
        "ensure_supplies",
        lambda _client, required, **_kwargs: gathered.append(required)
        or TaskResult.ok(),
    )

    assert handler._provision_iron_gear(client, state, "minecraft:iron_sword", 2)
    assert withdrawals == [({"minecraft:iron_ingot": 2}, state)]
    # The withdrawal alone satisfied the ingot requirement, so the slow
    # gather-from-scratch strategy must never run for it.
    assert {"minecraft:iron_ingot": 2} not in gathered
    assert {"minecraft:iron_sword": 1} in gathered


def test_provision_iron_gear_still_gathers_when_storage_is_short(
    monkeypatch, tmp_path
):
    """A partial or empty catalog withdrawal must fall back to gathering."""
    handler, client, state = _portal_reuse_handler(tmp_path)
    counts = {"minecraft:iron_ingot": 0}
    gathered = []
    monkeypatch.setattr(
        nether_prep, "count_item", lambda _client, item_id: counts.get(item_id, 0)
    )
    monkeypatch.setattr(
        nether_prep, "withdraw_required_from_catalog", lambda *_a, **_k: 0
    )
    monkeypatch.setattr(
        nether_prep,
        "ensure_supplies",
        lambda _client, required, **_kwargs: gathered.append(required)
        or TaskResult.ok(),
    )

    assert handler._provision_iron_gear(client, state, "minecraft:iron_sword", 2)
    assert {"minecraft:iron_ingot": 2} in gathered


def _portal_reuse_handler(tmp_path):
    transport = PortalTransport()
    client = SimpleNamespace(transport=transport, mission=MissionStub())
    state = StateManager(checkpoint_dir=tmp_path)
    return nether_prep.NetherAndBlazeHandler(), client, state


def test_prepare_portal_reuses_an_existing_overworld_portal(monkeypatch, tmp_path):
    """A lit portal already standing must be adopted, not rebuilt.

    find_nearest_portal was imported here but only ever called from inside
    the Nether, so an Overworld portal the bot could see -- one it built on
    an earlier run, or one an operator placed -- was invisible to this phase.
    Live 2026-08-02: Bot16 abandoned NETHER_AND_BLAZE after three failed
    builds while a usable portal stood by the settlement.
    """
    handler, client, state = _portal_reuse_handler(tmp_path)
    built = []

    monkeypatch.setattr(nether_prep, "find_nearest_portal", lambda *_a: (12, 65, -34))
    monkeypatch.setattr(nether_prep, "verify_portal", lambda *_a, **_k: True)
    monkeypatch.setattr(
        nether_prep, "build_nether_portal",
        lambda *_a: built.append("built") or True,
    )
    # Stubbed so the pre-fix code path fails its assertion immediately rather
    # than dropping into real gathering against a stub transport and hanging.
    monkeypatch.setattr(nether_prep, "_ensure_raw_planks", lambda *_a: True)
    monkeypatch.setattr(
        nether_prep, "ensure_supplies",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("must not gather obsidian when a portal already exists")
        ),
    )

    assert handler._prepare_portal(client, state) is True
    assert built == [], "an existing portal must not trigger a rebuild"

    recorded = state.get_locations("nether_portal").get("nether_portal", [])
    assert any(
        (entry.get("x"), entry.get("y"), entry.get("z")) == (12, 65, -34)
        for entry in recorded
    ), f"portal not persisted: {recorded}"


def test_prepare_portal_ignores_an_unlit_nearby_portal_frame(monkeypatch, tmp_path):
    """A frame that fails verification is not usable; fall through to building."""
    handler, client, state = _portal_reuse_handler(tmp_path)

    monkeypatch.setattr(nether_prep, "find_nearest_portal", lambda *_a: (12, 65, -34))
    monkeypatch.setattr(nether_prep, "verify_portal", lambda *_a, **_k: False)
    monkeypatch.setattr(nether_prep, "_ensure_raw_planks", lambda *_a: True)
    monkeypatch.setattr(
        nether_prep, "ensure_supplies", lambda *_a, **_k: TaskResult.fail("no obsidian")
    )

    # Falls through to the build path, which fails here for want of materials.
    assert handler._prepare_portal(client, state) is False


def test_prepare_portal_still_builds_when_nothing_is_nearby(monkeypatch, tmp_path):
    handler, client, state = _portal_reuse_handler(tmp_path)
    built = []

    monkeypatch.setattr(nether_prep, "find_nearest_portal", lambda *_a: None)
    monkeypatch.setattr(nether_prep, "_choose_portal_site", lambda *_a: (3, 64, 0))
    monkeypatch.setattr(nether_prep, "verify_portal", lambda *_a, **_k: True)
    monkeypatch.setattr(nether_prep, "_ensure_raw_planks", lambda *_a: True)
    monkeypatch.setattr(nether_prep, "ensure_supplies", lambda *_a, **_k: TaskResult.ok())
    monkeypatch.setattr(
        nether_prep, "_read_state_with_retry",
        lambda *_a, **_k: ({"block_position": {"x": 0, "y": 64, "z": 0}}, None),
    )
    monkeypatch.setattr(
        nether_prep, "build_nether_portal",
        lambda *_a: built.append("built") or True,
    )
    monkeypatch.setattr(nether_prep, "ignite_portal", lambda *_a: True)

    assert handler._prepare_portal(client, state) is True
    assert built, "with no portal nearby the bot must still build one"


def _lit_portal_transport(base_y=64, height=3):
    """Transport whose portal interior spans base_y .. base_y+height-1."""
    t = PortalTransport()
    for dy in range(height):
        for dx in (0, 1):
            t.blocks[(-156, base_y + dy, -278 + dx)] = "minecraft:nether_portal"
    return t


def test_enter_portal_targets_the_portal_base_not_its_middle(monkeypatch):
    """radius 0 is a Baritone GoalBlock: the bot must stand exactly there.

    A portal interior is 3 blocks tall and find_nearest_portal returns
    whichever block the scan hit first, so targeting the middle or top asks
    the bot to stand in mid-air -- unreachable, so it never arrives. Live
    2026-08-02: Bot07 aimed at y=65 and Bot17 at y=66 of a y=64..66 portal
    and both burned all three retries 8-14 blocks short.
    """
    transport = _lit_portal_transport(base_y=64, height=3)
    client = SimpleNamespace(transport=transport, mission=MissionStub())

    # Handed the MIDDLE block, as find_nearest_portal would return.
    assert nether.enter_portal(
        client, (-156, 65, -278), target_dimension="minecraft:the_nether", timeout=1
    )
    goto = [p for route, p in transport.calls if route == "goto"][-1]
    assert goto["y"] == 64, f"expected the portal base, got y={goto['y']}"
    assert (goto["x"], goto["z"]) == (-156, -278)


def test_enter_portal_targets_the_base_when_handed_the_top_block(monkeypatch):
    transport = _lit_portal_transport(base_y=64, height=3)
    client = SimpleNamespace(transport=transport, mission=MissionStub())

    assert nether.enter_portal(
        client, (-156, 66, -278), target_dimension="minecraft:the_nether", timeout=1
    )
    goto = [p for route, p in transport.calls if route == "goto"][-1]
    assert goto["y"] == 64, f"expected the portal base, got y={goto['y']}"


def test_enter_portal_leaves_an_already_basal_target_alone(monkeypatch):
    transport = _lit_portal_transport(base_y=64, height=3)
    client = SimpleNamespace(transport=transport, mission=MissionStub())

    assert nether.enter_portal(
        client, (-156, 64, -278), target_dimension="minecraft:the_nether", timeout=1
    )
    goto = [p for route, p in transport.calls if route == "goto"][-1]
    assert goto["y"] == 64


def test_portal_standing_level_stops_at_the_floor():
    """Must not walk below the portal into whatever is underneath it."""
    transport = _lit_portal_transport(base_y=70, height=3)
    transport.blocks[(-156, 69, -278)] = "minecraft:obsidian"
    client = SimpleNamespace(transport=transport)
    assert nether._portal_standing_level(client, (-156, 72, -278)) == 70


def _portal_state(tmp_path, locations):
    state = StateManager(checkpoint_dir=tmp_path)
    for loc in locations:
        state.add_location("nether_portal", loc["x"], loc["y"], loc["z"],
                           dimension=loc["dimension"], tags=loc.get("tags"))
    return state


def test_enter_nether_tries_every_persisted_portal(monkeypatch, tmp_path):
    """A dead coordinate at the head of the list must not fail the objective.

    Fleet-observed sightings are stored with an "active" tag but are never
    verified. Live 2026-08-03: Bot07 logged "Refusing to enter inactive
    portal" 120 times against (-144, 69, -278) -- not a portal at all --
    while the real portal at (-156, 64, -278) sat third in its own list.
    """
    handler = nether_prep.NetherAndBlazeHandler()
    transport = PortalTransport()
    client = SimpleNamespace(transport=transport, mission=MissionStub())
    state = _portal_state(tmp_path, [
        {"x": -144, "y": 69, "z": -278, "dimension": "overworld",
         "tags": ["active", "entry", "shared", "observed"]},   # phantom, first
        {"x": -156, "y": 64, "z": -278, "dimension": "overworld",
         "tags": ["active", "entry"]},                          # the real one
    ])

    tried = []

    def fake_enter(_client, portal, *, target_dimension, timeout):
        tried.append(portal)
        return portal == (-156, 64, -278)      # only the real portal works

    monkeypatch.setattr(nether_prep, "enter_portal", fake_enter)
    monkeypatch.setattr(nether_prep, "find_nearest_portal", lambda *_a: (-19, 85, -34))
    monkeypatch.setattr(handler, "_current_dimension", lambda _c: "minecraft:overworld")

    assert handler._enter_nether(client, state) is True
    assert (-156, 64, -278) in tried, f"never tried the live portal: {tried}"
    assert len(tried) >= 2, "must fall through the dead coordinate"


def test_enter_nether_fails_only_when_every_portal_is_dead(monkeypatch, tmp_path):
    handler = nether_prep.NetherAndBlazeHandler()
    client = SimpleNamespace(transport=PortalTransport(), mission=MissionStub())
    state = _portal_state(tmp_path, [
        {"x": -144, "y": 69, "z": -278, "dimension": "overworld", "tags": ["active"]},
        {"x": -141, "y": 68, "z": -279, "dimension": "overworld", "tags": ["shared"]},
    ])
    tried = []
    monkeypatch.setattr(
        nether_prep, "enter_portal",
        lambda _c, portal, **_k: tried.append(portal) or False,
    )
    monkeypatch.setattr(handler, "_current_dimension", lambda _c: "minecraft:overworld")

    assert handler._enter_nether(client, state) is False
    assert len(tried) == 2, "must exhaust every candidate before failing"


def test_verified_active_portals_are_tried_before_second_hand_sightings(tmp_path):
    state = _portal_state(tmp_path, [
        {"x": -144, "y": 69, "z": -278, "dimension": "overworld",
         "tags": ["shared", "observed"]},
        {"x": -156, "y": 64, "z": -278, "dimension": "overworld",
         "tags": ["active", "entry"]},
    ])
    order = nether_prep.NetherAndBlazeHandler._persisted_portal_candidates(
        state, "overworld"
    )
    assert order[0] == (-156, 64, -278), f"active portal must sort first: {order}"


def test_all_fleet_observed_portals_are_recorded_not_just_the_first(monkeypatch, tmp_path):
    """Shared sightings are unverified; one dead entry must not hide a live one.

    periodic_visible_scan keeps landmarks for portals that no longer exist.
    Returning on the first sighting persisted a phantom and left the real
    portal unknown to the entry step. Live 2026-08-03: Bot07 and Bot16 each
    logged 96 "Refusing to enter inactive portal" against (-144, 69, -278)
    while (-156, 64, -278) sat in the same shared catalog.
    """
    handler = nether_prep.NetherAndBlazeHandler()
    client = SimpleNamespace(transport=PortalTransport(), mission=MissionStub())
    state = StateManager(checkpoint_dir=tmp_path)

    monkeypatch.setattr(nether_prep, "verify_portal", lambda *_a, **_k: False)
    monkeypatch.setattr(nether_prep, "find_nearest_portal", lambda *_a: None)
    monkeypatch.setattr(
        nether_prep, "catalog_for",
        lambda _c, _s: SimpleNamespace(list_landmarks=lambda _cat: [
            {"x": -144, "y": 69, "z": -278, "dimension": "minecraft:overworld"},
            {"x": -156, "y": 64, "z": -278, "dimension": "minecraft:overworld"},
            {"x": -19, "y": 85, "z": -35, "dimension": "minecraft:the_nether"},
        ]),
    )

    assert handler._prepare_portal(client, state) is True
    persisted = handler._persisted_portal_candidates(state, "overworld")
    assert (-156, 64, -278) in persisted, f"live portal not persisted: {persisted}"
    assert (-144, 69, -278) in persisted, "phantom should still be a candidate to try"
    assert len(persisted) == 2, persisted


class _LazyChunkTransport(PortalTransport):
    """A portal the bot cannot see until it walks into render distance.

    Models the live failure exactly: get_block over an unloaded chunk answers
    void_air, and find_blocks -- which only scans loaded chunks -- returns
    nothing, so nothing about the portal is knowable from a distance.
    """

    def __init__(self, portal_base):
        super().__init__()
        self.portal_base = portal_base
        self.position = {"x": portal_base[0] + 98, "y": 70, "z": portal_base[2]}

    @property
    def _loaded(self):
        return abs(self.position["x"] - self.portal_base[0]) <= 16

    def dispatch(self, route, payload, **_kwargs):
        if route in ("get_block", "find_blocks") and self._loaded:
            x, y, z = self.portal_base
            for dy in (0, 1, 2):
                self.blocks[(x, y + dy, z)] = "minecraft:nether_portal"
            self.found_blocks = [{"x": x, "y": y + dy, "z": z} for dy in (0, 1, 2)]
        elif route == "get_block" and not self._loaded:
            self.calls.append((route, dict(payload)))
            return {"id": "minecraft:void_air"}
        return super().dispatch(route, payload, **_kwargs)


def test_enter_portal_approaches_a_portal_in_an_unloaded_chunk():
    """void_air means 'chunk not loaded', not 'portal is gone'.

    Refusing on void_air was self-defeating: a bot cannot load the chunk
    without walking there, so a portal it had never stood near could never
    be entered. Live 2026-08-03: Bot07 sat 98 blocks from a verified-live
    portal, read all three blocks as void_air, and refused 96 times.
    """
    transport = _LazyChunkTransport(portal_base=(-156, 64, -278))
    client = SimpleNamespace(transport=transport, mission=MissionStub())

    assert nether.enter_portal(
        client, (-156, 64, -278), target_dimension="minecraft:the_nether", timeout=1
    )
    goto = [p for route, p in transport.calls if route == "goto"]
    assert goto, "must path toward an unloaded portal instead of refusing"
    # First move is an approach (GoalNear); only once the chunk is loaded and
    # the interior is visible does it commit to a radius-0 GoalBlock.
    assert goto[0]["radius"] > 0, f"first move must be an approach: {goto[0]}"
    assert (goto[-1]["x"], goto[-1]["y"], goto[-1]["z"]) == (-156, 64, -278)
    assert goto[-1]["radius"] == 0


def test_enter_portal_targets_the_interior_not_the_named_frame_block():
    """The persisted coordinate can name a frame corner, not the purple middle.

    The old code guessed the interior as (x+1, y+1, z), which assumes an axis
    and a corner convention. On a Z-axis portal that lands in obsidian, and a
    radius-0 GoalBlock there is unsatisfiable -- Baritone drops it without
    moving. Live 2026-08-03: Bot17 sat 13 blocks from a live portal for 150s
    at zero velocity, burning every retry.
    """
    transport = PortalTransport()
    named = (-156, 66, -278)          # a frame block the catalog knows about
    transport.blocks[named] = "minecraft:obsidian"
    for y in (64, 65, 66):            # the real interior, one column over
        transport.blocks[(-156, y, -277)] = "minecraft:nether_portal"
    transport.found_blocks = [
        {"x": -156, "y": y, "z": -277} for y in (64, 65, 66)
    ]
    client = SimpleNamespace(transport=transport, mission=MissionStub())

    assert nether.enter_portal(
        client, named, target_dimension="minecraft:the_nether", timeout=1
    )
    goto = [p for route, p in transport.calls if route == "goto"]
    assert (goto[-1]["x"], goto[-1]["y"], goto[-1]["z"]) == (-156, 64, -277), (
        f"must stand in the portal's lowest interior block, got {goto[-1]}"
    )


def test_enter_portal_still_refuses_a_genuinely_absent_portal():
    """A loaded chunk showing plain air is real evidence the portal is gone."""
    transport = PortalTransport()
    transport.blocks[(-144, 69, -278)] = "minecraft:stone"   # loaded, not a portal
    client = SimpleNamespace(transport=transport, mission=MissionStub())

    assert nether.enter_portal(
        client, (-144, 69, -278), target_dimension="minecraft:the_nether", timeout=1
    ) is False
    assert not [p for route, p in transport.calls if route == "goto"], \
        "must not walk to a coordinate proven not to hold a portal"


def test_fortress_search_pushes_outward_when_the_bot_stops_moving(monkeypatch):
    """Scanning is not searching.

    find_nether_fortress dispatched explore once and then rescanned on a
    timer. Baritone ends explore on its own, and nothing noticed, so the bot
    stood still re-reading the same 32-block sphere for the rest of the
    600s timeout. Live 2026-08-03: Bot07, Bot16 and Bot18 each held exactly
    one coordinate for 210s inside this loop.
    """

    class StuckTransport(PortalTransport):
        """A bot whose explore has quietly ended: it never moves itself."""

        def __init__(self):
            super().__init__()
            self.dimension = "minecraft:the_nether"
            self.position = {"x": 0, "y": 70, "z": 0}
            self.explores = []
            self.gotos = []

        def dispatch(self, route, payload, **kwargs):
            if route == "explore":
                self.explores.append(dict(payload))
                return {"started": True}
            if route == "goto":
                self.gotos.append(dict(payload))
                return {"started": True}   # deliberately does NOT move
            if route == "find_blocks":
                return {"found": []}
            return super().dispatch(route, payload, **kwargs)

    transport = StuckTransport()
    client = SimpleNamespace(transport=transport, mission=MissionStub())
    monkeypatch.setattr(nether.time, "sleep", lambda _seconds: None)
    # Drive the scan clock rather than waiting out a real 60s timeout.
    clock = {"now": 0.0}

    def fake_time():
        clock["now"] += 3.0
        return clock["now"]

    monkeypatch.setattr(nether.time, "time", fake_time)

    assert nether.find_nether_fortress(client, timeout=60) is None

    assert len(transport.explores) > 1, (
        "a stalled search must re-issue explore, not rescan one spot forever"
    )
    assert transport.gotos, "must commit to a waypoint to leave the dead area"
    headings = {
        (
            (g["x"] > 0) - (g["x"] < 0),
            (g["z"] > 0) - (g["z"] < 0),
        )
        for g in transport.gotos
    }
    assert len(headings) > 1, f"must try more than one direction: {transport.gotos}"


def test_marooned_bot_escapes_instead_of_cycling_headings_forever(monkeypatch):
    """No bearing is walkable off a ledge; rotating them is not a search.

    Being stuck is a property of the terrain, not the heading. Live
    2026-08-03: Bot16 and Bot18 sat on the same glowstone blob at
    (-132, 78, -7) in a basalt delta -- air underneath, air on every side --
    and cycled all eight compass headings every 20 seconds for over an hour
    without moving a block.
    """

    class LedgeTransport(PortalTransport):
        def __init__(self):
            super().__init__()
            self.dimension = "minecraft:the_nether"
            self.position = {"x": -132, "y": 78, "z": -7}

        def dispatch(self, route, payload, **kwargs):
            if route in ("goto", "explore"):
                return {"started": True}      # nothing is walkable
            if route == "find_blocks":
                return {"found": []}
            return super().dispatch(route, payload, **kwargs)

    transport = LedgeTransport()
    client = SimpleNamespace(transport=transport, mission=MissionStub())
    monkeypatch.setattr(nether.time, "sleep", lambda _seconds: None)
    clock = {"now": 0.0}

    def fake_time():
        clock["now"] += 3.0
        return clock["now"]

    monkeypatch.setattr(nether.time, "time", fake_time)

    escapes = []

    def fake_egress(_client, state, **kwargs):
        escapes.append(kwargs)
        return None      # no progress -> must not spin on it forever

    monkeypatch.setattr(nether, "try_lower_surface_egress", fake_egress)

    nether.find_nether_fortress(client, timeout=600)

    assert escapes, (
        "a bot that refuses every heading must be treated as marooned, "
        "not handed a ninth heading"
    )
    assert escapes[0].get("minimum_altitude") == 0, (
        "the Overworld high-shelf altitude guard would refuse to help at y=78"
    )


def test_marooned_descent_is_pressed_until_it_stops_making_progress(monkeypatch):
    """One escape call per eight-heading circuit is one block per few minutes.

    Live 2026-08-03: Bot16's descent bought a single block, then control
    returned to the heading rotation and the counter reset, so it inched one
    block in five minutes while still marooned.
    """

    class LedgeTransport(PortalTransport):
        def __init__(self):
            super().__init__()
            self.dimension = "minecraft:the_nether"
            self.position = {"x": -132, "y": 78, "z": -7}

        def dispatch(self, route, payload, **kwargs):
            if route in ("goto", "explore"):
                return {"started": True}
            if route == "find_blocks":
                return {"found": []}
            return super().dispatch(route, payload, **kwargs)

    transport = LedgeTransport()
    client = SimpleNamespace(transport=transport, mission=MissionStub())
    monkeypatch.setattr(nether.time, "sleep", lambda _seconds: None)
    clock = {"now": 0.0}

    def fake_time():
        clock["now"] += 3.0
        return clock["now"]

    monkeypatch.setattr(nether.time, "time", fake_time)

    calls = {"n": 0}

    def descending_egress(_client, _state, **_kwargs):
        calls["n"] += 1
        if calls["n"] > 4:
            return None                    # ran out of room to descend
        transport.position = {
            **transport.position,
            "y": transport.position["y"] - 1,
        }
        return (-132, transport.position["y"], -7)

    monkeypatch.setattr(nether, "try_lower_surface_egress", descending_egress)

    nether.find_nether_fortress(client, timeout=600)

    assert calls["n"] > 1, (
        "a descent that is still making progress must be pressed again "
        "immediately, not after another full heading circuit"
    )


def test_nether_egress_looks_for_blocks_that_exist_in_the_nether():
    """Overworld ground blocks do not occur in the Nether.

    try_lower_surface_egress searched for grass/dirt/stone, found nothing,
    and silently no-op'd for a genuinely marooned bot.
    """
    from baritone_client.common import surface_egress

    nether_blocks = surface_egress._surface_blocks_for(
        {"dimension": "minecraft:the_nether"}
    )
    assert "minecraft:netherrack" in nether_blocks
    assert "minecraft:basalt" in nether_blocks
    assert not any("grass" in block for block in nether_blocks)

    overworld = surface_egress._surface_blocks_for({"dimension": "minecraft:overworld"})
    assert "minecraft:grass_block" in overworld


def test_column_descent_helps_a_marooned_bot_below_the_overworld_shelf_height(
    monkeypatch, advancing_clock
):
    """The y<96 gate refused the one escape that fits the situation.

    supported_column_descent exists precisely for "void underfoot, blocks in
    inventory": it places a block beneath the floor, then removes the floor,
    stepping down safely. Live 2026-08-03: Bot16 stood on a single glowstone
    block at y=78 in the Nether over 28 blocks of pure air, carrying 82
    cobblestone, and the altitude gate returned None before looking at
    anything. Whether this is the right escape depends on the void, not on
    the absolute height.
    """
    from baritone_client.common import shelf_escape

    monkeypatch.setattr(shelf_escape, "time", advancing_clock())

    class VoidLedgeTransport(PortalTransport):
        def __init__(self):
            super().__init__()
            self.dimension = "minecraft:the_nether"
            self.position = {"x": -132, "y": 78, "z": -7}
            self.broke = []

        def dispatch(self, route, payload, **kwargs):
            if route == "get_block":
                key = (payload["x"], payload["y"], payload["z"])
                if key == (-132, 77, -7):
                    return {"id": "minecraft:glowstone"}   # the one floor block
                return {"id": "minecraft:air"}             # void everywhere else
            if route == "dig_block":
                self.broke.append(dict(payload))
                self.position = {**self.position, "y": self.position["y"] - 1}
                return {"broken": True}
            if route == "cancel":
                return {}
            if route == "get_inventory":
                return {"inventory": [{"slot": 0, "id": "minecraft:cobblestone", "count": 82}]}
            return super().dispatch(route, payload, **kwargs)

    transport = VoidLedgeTransport()
    client = SimpleNamespace(transport=transport, mission=MissionStub())
    from baritone_client.common import automation_utils

    placed = []
    monkeypatch.setattr(
        automation_utils,
        "place_block",
        lambda _c, x, y, z, item: (placed.append((x, y, z, item)), True)[1],
    )

    # The default gate refuses outright at y=78 ...
    assert (
        shelf_escape.supported_column_descent(
            client, {"block_position": dict(transport.position)}
        )
        is None
    )
    # ... but a caller that has proven the bot is marooned gets a descent.
    transport.position = {"x": -132, "y": 78, "z": -7}
    result = shelf_escape.supported_column_descent(
        client,
        {"block_position": dict(transport.position)},
        minimum_altitude=0,
        target_y=70,
        max_steps=12,
    )
    assert result is not None, "a marooned bot over a void must be able to descend"
    assert transport.broke, "descent must actually remove the floor it stands on"
    assert placed, "each step must place a support block before removing the floor"


def test_blaze_hunt_widens_when_local_fortress_cells_are_exhausted(monkeypatch):
    class ExhaustedFortressTransport(PortalTransport):
        def __init__(self):
            super().__init__()
            self.dimension = "minecraft:the_nether"
            self.position = {"x": 0, "y": 70, "z": 0}

        def dispatch(self, route, payload, **kwargs):
            if route == "find_blocks":
                return {
                    "found": [
                        {
                            "x": 1,
                            "y": 70,
                            "z": 1,
                            "block": "minecraft:nether_bricks",
                        }
                    ]
                }
            if route in ("goto", "explore"):
                self.calls.append((route, dict(payload)))
                return {"started": True}
            return super().dispatch(route, payload, **kwargs)

    transport = ExhaustedFortressTransport()
    client = SimpleNamespace(transport=transport)
    monkeypatch.setattr(nether, "count_item", lambda *_args: 0)
    monkeypatch.setattr(nether.time, "sleep", lambda _seconds: None)
    clock = {"now": 0.0}

    def fake_time():
        clock["now"] += 1.0
        return clock["now"]

    monkeypatch.setattr(nether.time, "time", fake_time)

    assert nether.hunt_blazes(client, target_count=6, timeout=20) == 0

    widened = [
        payload
        for route, payload in transport.calls
        if route == "goto" and payload.get("radius") == 12
    ]
    assert len(widened) > 1
    assert len({(item["x"], item["z"]) for item in widened}) > 1


def test_blaze_hunt_widens_when_local_fortress_scan_is_empty(monkeypatch):
    class EmptyFortressTransport(PortalTransport):
        def __init__(self):
            super().__init__()
            self.dimension = "minecraft:the_nether"
            self.position = {"x": -205, "y": 87, "z": 82}

        def dispatch(self, route, payload, **kwargs):
            if route == "find_blocks":
                return {"found": []}
            if route in ("goto", "explore"):
                self.calls.append((route, dict(payload)))
                return {"started": True}
            return super().dispatch(route, payload, **kwargs)

    transport = EmptyFortressTransport()
    client = SimpleNamespace(transport=transport)
    monkeypatch.setattr(nether, "count_item", lambda *_args: 0)
    monkeypatch.setattr(nether.time, "sleep", lambda _seconds: None)
    clock = {"now": 0.0}

    def fake_time():
        clock["now"] += 1.0
        return clock["now"]

    monkeypatch.setattr(nether.time, "time", fake_time)

    assert nether.hunt_blazes(client, target_count=6, timeout=20) == 0

    widened = [
        payload
        for route, payload in transport.calls
        if route == "goto" and payload.get("radius") == 12
    ]
    assert widened
    assert all(payload["y"] == 87 for payload in widened)


def test_blaze_spawner_camp_holds_position_and_reanchors(monkeypatch):
    """An empty first scan must not walk out of spawner activation range."""
    client = SimpleNamespace(transport=PortalTransport())
    client.transport.blocks[(-332, 70, 104)] = "minecraft:spawner"
    rods = {"count": 0}
    travels = []
    hunts = []

    monkeypatch.setattr(
        nether,
        "_travel_to",
        lambda _client, position, **kwargs: (
            travels.append((position, kwargs)), True
        )[1],
    )
    monkeypatch.setattr(
        blaze_spawners,
        "count_item",
        lambda *_args: rods["count"],
    )
    monkeypatch.setattr(blaze_spawners, "has_full_armor", lambda *_a, **_k: True)

    def fake_hunt(_client, **kwargs):
        hunts.append(kwargs)
        rods["count"] = 2 if rods["count"] == 0 else 6
        return TaskResult.ok("bounded spawner hunt")

    monkeypatch.setattr(blaze_spawners, "hunt_mobs", fake_hunt)

    assert nether._camp_blaze_spawner(
        client,
        (-332, 70, 104),
        target_count=6,
        deadline=nether.time.time() + 120,
    ) is True
    assert len(hunts) == 2
    assert all(call["explore_when_empty"] is False for call in hunts)
    assert all(call["search_radius"] == 20 for call in hunts)
    assert all(call["heal_threshold"] == 16.0 for call in hunts)
    assert all(call["no_retreat"] is True for call in hunts)
    assert all(call["recover_after_combat"] is True for call in hunts)
    assert all(call["recovery_anchor"] != (-332, 70, 104) for call in hunts)
    assert all(
        20 <= sum(
            (call["recovery_anchor"][index] - (-332, 70, 104)[index]) ** 2
            for index in range(3)
        ) ** 0.5 <= 25
        for call in hunts
    )
    assert len(travels) == 2, "combat must re-anchor at the spawner"
    assert all(call[1]["radius"] == 8 for call in travels)


def test_blaze_hunt_fails_fast_on_peaceful(monkeypatch, caplog):
    client = SimpleNamespace(
        transport=SimpleNamespace(
            dispatch=lambda route, _payload: (
                {"difficulty": "peaceful"} if route == "get_state" else {}
            )
        )
    )
    monkeypatch.setattr(nether, "count_item", lambda *_args: 3)
    monkeypatch.setattr(
        nether,
        "_shared_blaze_spawners",
        lambda *_args: pytest.fail("Peaceful must fail before spawner travel"),
    )

    assert nether.hunt_blazes(client, target_count=6, timeout=600) == 3
    assert "world difficulty is Peaceful" in caplog.text


def test_blaze_hunt_reads_difficulty_from_world_info(monkeypatch, caplog):
    calls = []

    def dispatch(route, _payload):
        calls.append(route)
        if route == "get_state":
            return {"dimension": "minecraft:the_nether"}
        if route == "get_world_info":
            return {"difficulty": "peaceful"}
        return {}

    client = SimpleNamespace(transport=SimpleNamespace(dispatch=dispatch))
    monkeypatch.setattr(nether, "count_item", lambda *_args: 3)
    monkeypatch.setattr(
        nether,
        "_shared_blaze_spawners",
        lambda *_args: pytest.fail("Peaceful must fail before spawner travel"),
    )

    assert nether.hunt_blazes(client, target_count=6, timeout=600) == 3
    assert "get_world_info" in calls
    assert "world difficulty is Peaceful" in caplog.text


def test_blaze_spawner_camp_rejects_an_under_armored_bot(monkeypatch):
    client = SimpleNamespace(transport=PortalTransport())
    hunts = []
    monkeypatch.setattr(blaze_spawners, "has_full_armor", lambda *_a, **_k: False)
    monkeypatch.setattr(
        blaze_spawners,
        "hunt_mobs",
        lambda *_a, **_k: hunts.append(True),
    )

    assert nether._camp_blaze_spawner(
        client,
        (-332, 70, 104),
        target_count=6,
        deadline=nether.time.time() + 120,
    ) is False
    assert hunts == []


def test_unreachable_shared_spawner_never_starts_hunt(monkeypatch):
    client = SimpleNamespace(transport=PortalTransport())
    hunts = []
    monkeypatch.setattr(nether, "_travel_to", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        blaze_spawners,
        "hunt_mobs",
        lambda *_args, **_kwargs: hunts.append(True),
    )

    assert nether._camp_blaze_spawner(
        client,
        (-332, 70, 104),
        target_count=6,
        deadline=nether.time.time() + 120,
    ) is False
    assert hunts == []


def test_shared_nether_spawners_are_nearest_first(monkeypatch):
    client = SimpleNamespace(transport=PortalTransport())
    client.transport.dimension = "minecraft:the_nether"
    client.transport.position = {"x": -300, "y": 70, "z": 100}

    class Catalog:
        def list_landmarks(self, category, *, dimension):
            assert dimension == "minecraft:the_nether"
            if category == "blaze_spawner":
                return []
            assert category == "spawner"
            return [
                {"x": 138, "y": 39, "z": -226},
                {"x": -332, "y": 70, "z": 104},
                {"x": -287, "y": 76, "z": 78},
            ]

    from baritone_client.common import storage_catalog

    monkeypatch.setattr(storage_catalog, "catalog_for", lambda *_args: Catalog())
    candidates = nether._shared_blaze_spawners(client, object())
    assert candidates[:2] == [(-287, 76, 78), (-332, 70, 104)]


class _BlazeSearchTransport(PortalTransport):
    """A Nether bot that walks toward its goal at a realistic pace."""

    def __init__(self, fortress_blocks):
        super().__init__()
        self.dimension = "minecraft:the_nether"
        self.position = {"x": 0, "y": 64, "z": 0}
        self.fortress_blocks = fortress_blocks
        self.gotos = []
        self.goal = None

    def dispatch(self, route, payload, **kwargs):
        if route == "goto":
            self.gotos.append(dict(payload))
            self.goal = (payload["x"], payload["y"], payload["z"])
            return {"started": True}
        if route == "explore":
            return {"started": True}
        if route == "get_state":
            # Creep one block per poll toward the active goal.
            if self.goal:
                for axis, index in (("x", 0), ("z", 2)):
                    delta = self.goal[index] - self.position[axis]
                    if delta:
                        self.position[axis] += 1 if delta > 0 else -1
            return {
                "dimension": self.dimension,
                "block_position": dict(self.position),
            }
        if route == "get_inventory":
            return {"inventory": []}
        if route == "find_blocks":
            return {
                "found": [
                    {"x": x, "y": y, "z": z, "block": "minecraft:nether_bricks"}
                    for x, y, z in self.fortress_blocks
                ]
            }
        return super().dispatch(route, payload, **kwargs)


def test_blaze_frontier_targets_the_nearest_unvisited_cell(monkeypatch):
    """Targeting the farthest frontier block guarantees it is never reached.

    The cell is only marked explored once the bot stands in it, so a target
    it cannot reach inside one scan interval is re-chosen forever. Live
    2026-08-03: Bot17 re-issued a goto to (-260, 47, 131) every 10 seconds
    and covered 10 blocks in three minutes.
    """
    transport = _BlazeSearchTransport([(8, 64, 0), (400, 64, 0)])
    client = SimpleNamespace(transport=transport, mission=MissionStub())
    monkeypatch.setattr(nether.time, "sleep", lambda _seconds: None)

    nether._advance_blaze_frontier(
        client,
        transport.fortress_blocks,
        set(),
        [],
        current=(0, 64, 0),
        center=(0, 64, 0),
        frontier_index=0,
    )

    assert transport.gotos, "must issue a goto"
    assert (transport.gotos[0]["x"], transport.gotos[0]["z"]) == (8, 0), (
        f"must head for the nearest unvisited cell, got {transport.gotos[0]}"
    )


def test_blaze_frontier_waits_for_arrival_before_retargeting(monkeypatch):
    """A goto followed by sleep(3) is replaced before the bot has walked.

    Live 2026-08-03: Bot07 issued a new frontier goto every five seconds and
    covered 30 blocks in three minutes; Bot18 raced from ring 14 to 15 through
    six coordinates in 30 seconds without arriving at any of them.
    """
    transport = _BlazeSearchTransport([(20, 64, 0)])
    client = SimpleNamespace(transport=transport, mission=MissionStub())
    monkeypatch.setattr(nether.time, "sleep", lambda _seconds: None)

    nether._advance_blaze_frontier(
        client,
        transport.fortress_blocks,
        set(),
        [],
        current=(0, 64, 0),
        center=(0, 64, 0),
        frontier_index=0,
    )

    assert len(transport.gotos) == 1, "one destination per advance, not a stream"
    # It must actually be there when it hands control back, otherwise the next
    # scan re-reads the same starting area.
    assert abs(transport.position["x"] - 20) <= 6, (
        f"must arrive before advancing, stopped at {transport.position}"
    )


def test_unreachable_frontier_cell_is_retired_not_retried_forever(monkeypatch):
    """An unreachable goal must be abandoned, not re-chosen every pass.

    A cell is normally marked explored by standing in it, so a frontier target
    the bot cannot reach never leaves the candidate list. Live 2026-08-03:
    Bot07 logged "Advancing blaze search to fortress frontier (709, 74, 550)"
    every ten seconds at precisely zero movement.
    """

    class UnreachableTransport(_BlazeSearchTransport):
        def dispatch(self, route, payload, **kwargs):
            if route == "get_state":
                return {                      # never moves, whatever we ask
                    "dimension": self.dimension,
                    "block_position": dict(self.position),
                }
            return super().dispatch(route, payload, **kwargs)

    blocks = [(709, 74, 550), (400, 64, 0)]
    transport = UnreachableTransport(blocks)
    client = SimpleNamespace(transport=transport, mission=MissionStub())
    monkeypatch.setattr(nether.time, "sleep", lambda _seconds: None)

    explored = set()
    first_target = None
    for _ in range(3):
        nether._advance_blaze_frontier(
            client,
            blocks,
            explored,
            [],
            current=(700, 74, 545),
            center=(700, 74, 545),
            frontier_index=0,
        )
        if first_target is None:
            first_target = (
                transport.gotos[0]["x"],
                transport.gotos[0]["y"],
                transport.gotos[0]["z"],
            )

    targets = [(g["x"], g["y"], g["z"]) for g in transport.gotos]
    assert targets[0] == first_target
    assert targets.count(first_target) == 1, (
        f"an unreachable cell must be retired after one attempt: {targets}"
    )
    assert len(set(targets)) > 1, "the search must move on to another cell"


def test_nether_travel_escapes_a_marooned_pillar_before_failing(monkeypatch):
    """A stationary, idle bot gets one bounded lower-surface recovery."""

    class MaroonedTransport(_BlazeSearchTransport):
        def __init__(self):
            super().__init__([])
            self.recovered = False

        def dispatch(self, route, payload, **kwargs):
            if route == "get_state" and not self.recovered:
                return {
                    "dimension": self.dimension,
                    "block_position": dict(self.position),
                    "is_pathing": False,
                }
            return super().dispatch(route, payload, **kwargs)

    transport = MaroonedTransport()
    client = SimpleNamespace(transport=transport)
    recoveries = []
    monkeypatch.setattr(nether.time, "sleep", lambda _seconds: None)

    def recover(_client, state, **kwargs):
        recoveries.append((state, kwargs))
        transport.recovered = True
        return (0, 60, 0)

    monkeypatch.setattr(nether, "try_lower_surface_egress", recover)

    assert nether._travel_to(
        client,
        (12, 64, 0),
        radius=2,
        timeout=45,
    ) is True
    assert len(recoveries) == 1
    assert recoveries[0][1]["minimum_altitude"] == 0
    assert len(transport.gotos) == 2, "the original goal must be re-issued"


def test_nether_travel_cancels_when_defense_intervenes():
    class Transport:
        def __init__(self):
            self.calls = []

        def dispatch(self, route, payload):
            self.calls.append((route, dict(payload)))
            if route == "get_state":
                return {
                    "health": 20,
                    "is_pathing": True,
                    "block_position": {"x": 0, "y": 64, "z": 0},
                }
            return {}

    transport = Transport()
    client = SimpleNamespace(transport=transport)

    assert not nether._travel_to(
        client,
        (20, 64, 0),
        radius=2,
        on_defense=lambda: True,
    )
    assert transport.calls[-1] == ("cancel", {})


def test_nether_travel_propagates_player_death():
    class Transport:
        def __init__(self):
            self.calls = []

        def dispatch(self, route, payload):
            self.calls.append((route, dict(payload)))
            if route == "get_state":
                return {
                    "health": 0,
                    "is_dead": True,
                    "block_position": {"x": 0, "y": 64, "z": 0},
                }
            return {}

    transport = Transport()
    client = SimpleNamespace(transport=transport)

    with pytest.raises(PlayerDeathDetected):
        nether._travel_to(client, (20, 64, 0), radius=2)
    assert transport.calls[-1] == ("cancel", {})


def test_portal_approach_leaves_water_before_a_long_overland_route(monkeypatch):
    """Baritone will not route hundreds of blocks starting from deep water.

    The existing surface helpers only run in drowning contexts, so a bot
    floating at full health never reaches them and every portal candidate
    burns its whole timeout without a step. Live 2026-08-03: Bot16 sat
    submerged at (406, 62, -22), water on all four sides, full health, 617
    blocks from its portal, at exactly zero movement.
    """
    from baritone_client.common import surface_recovery

    transport = PortalTransport()
    transport.position = {"x": 406, "y": 62, "z": -22}
    client = SimpleNamespace(transport=transport, mission=MissionStub())
    monkeypatch.setattr(nether.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        surface_recovery, "position_is_aquatic", lambda _c, _p: True
    )

    rescued = []

    def fake_reach_dry_surface(_client, **kwargs):
        rescued.append(kwargs["origin"])
        return (410, 64, -20)

    monkeypatch.setattr(
        surface_recovery, "reach_dry_surface", fake_reach_dry_surface
    )

    nether._approach_and_relocate(client, (-156, 64, -278))

    assert rescued, "must get onto land before asking for a 617-block route"
    assert rescued[0] == (406, 62, -22)


def test_portal_approach_does_not_detour_when_already_on_land(monkeypatch):
    """The dry-land step must not add a detour to every ordinary approach."""
    from baritone_client.common import surface_recovery

    transport = PortalTransport()
    client = SimpleNamespace(transport=transport, mission=MissionStub())
    monkeypatch.setattr(nether.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        surface_recovery, "position_is_aquatic", lambda _c, _p: False
    )

    called = []
    monkeypatch.setattr(
        surface_recovery,
        "reach_dry_surface",
        lambda *_a, **_k: called.append(1),
    )

    nether._approach_and_relocate(client, (-156, 64, -278))

    assert not called, "a bot on dry land must head straight for the portal"


def test_armor_is_equipped_and_crafted_before_the_hunger_gate(monkeypatch):
    """A starving bot must still get its armor on.

    On 2026-08-06 all six bots sat at zero food. The hunger gate ran before
    any armor step, so three bots carrying unequipped armor and two holding
    40-66 iron ingots never reached the equip/craft code at all, and kept
    dying to ordinary hostiles at "only 0/4 armor pieces".
    """
    from baritone_client.automator.phases import nether_prep as np_mod

    order = []

    monkeypatch.setattr(
        np_mod, "equip_best_armor", lambda _c: order.append("equip_armor")
    )
    monkeypatch.setattr(
        np_mod, "equip_best_weapon", lambda _c: order.append("equip_weapon") or True
    )
    monkeypatch.setattr(
        np_mod.NetherAndBlazeHandler,
        "_craft_armor_from_carried_iron",
        staticmethod(lambda _c: order.append("craft_from_carried") or 0),
    )

    def hungry(*_a, **_k):
        order.append("hunger_gate")
        return False

    monkeypatch.setattr(np_mod, "eat_until_hunger", hungry)
    monkeypatch.setattr(np_mod, "acquire_emergency_food", lambda *_a, **_k: False)

    client = SimpleNamespace(
        transport=SimpleNamespace(
            dispatch=lambda *_a, **_k: {"dimension": "minecraft:overworld"}
        )
    )
    handler = np_mod.NetherAndBlazeHandler()
    monkeypatch.setattr(
        handler, "_nether_loadout_ready", lambda *_a, **_k: False
    )

    assert handler._ensure_nether_readiness(client, None) is False

    assert "equip_armor" in order, "starving bot never equipped carried armor"
    assert order.index("equip_armor") < order.index("hunger_gate"), (
        "armor was gated behind hunger; that is the fleet-wide deadlock"
    )
    assert order.index("craft_from_carried") < order.index("hunger_gate")


def test_portal_site_offsets_widen_to_the_measured_a1_worst_case():
    """A1 2026-09-05 needed radius 16 to clear its base clutter.

    The original search was six fixed points at one small radius and failed
    site selection on 100% of NETHER_AND_BLAZE attempts over an hour. A live
    probe against the bot's actual position found nothing pristine within
    radius 7 in any of 8 directions; the first clear pocket was radius 16.
    This pins the widened ring search so a future edit cannot silently shrink
    it back below that measured floor.
    """
    assert 16 in nether_prep._PORTAL_SITE_RADII, (
        "16 is the measured worst-case radius on live A1 -- it must stay covered"
    )
    assert max(nether_prep._PORTAL_SITE_RADII) >= 16
    assert len(nether_prep._PORTAL_SITE_ANGLES) == 8, "expects 8 compass directions"

    offsets = nether_prep._PORTAL_SITE_OFFSETS
    assert len(offsets) == len(nether_prep._PORTAL_SITE_RADII) * 8
    assert len(offsets) == len(set(offsets)), "no duplicate candidate origins"

    # Nearest-ring-first ordering matters: _choose_portal_site returns the
    # first match, so a base-adjacent site must never be skipped in favor of
    # a farther one just because of list order.
    radii_in_offset_order = [max(abs(dx), abs(dz)) for dx, dz in offsets]
    assert radii_in_offset_order == sorted(radii_in_offset_order), (
        "offsets must be sorted nearest-first, matching _lava_candidates"
    )


def test_choose_portal_site_returns_the_first_qualifying_ring():
    """_choose_portal_site must not overshoot to a farther valid site.

    The search exists to prefer nearby ground; once a nearer candidate passes
    the pristine/reachable check it must win even though farther candidates
    later in the list would also pass.
    """
    position = {"x": 0, "y": 64, "z": 0}
    snapshot = {"block_position": position}

    qualifying_offset = nether_prep._PORTAL_SITE_OFFSETS[10]
    qualifying_origin = (position["x"] + qualifying_offset[0], 64, position["z"] + qualifying_offset[1])

    def fake_check(_client, origin):
        return origin == qualifying_origin

    with mock.patch.object(
        nether_prep, "_portal_site_is_pristine_and_reachable", side_effect=fake_check
    ):
        chosen = nether_prep._choose_portal_site(client=None, snapshot=snapshot)

    assert chosen == qualifying_origin
