from types import SimpleNamespace

from baritone_client.automator.phases.iron_farm import IronFarmHandler
from baritone_client.automator.phases.trading import ToolPerfectionHandler
from baritone_client.automator.resource_manager import ResourceManager
from baritone_client.automator.state_manager import Phase
from baritone_client.common.iron_farm import detect_farm_entities


FORBIDDEN_ROUTES = {
    "build_iron_farm",
    "move_entities",
    "move_entity",
    "summon",
    "start_iron_farm",
    "get_trading_state",
    "breed_villagers",
    "trade_librarian",
    "cure_villagers",
}


class RecordingState:
    def __init__(self, payloads=None):
        self.payloads = payloads or {}

    def get_phase_payload(self, phase):
        return self.payloads.get(phase, {})

    def record_phase_payload(self, phase, payload):
        self.payloads[phase] = payload


class RealSchemaTransport:
    def __init__(self, *, entities=None, inventory=None, blocks=None):
        self.entities = list(entities or [])
        self.inventory = list(inventory or [])
        self.blocks = dict(blocks or {})
        self.calls = []

    def dispatch(self, route, payload, **_kwargs):
        self.calls.append((route, dict(payload)))
        if route == "get_state":
            return {
                "block_position": {"x": 10, "y": 64, "z": 10},
                "health": 20,
                "dimension": "minecraft:overworld",
            }
        if route == "get_entities":
            return {
                "entities": list(self.entities),
                "count": len(self.entities),
                "radius": payload["radius"],
            }
        if route == "get_inventory":
            return {"inventory": list(self.inventory), "armor": [], "offhand": []}
        if route == "get_block":
            position = (payload["x"], payload["y"], payload["z"])
            return {"id": self.blocks.get(position, "minecraft:air")}
        if route == "entity_interact":
            return {"success": True, "accepted": True, "entity_id": payload["entity_id"]}
        if route == "get_screen":
            return {
                "type": "MerchantMenu",
                "slots": [{"slot": 0, "id": "minecraft:enchanted_book", "count": 1}],
            }
        if route == "close_screen":
            return {"closed": True}
        raise AssertionError(f"unexpected route: {route}")


def entity(entity_id, entity_type, position, **extra):
    return {
        "id": entity_id,
        "type": entity_type,
        "position": {"x": position[0], "y": position[1], "z": position[2]},
        "distance": extra.pop("distance", 3.0),
        **extra,
    }


def structure_witnesses():
    return {
        "beds": [[8, 64, 8], [9, 64, 8], [10, 64, 8]],
        "hopper": [10, 63, 10],
        "chest": [10, 62, 10],
        "spawn_platform": [
            [x, 66, z]
            for x in range(9, 12)
            for z in range(9, 12)
        ],
    }


def structure_blocks(witnesses):
    blocks = {
        tuple(position): "minecraft:white_bed"
        for position in witnesses["beds"]
    }
    blocks[tuple(witnesses["hopper"])] = "minecraft:hopper"
    blocks[tuple(witnesses["chest"])] = "minecraft:chest"
    blocks.update(
        {
            tuple(position): "minecraft:stone"
            for position in witnesses["spawn_platform"]
        }
    )
    return blocks


def test_resource_contracts_do_not_short_circuit_world_state_phases():
    transport = RealSchemaTransport()
    resources = ResourceManager(SimpleNamespace(transport=transport))

    assert resources.get_phase_requirements(Phase.IRON_FARM) == {}
    assert resources.get_phase_requirements(Phase.TOOL_PERFECTION) == {}
    assert resources.phase_ready_result(Phase.IRON_FARM, "skip") is None
    assert resources.phase_ready_result(Phase.TOOL_PERFECTION, "skip") is None
    assert transport.calls == []


def test_entity_observation_uses_real_bridge_type_and_position_schema():
    transport = RealSchemaTransport(
        entities=[
            entity(41, "minecraft:villager", (11, 64, 10)),
            entity(42, "minecraft:zombie", (12, 64, 10)),
            entity(43, "minecraft:villager", (40, 64, 40)),
        ]
    )

    counts = detect_farm_entities(SimpleNamespace(transport=transport), (10, 64, 10), limit=8)

    assert counts == {"minecraft:villager": 1, "minecraft:zombie": 1}
    assert transport.calls == [("get_entities", {"radius": 16})]


def test_iron_farm_persists_exact_supported_capability_blockers():
    transport = RealSchemaTransport(
        entities=[entity(41, "minecraft:villager", (11, 64, 10), is_baby=False)]
    )
    state = RecordingState()

    result = IronFarmHandler().execute(
        SimpleNamespace(transport=transport),
        object(),
        state,
    )

    assert not result.success
    assert "survival villager-transport primitive" in result.reason
    assert "survival zombie-capture primitive" in result.reason
    assert "complete iron-farm builder" in result.reason
    payload = state.get_phase_payload(Phase.IRON_FARM)
    assert payload["farm_location"] == [10, 64, 10]
    assert payload["adult_villager_count"] == 1
    assert not payload["production_verified"]
    assert not ({route for route, _payload in transport.calls} & FORBIDDEN_ROUTES)


def test_iron_farm_accepts_only_live_structure_entities_and_production():
    witnesses = structure_witnesses()
    transport = RealSchemaTransport(
        entities=[
            entity(1, "minecraft:villager", (9, 64, 10), is_baby=False),
            entity(2, "minecraft:villager", (10, 64, 10), is_baby=False),
            entity(3, "minecraft:villager", (11, 64, 10), is_baby=False),
            entity(4, "minecraft:zombie", (10, 64, 11)),
            entity(5, "minecraft:iron_golem", (12, 64, 10)),
        ],
        blocks=structure_blocks(witnesses),
    )
    state = RecordingState(
        {
            Phase.IRON_FARM: {
                "farm_location": [10, 64, 10],
                "structure_witnesses": witnesses,
            }
        }
    )

    result = IronFarmHandler().execute(
        SimpleNamespace(transport=transport),
        object(),
        state,
    )

    assert result.success
    assert result.reason == "Existing survival iron farm verified"
    assert result.data["implementation_blocker"] is None
    assert not ({route for route, _payload in transport.calls} & FORBIDDEN_ROUTES)


def test_trading_inspects_real_librarian_then_reports_component_blocker():
    transport = RealSchemaTransport(
        entities=[
            entity(
                7,
                "minecraft:villager",
                (11, 64, 10),
                profession="minecraft:librarian",
                offers_count=5,
                is_baby=False,
            )
        ],
        inventory=[
            {
                "slot": 0,
                "id": "minecraft:enchanted_book",
                "count": 4,
                "damage": 0,
                "name": "enchanted_book",
            }
        ],
    )
    state = RecordingState()

    result = ToolPerfectionHandler().execute(
        SimpleNamespace(transport=transport),
        object(),
        state,
    )

    assert not result.success
    assert "omit stored enchantment components and merchant offers" in result.reason
    payload = state.get_phase_payload(Phase.TOOL_PERFECTION)
    assert payload["librarian_count"] == 1
    assert payload["librarian_offer_counts"] == [5]
    assert payload["trade_screen_opened"]
    assert payload["verified_enchantments"] == []
    assert [route for route, _payload in transport.calls][-3:] == [
        "entity_interact",
        "get_screen",
        "close_screen",
    ]
    assert not ({route for route, _payload in transport.calls} & FORBIDDEN_ROUTES)
