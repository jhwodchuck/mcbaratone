"""Focused route-contract tests for villager and XP progression."""

from types import SimpleNamespace

from baritone_client.automator.phases import villager as villager_phase
from baritone_client.automator.phases import xp_engine as xp_phase
from baritone_client.automator.resource_manager import ResourceManager
from baritone_client.automator.state_manager import Phase
from baritone_client.common import mob_farm, villager
from baritone_client.common.combat import entity_position


REGISTERED_ROUTES = {
    "cancel", "find_blocks", "get_block", "get_entities", "get_inventory",
    "get_state", "goto", "look_at", "select_slot", "throw_item",
}


def adult(entity_id, x):
    return {
        "id": entity_id,
        "uuid": f"adult-{entity_id}",
        "type": "minecraft:villager",
        "is_baby": False,
        "position": {"x": x, "y": 64, "z": 10},
    }


class RouteTransport:
    def __init__(self):
        self.calls = []
        self.bread = 6
        self.weapon = 1
        self.selected_slot = 0
        self.entity_queries = 0
        self.levels = [29, 29, 30]
        self.experience_totals = [100, 100, 105]
        self.bed_blocks = [
            {"x": 10 + index, "y": 64, "z": 12, "block": "minecraft:white_bed"}
            for index in range(6)
        ]

    def dispatch(self, route, payload, **_kwargs):
        assert route in REGISTERED_ROUTES, f"unsupported mocked route: {route}"
        self.calls.append((route, payload))
        if route == "get_inventory":
            return {
                "inventory": [
                    {"slot": 0, "id": "minecraft:bread", "count": self.bread},
                    {"slot": 1, "id": "minecraft:iron_sword", "count": self.weapon},
                ],
                "armor": [],
                "offhand": [],
                "selected_slot": self.selected_slot,
            }
        if route == "get_entities":
            self.entity_queries += 1
            entities = [adult(1, 10), adult(2, 12)]
            if self.entity_queries >= 2:
                entities.append({
                    "id": 3,
                    "uuid": "new-baby",
                    "type": "minecraft:villager",
                    "is_baby": True,
                    "position": {"x": 11, "y": 64, "z": 10},
                })
            return {"entities": entities}
        if route == "throw_item":
            self.bread -= 1
            return {"thrown": True, "all": False}
        if route == "select_slot":
            self.selected_slot = int(payload["slot"])
            return {"selected": True, "slot": self.selected_slot}
        if route == "find_blocks":
            if payload["blocks"] == ["minecraft:spawner"]:
                return {"found": [{"x": 20, "y": 40, "z": 20, "distance": 8.0}]}
            return {"found": list(self.bed_blocks)}
        if route == "get_block":
            return {"id": "minecraft:spawner"}
        if route == "get_state":
            level = self.levels.pop(0) if len(self.levels) > 1 else self.levels[0]
            total = (
                self.experience_totals.pop(0)
                if len(self.experience_totals) > 1
                else self.experience_totals[0]
            )
            return {
                "block_position": {"x": 20, "y": 42, "z": 20},
                "experience_level": level,
                "experience_total": total,
            }
        return {"accepted": True}


class DummyResources:
    def __init__(self, missing=None):
        self.missing = missing or {}

    def check_phase_requirements(self, _phase):
        return dict(self.missing)

    def get_summary(self):
        return {"inventory": {"minecraft:bread": 6}}


class DummyState:
    def __init__(self):
        self.custom_data = {}
        self.payloads = {}
        self.locations = []
        self.saved = []

    def add_location(self, category, x, y, z, **kwargs):
        self.locations.append((category, x, y, z, kwargs))

    def record_phase_payload(self, phase, payload):
        self.payloads[phase] = payload

    def save_checkpoint(self, inventory):
        self.saved.append(inventory)


def test_villager_helpers_use_registered_routes_and_observe_new_offspring(monkeypatch):
    transport = RouteTransport()
    client = SimpleNamespace(transport=transport)
    monkeypatch.setattr(villager, "goto", lambda *_args, **_kwargs: True)

    breeder = villager.build_villager_breeder(client, 11, 64, 10, required_beds=3)
    breeding = villager.start_villager_multiplication(
        client, timeout=0.01, poll_interval=0.001
    )

    assert breeder["verified"] is True
    assert len(breeder["bed_blocks"]) == 6
    assert breeding["offspring_uuid"] == "new-baby"
    assert breeding["bread_consumed"] == 6
    assert all(route in REGISTERED_ROUTES for route, _payload in transport.calls)


