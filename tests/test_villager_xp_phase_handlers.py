"""Focused route-contract tests for villager and XP progression."""

from types import SimpleNamespace

from baritone_client.automator.phases import villager as villager_phase
from baritone_client.automator.phases import xp_engine as xp_phase
from baritone_client.automator.resource_manager import ResourceManager
from baritone_client.automator.state_manager import Phase
from baritone_client.common import mob_farm, villager


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
    missing = villager_phase.VillagerInfraHandler().execute(
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