def test_xp_helpers_use_real_spawner_and_live_experience(monkeypatch):
    transport = RouteTransport()
    client = SimpleNamespace(transport=transport)
    monkeypatch.setattr(mob_farm, "goto", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(mob_farm, "safe_combat", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        mob_farm,
        "get_nearby_entities",
        lambda *_args, **_kwargs: [
            {
                "id": 9,
                "type": "minecraft:zombie",
                "distance": 2.0,
                "position": {"x": 21, "y": 40, "z": 20},
            }
        ],
    )

    source = mob_farm.find_spawner(client, 128)
    result = mob_farm.grind_xp_at_location(
        client, *source, target_level=30, timeout=0.1, poll_interval=0.001
    )

    assert source == (20, 40, 20)
    assert result["achieved_level"] == 30
    assert result["location"] == [20, 40, 20]
    assert all(route in REGISTERED_ROUTES for route, _payload in transport.calls)


def test_handlers_persist_executor_verifiable_payloads(monkeypatch):
    villagers = [adult(1, 10), adult(2, 12)]
    monkeypatch.setattr(villager_phase, "get_nearby_entities", lambda *_args, **_kwargs: villagers)
    monkeypatch.setattr(
        villager_phase,
        "build_villager_breeder",
        lambda *_args, **_kwargs: {
            "location": [11, 64, 10],
            "required_beds": 3,
            "bed_blocks": [[10 + index, 64, 12] for index in range(6)],
            "verified": True,
        },
    )
    monkeypatch.setattr(
        villager_phase,
        "start_villager_multiplication",
        lambda *_args, **_kwargs: {
            "before_count": 2,
            "after_count": 3,
            "bread_consumed": 6,
            "offspring_uuid": "new-baby",
            "offspring_observed": True,
        },
    )
    monkeypatch.setattr(xp_phase, "find_spawner", lambda *_args, **_kwargs: (20, 40, 20))
    monkeypatch.setattr(
        xp_phase,
        "grind_xp_at_location",
        lambda *_args, **_kwargs: {
            "location": [20, 40, 20],
            "start_level": 20,
            "achieved_level": 30,
            "target_level": 30,
            "encounters": 4,
            "xp_gained": 12,
        },
    )

    state = DummyState()
    villager_result = villager_phase.VillagerInfraHandler().execute(
        SimpleNamespace(), DummyResources(), state
    )
    xp_result = xp_phase.XpEngineHandler().execute(
        SimpleNamespace(), DummyResources(), state
    )

    assert villager_result.success and xp_result.success
    assert state.payloads[Phase.VILLAGER_INFRA]["breeding"]["offspring_observed"]
    assert state.payloads[Phase.XP_ENGINE]["grind"]["achieved_level"] == 30


def test_handlers_fail_closed_on_missing_resources_or_capability(monkeypatch):
    state = DummyState()
    handler = villager_phase.VillagerInfraHandler()
    monkeypatch.setattr(handler, "_provision_breeding_bread", lambda *_args: False)
    missing = handler.execute(
        SimpleNamespace(), DummyResources({"minecraft:bread": 6}), state
    )
    monkeypatch.setattr(xp_phase, "find_spawner", lambda *_args, **_kwargs: None)
    no_spawner = xp_phase.XpEngineHandler().execute(
        SimpleNamespace(), DummyResources(), state
    )

    assert not missing.success
    assert missing.data["missing_requirements"] == {"minecraft:bread": 6}
    assert not no_spawner.success
    assert no_spawner.data["capability_blocker"] == "generic_mob_farm_construction"


def test_villager_handler_acquires_missing_bread_before_observation(monkeypatch):
    state = DummyState()
    handler = villager_phase.VillagerInfraHandler()
    provisioned = []
    monkeypatch.setattr(
        handler,
        "_provision_breeding_bread",
        lambda *_args: provisioned.append(True) or True,
    )
    monkeypatch.setattr(
        villager_phase,
        "get_nearby_entities",
        lambda *_args, **_kwargs: [adult(1, 10), adult(2, 12)],
    )
    monkeypatch.setattr(
        villager_phase,
        "build_villager_breeder",
        lambda *_args, **_kwargs: {
            "location": [11, 64, 10],
            "required_beds": 3,
            "bed_blocks": [[10 + index, 64, 12] for index in range(6)],
            "verified": True,
        },
    )
    monkeypatch.setattr(
        villager_phase,
        "start_villager_multiplication",
        lambda *_args, **_kwargs: {
            "offspring_uuid": "new-baby",
            "offspring_observed": True,
        },
    )

    result = handler.execute(
        SimpleNamespace(), DummyResources({"minecraft:bread": 6}), state
    )

    assert result.success
    assert provisioned == [True]


def test_villager_bread_provisioning_reuses_legacy_farm_location(monkeypatch):
    state = DummyState()
    state.custom_data["farm_location"] = [-426, 79, -20]
    counts = {"minecraft:bread": 0, "minecraft:wheat": 0}
    harvested = []

    monkeypatch.setattr(
        villager_phase, "withdraw_required_from_catalog", lambda *_a, **_k: 0
    )
    monkeypatch.setattr(
        villager_phase, "count_item", lambda _client, item: counts.get(item, 0)
    )
    monkeypatch.setattr(
        villager_phase,
        "harvest_wheat_farm",
        lambda _client, x, y, z: harvested.append((x, y, z))
        or counts.__setitem__("minecraft:wheat", 18)
        or True,
    )

    def craft(_client, requirements, **_kwargs):
        counts["minecraft:bread"] = requirements["minecraft:bread"]
        return SimpleNamespace(success=True)

    monkeypatch.setattr(villager_phase, "ensure_supplies", craft)
    client = SimpleNamespace(
        transport=SimpleNamespace(
            dispatch=lambda route, _payload: {
                "block_position": {"x": -410, "y": 79, "z": -17}
            }
            if route == "get_state"
            else {}
        )
    )

    assert villager_phase.VillagerInfraHandler._provision_breeding_bread(
        client, state
    )
    assert harvested == [(-426, 79, -20)]


def test_resource_requirements_are_real_nonzero_preconditions():
    transport = RouteTransport()
    resources = ResourceManager(SimpleNamespace(transport=transport))

    assert resources.check_phase_requirements(Phase.VILLAGER_INFRA) == {}
    assert resources.check_phase_requirements(Phase.XP_ENGINE) == {}

    transport.bread = 0
    transport.weapon = 0
    assert resources.check_phase_requirements(Phase.VILLAGER_INFRA) == {
        "minecraft:bread": 6
    }
    assert resources.check_phase_requirements(Phase.XP_ENGINE) == {
        "minecraft:iron_sword": 1
    }


class VillageTransport:
    """Stubs get_state/get_entities/explore/goto for locate_village."""

    def __init__(self, entity_batches, position=None):
        self.calls = []
        self._entity_batches = list(entity_batches)
        self.position = dict(position or {"x": 0, "y": 64, "z": 0})

    def dispatch(self, route, payload, **_kwargs):
        self.calls.append((route, dict(payload) if isinstance(payload, dict) else payload))
        if route == "get_state":
            return {"block_position": dict(self.position)}
        if route == "get_entities":
            batch = (
                self._entity_batches.pop(0)
                if len(self._entity_batches) > 1
                else self._entity_batches[0]
            )
            return {"entities": batch}
        return {"accepted": True}


def test_locate_village_finds_two_adults_on_the_first_scan():
    transport = VillageTransport([[adult(1, 40), adult(2, 44)]])
    client = SimpleNamespace(transport=transport)

    anchor = villager.locate_village(client)

    assert anchor == (42, 64, 10)
    assert any(route == "explore" for route, _payload in transport.calls)
    assert any(route == "get_entities" for route, _payload in transport.calls)


def test_locate_village_gives_up_after_the_timeout_when_none_is_found(monkeypatch):
    transport = VillageTransport([[]])
    client = SimpleNamespace(transport=transport)
    monkeypatch.setattr(villager.time, "sleep", lambda _seconds: None)

    assert villager.locate_village(client, timeout=1) is None


def test_villager_handler_travels_to_a_located_village_before_failing(monkeypatch):
    state = DummyState()
    handler = villager_phase.VillagerInfraHandler()

    villagers = [adult(1, 10), adult(2, 12)]
    positions = [entity_position(entity) for entity in villagers]
    observations = [([], [], []), (villagers, villagers, positions)]

    monkeypatch.setattr(
        villager_phase.VillagerInfraHandler,
        "_observe_nearby_adult_villagers",
        staticmethod(lambda _client: observations.pop(0)),
    )
    traveled = []
    monkeypatch.setattr(
        villager_phase.VillagerInfraHandler,
        "_travel_to_a_located_village",
        staticmethod(lambda *_a, **_k: traveled.append(True) or True),
    )
    monkeypatch.setattr(
        villager_phase,
        "build_villager_breeder",
        lambda *_args, **_kwargs: {
            "location": [11, 64, 10],
            "required_beds": 3,
            "bed_blocks": [[10 + index, 64, 12] for index in range(6)],
            "verified": True,
        },
    )
    monkeypatch.setattr(
        villager_phase,
        "start_villager_multiplication",
        lambda *_args, **_kwargs: {
            "offspring_uuid": "new-baby",
            "offspring_observed": True,
        },
    )

    result = handler.execute(
        SimpleNamespace(), DummyResources(), state
    )

    assert traveled == [True]
    assert result.success


def test_villager_handler_still_fails_closed_when_no_village_is_reachable(monkeypatch):
    state = DummyState()
    handler = villager_phase.VillagerInfraHandler()
    monkeypatch.setattr(
        villager_phase,
        "get_nearby_entities",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        villager_phase.VillagerInfraHandler,
        "_travel_to_a_located_village",
        staticmethod(lambda *_a, **_k: False),
    )

    result = handler.execute(SimpleNamespace(), DummyResources(), state)

    assert not result.success
    assert result.data["capability_blocker"] == "villager_transport"
    assert result.data["observed_adults"] == 0


class VillageLocationState:
    """A minimal state stub carrying a growable list of village locations,
    matching StateManager.get_locations/add_location's real shape.
    """

    def __init__(self, villages):
        self._villages = list(villages)
        self.saved = []

    def get_locations(self, category=None):
        return {
            "village": [
                {"x": x, "y": y, "z": z} for x, y, z in self._villages
            ]
        }

    def add_location(self, category, x, y, z, **kwargs):
        self._villages.append((x, y, z))
        self.saved.append((category, x, y, z, kwargs))


class TravelTransport:
    """Stubs get_state/get_entities for _travel_to_a_located_village.

    goto/staged_goto are monkeypatched directly at the module level (they
    are plain navigation calls, not bridge routes this transport handles),
    and update `position` themselves to simulate arrival.
    """

    def __init__(self, position, populations):
        self.position = dict(position)
        self._populations = populations

    def dispatch(self, route, payload=None, **_kwargs):
        if route == "get_state":
            return {"block_position": dict(self.position)}
        if route == "get_entities":
            key = (self.position["x"], self.position["y"], self.position["z"])
            return {"entities": self._populations.get(key, [])}
        return {"accepted": True}


def _fake_goto(transport):
    def goto(_client, x, y, z, **_kwargs):
        transport.position = {"x": x, "y": y, "z": z}
        return True

    return goto


def test_travel_advances_past_a_depleted_known_village(monkeypatch):
    """THE regression this fixes: the nearer known village has gone quiet
    (its villagers wandered off, or the population never really held), but
    a second known village -- farther away -- still has two adults.
    Previously only known[0] was ever tried, forever, regardless of how many
    other villages the bot already knew about.
    """
    near, far = (10, 64, 10), (500, 64, 500)
    state = VillageLocationState([near, far])
    populations = {far: [adult(1, far[0]), adult(2, far[0] + 2)]}
    transport = TravelTransport(position={"x": 0, "y": 64, "z": 0}, populations=populations)
    client = SimpleNamespace(transport=transport)

    goto = _fake_goto(transport)
    monkeypatch.setattr(villager_phase, "goto", goto)
    monkeypatch.setattr(
        villager_phase, "staged_goto",
        lambda _client, target, _current: goto(_client, *target),
    )
    scanned = {"value": False}
    monkeypatch.setattr(
        villager_phase, "locate_village",
        lambda *_a, **_k: scanned.__setitem__("value", True) or None,
    )

    reached = villager_phase.VillagerInfraHandler._travel_to_a_located_village(
        client, state
    )

    assert reached is True
    assert transport.position == {"x": far[0], "y": far[1], "z": far[2]}
    assert not scanned["value"], "must not scan fresh while a known village still works"


def test_travel_falls_back_to_a_fresh_scan_when_all_known_villages_are_depleted(
    monkeypatch,
):
    depleted = (10, 64, 10)
    fresh_spot = (300, 64, 300)
    state = VillageLocationState([depleted])
    populations = {fresh_spot: [adult(1, fresh_spot[0]), adult(2, fresh_spot[0] + 2)]}
    transport = TravelTransport(position={"x": 0, "y": 64, "z": 0}, populations=populations)
    client = SimpleNamespace(transport=transport)

    goto = _fake_goto(transport)
    monkeypatch.setattr(villager_phase, "goto", goto)
    monkeypatch.setattr(
        villager_phase, "staged_goto",
        lambda _client, target, _current: goto(_client, *target),
    )
    monkeypatch.setattr(villager_phase, "locate_village", lambda *_a, **_k: fresh_spot)

    reached = villager_phase.VillagerInfraHandler._travel_to_a_located_village(
        client, state
    )

    assert reached is True
    assert (
        "village",
        fresh_spot[0], fresh_spot[1], fresh_spot[2],
        {"dimension": "overworld", "tags": ["verified"]},
    ) in state.saved


def test_travel_fails_closed_when_everything_is_exhausted(monkeypatch):
    depleted = (10, 64, 10)
    state = VillageLocationState([depleted])
    transport = TravelTransport(position={"x": 0, "y": 64, "z": 0}, populations={})
    client = SimpleNamespace(transport=transport)

    monkeypatch.setattr(villager_phase, "goto", _fake_goto(transport))
    monkeypatch.setattr(
        villager_phase, "staged_goto",
        lambda _client, target, _current: _fake_goto(transport)(_client, *target),
    )
    monkeypatch.setattr(villager_phase, "locate_village", lambda *_a, **_k: None)

    assert villager_phase.VillagerInfraHandler._travel_to_a_located_village(
        client, state
    ) is False
