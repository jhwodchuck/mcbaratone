from types import SimpleNamespace
from unittest.mock import mock_open

from baritone_client.automator.phases import boot_sequence
from baritone_client.automator.phases.boot_sequence import BootSequenceHandler
from baritone_client.actions.boot_sequence import (
    BaseRecoveryAction,
    BedAcquisitionAction,
    ConditionalWoodGatheringAction,
    FinalSleepAction,
    HuntingAndScoutingAction,
    InfrastructurePlacementAction,
    SafetyCheckAction,
    StoneToolCraftingAction,
)


class RecordingState:
    def __init__(self):
        self.payloads = []

    def record_phase_payload(self, phase, payload):
        self.payloads.append((phase, payload))


def test_boot_sequence_translates_action_message_to_task_reason(monkeypatch):
    monkeypatch.setattr(
        boot_sequence.SequenceAction,
        "execute",
        lambda _sequence, _context: SimpleNamespace(success=True, message="boot ready"),
    )
    monkeypatch.setattr(
        BootSequenceHandler,
        "_plant_crops",
        lambda _handler, _client: True,
    )
    state = RecordingState()

    result = BootSequenceHandler().execute(
        SimpleNamespace(), SimpleNamespace(), state
    )

    assert result.success
    assert result.reason == "boot ready"
    assert state.payloads


def test_boot_crop_planting_verifies_and_persists_irrigated_plot(monkeypatch):
    blocks = {(0, 64, 0): "minecraft:water"}
    for x in range(-1, 2):
        for z in range(-1, 2):
            if (x, z) != (0, 0):
                blocks[(x, 64, z)] = "minecraft:dirt"
                blocks[(x, 65, z)] = "minecraft:air"

    inventory = {
        "minecraft:wooden_hoe": 1,
        "minecraft:wheat_seeds": 2,
    }

    class Transport:
        selected = None

        def dispatch(self, route, payload):
            if route == "get_block":
                return {
                    "id": blocks.get(
                        (payload["x"], payload["y"], payload["z"]),
                        "minecraft:air",
                    )
                }
            if route == "interact_block":
                pos = (payload["x"], payload["y"], payload["z"])
                if self.selected == "minecraft:wooden_hoe":
                    blocks[pos] = "minecraft:farmland"
                elif (
                    self.selected == "minecraft:wheat_seeds"
                    and blocks.get(pos) == "minecraft:farmland"
                    and inventory["minecraft:wheat_seeds"] > 0
                ):
                    blocks[(pos[0], pos[1] + 1, pos[2])] = "minecraft:wheat"
                    inventory["minecraft:wheat_seeds"] -= 1
            return {}

    transport = Transport()
    client = SimpleNamespace(transport=transport)
    locations = []
    state = SimpleNamespace(
        custom_data={},
        add_location=lambda *args, **kwargs: locations.append((args, kwargs)),
    )
    handler = BootSequenceHandler()
    handler.state = state

    monkeypatch.setattr(
        "baritone_client.common.navigation.find_nearby_block",
        lambda _client, block_ids, **_kwargs: (0, 64, 0)
        if "minecraft:water" in block_ids
        else None,
    )
    monkeypatch.setattr(
        "baritone_client.common.navigation.goto",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        boot_sequence,
        "count_item",
        lambda _client, item_id: inventory.get(item_id, 0),
    )

    def select(_client, item_id):
        if inventory.get(item_id, 0) <= 0:
            return False
        transport.selected = item_id
        return True

    monkeypatch.setattr(boot_sequence, "select_item", select)
    monkeypatch.setattr(boot_sequence.time, "sleep", lambda _seconds: None)

    assert handler._plant_crops(client)
    farm = state.custom_data["structures"]["food_source"]
    assert farm["verified"] is True
    assert farm["irrigated"] is True
    assert farm["planted"] == 2
    assert state.custom_data["farm_location"] == [0, 64, 0]
    assert len(locations) == 1


def test_boot_crop_planting_reuses_verified_plot_without_new_supplies(monkeypatch):
    blocks = {
        (0, 64, 0): "minecraft:water",
        (1, 64, 0): "minecraft:farmland",
        (1, 65, 0): "minecraft:wheat",
    }

    class Transport:
        def __init__(self):
            self.calls = []

        def dispatch(self, route, payload):
            self.calls.append((route, payload))
            if route == "get_block":
                return {
                    "id": blocks.get(
                        (payload["x"], payload["y"], payload["z"]),
                        "minecraft:air",
                    )
                }
            if route in {"mine", "interact_block"}:
                raise AssertionError("verified farm must not be rebuilt")
            return {}

    transport = Transport()
    client = SimpleNamespace(transport=transport)
    state = SimpleNamespace(custom_data={"farm_location": [0, 64, 0]})
    handler = BootSequenceHandler()
    handler.state = state
    monkeypatch.setattr(
        "baritone_client.common.navigation.goto",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(boot_sequence, "count_item", lambda *_args: 0)
    monkeypatch.setattr(
        boot_sequence,
        "ensure_supplies",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("verified farm must not craft a hoe")
        ),
    )

    assert handler._plant_crops(client)
    assert state.custom_data["structures"]["food_source"]["planted"] == 1


def test_boot_infrastructure_reuses_verified_house_blocks(monkeypatch):
    positions = {
        (11, 65, 21): "minecraft:crafting_table",
        (12, 65, 21): "minecraft:furnace",
        (11, 65, 22): "minecraft:chest",
    }

    class Transport:
        def dispatch(self, route, payload):
            if route == "get_state":
                return {"is_pathing": False}
            if route == "get_block":
                return {"id": positions.get((payload["x"], payload["y"], payload["z"]), "minecraft:air")}
            return {}

    state = SimpleNamespace(
        custom_data={
            "structures": {
                "starter_house": {
                    "origin": [10, 64, 20],
                    "crafting_table": [11, 65, 21],
                    "furnace": [12, 65, 21],
                    "supply_chest": [11, 65, 22],
                }
            }
        }
    )
    monkeypatch.setattr(
        "baritone_client.actions.boot_sequence.time.sleep", lambda _seconds: None
    )
    monkeypatch.setattr(
        "baritone_client.actions.boot_sequence.goto", lambda *_args, **_kwargs: True
    )
    monkeypatch.setattr(
        "baritone_client.actions.boot_sequence.setup_base",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("verified infrastructure must not be rebuilt")
        ),
    )

    result = InfrastructurePlacementAction().execute(
        SimpleNamespace(client=SimpleNamespace(transport=Transport()), state=state)
    )

    assert result.success
    assert "verified" in result.message


def test_boot_infrastructure_repairs_only_missing_world_block(monkeypatch):
    positions = {
        (11, 65, 21): "minecraft:air",
        (12, 65, 21): "minecraft:furnace",
        (11, 65, 22): "minecraft:chest",
    }

    class Transport:
        def dispatch(self, route, payload):
            if route == "get_block":
                return {
                    "id": positions.get(
                        (payload["x"], payload["y"], payload["z"]),
                        "minecraft:air",
                    )
                }
            return {}

    state = SimpleNamespace(
        custom_data={
            "structures": {
                "starter_house": {
                    "origin": [10, 64, 20],
                    "crafting_table": [11, 65, 21],
                    "furnace": [12, 65, 21],
                    "supply_chest": [11, 65, 22],
                }
            }
        }
    )
    client = SimpleNamespace(transport=Transport())
    monkeypatch.setattr(
        "baritone_client.actions.boot_sequence.goto", lambda *_args, **_kwargs: True
    )
    monkeypatch.setattr(
        "baritone_client.actions.boot_sequence.count_item",
        lambda _client, item_id: 1 if item_id == "minecraft:crafting_table" else 0,
    )
    monkeypatch.setattr(
        "baritone_client.actions.boot_sequence.ensure_supplies",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("existing furnace and chest must not be recrafted")
        ),
    )
    monkeypatch.setattr(
        "baritone_client.actions.boot_sequence.setup_base",
        lambda *_args, **_kwargs: (True, (10, 65, 20)),
    )

    result = InfrastructurePlacementAction().execute(
        SimpleNamespace(client=client, state=state)
    )

    assert result.success


def test_boot_infrastructure_can_precede_full_house(monkeypatch):
    state = SimpleNamespace(custom_data={})
    client = SimpleNamespace(transport=SimpleNamespace())
    monkeypatch.setattr(
        "baritone_client.actions.boot_sequence.find_flat_ground",
        lambda *_args, **_kwargs: (20, 64, 30),
    )
    monkeypatch.setattr(
        "baritone_client.actions.boot_sequence.setup_base",
        lambda *_args, **_kwargs: (True, (20, 64, 30)),
    )

    result = InfrastructurePlacementAction().execute(
        SimpleNamespace(client=client, state=state)
    )

    assert result.success
    assert state.custom_data["bootstrap_base_location"] == [20, 64, 30]
    assert state.custom_data["structures"]["bootstrap_base"] == {
        "origin": [20, 64, 30],
        "crafting_table": [21, 64, 31],
        "furnace": [22, 64, 31],
        "supply_chest": [21, 64, 32],
        "verified": True,
    }


def test_boot_wood_check_counts_real_plank_ids(monkeypatch):
    monkeypatch.setattr(
        "baritone_client.actions.boot_sequence.count_item",
        lambda _client, item_id: 16 if item_id == "minecraft:dark_oak_planks" else 0,
    )
    monkeypatch.setattr(
        "baritone_client.actions.boot_sequence.gather_wood",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("existing plank families must satisfy the wood check")
        ),
    )

    result = ConditionalWoodGatheringAction(needed_logs=4).execute(
        SimpleNamespace(client=SimpleNamespace())
    )

    assert result.success


def test_boot_wood_check_counts_carried_logs_as_convertible_planks(monkeypatch):
    def count(_client, item_id):
        if item_id == "minecraft:spruce_log":
            return 2
        if item_id == "minecraft:dark_oak_planks":
            return 8
        return 0

    monkeypatch.setattr(
        "baritone_client.actions.boot_sequence.count_item",
        count,
    )
    monkeypatch.setattr(
        "baritone_client.actions.boot_sequence.gather_wood",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("convertible logs and planks must satisfy the wood check")
        ),
    )

    result = ConditionalWoodGatheringAction(needed_logs=4).execute(
        SimpleNamespace(client=SimpleNamespace())
    )

    assert result.success


def test_boot_wood_check_defers_reserve_on_ledge_with_nearby_table(monkeypatch):
    def count(_client, item_id):
        if item_id == "minecraft:spruce_log":
            return 2
        if item_id == "minecraft:dark_oak_planks":
            return 2
        return 0

    monkeypatch.setattr(
        "baritone_client.actions.boot_sequence.count_item",
        count,
    )
    monkeypatch.setattr(
        "baritone_client.actions.boot_sequence.find_nearby_block",
        lambda *_args, **_kwargs: (5, 101, -10),
    )
    monkeypatch.setattr(
        "baritone_client.actions.boot_sequence.gather_wood",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("stone recovery must run before the larger wood reserve")
        ),
    )

    result = ConditionalWoodGatheringAction(needed_logs=4).execute(
        SimpleNamespace(client=SimpleNamespace())
    )

    assert result.success


def test_boot_base_recovery_prefers_checkpointed_house(monkeypatch):
    calls = []

    class Transport:
        def dispatch(self, route, payload):
            calls.append((route, payload))
            if route == "get_block":
                return {"id": "minecraft:crafting_table"}
            if route == "waypoint":
                raise AssertionError("stale waypoint must not override checkpointed house")
            return {}

    state = SimpleNamespace(
        custom_data={
            "structures": {
                "starter_house": {
                    "crafting_table": [-8, 79, -121],
                }
            }
        }
    )
    monkeypatch.setattr("builtins.open", mock_open(read_data="(-8, 79, -121)"))

    result = BaseRecoveryAction().execute(
        SimpleNamespace(client=SimpleNamespace(transport=Transport()), state=state)
    )

    assert result.success
    assert (
        "chat",
        {"message": "#waypoint save base -8 79 -121"},
    ) in calls


def test_stone_tool_action_gathers_raw_cobble_before_crafting(monkeypatch):
    inventory = {"minecraft:cobblestone": 0}
    calls = []
    monkeypatch.setattr(
        "baritone_client.actions.boot_sequence.count_item",
        lambda _client, item_id: inventory.get(item_id, 0),
    )

    def gather(_client, count, timeout):
        calls.append(("gather", count, timeout))
        inventory["minecraft:cobblestone"] = count
        return True

    monkeypatch.setattr("baritone_client.actions.boot_sequence.gather_stone", gather)
    monkeypatch.setattr(
        "baritone_client.actions.boot_sequence.ensure_supplies",
        lambda _client, requirements: calls.append(("craft", requirements))
        or SimpleNamespace(success=True),
    )

    result = StoneToolCraftingAction().execute(
        SimpleNamespace(client=SimpleNamespace())
    )

    assert result.success
    assert calls[0] == ("gather", 9, 180)
    assert calls[1][0] == "craft"


def test_boot_base_recovery_keeps_house_when_table_needs_repair(monkeypatch):
    calls = []
    blocks = {
        (-8, 79, -121): "minecraft:air",
        (-7, 79, -121): "minecraft:furnace",
        (-8, 79, -120): "minecraft:chest",
    }

    class Transport:
        def dispatch(self, route, payload):
            calls.append((route, payload))
            if route == "get_block":
                return {
                    "id": blocks.get(
                        (payload["x"], payload["y"], payload["z"]),
                        "minecraft:air",
                    )
                }
            if route == "waypoint":
                raise AssertionError("temporary waypoint must not override house anchors")
            return {}

    state = SimpleNamespace(
        custom_data={
            "structures": {
                "starter_house": {
                    "crafting_table": [-8, 79, -121],
                    "furnace": [-7, 79, -121],
                    "supply_chest": [-8, 79, -120],
                }
            }
        }
    )
    monkeypatch.setattr("builtins.open", mock_open(read_data="(-8, 79, -121)"))

    result = BaseRecoveryAction().execute(
        SimpleNamespace(client=SimpleNamespace(transport=Transport()), state=state)
    )

    assert result.success
    assert (
        "chat",
        {"message": "#waypoint save base -8 79 -121"},
    ) in calls


def test_final_sleep_waits_safely_when_no_bed_is_available(monkeypatch):
    class Transport:
        def dispatch(self, route, payload):
            if route == "get_state":
                return {"world_time": 14000}
            return {}

    client = SimpleNamespace(transport=Transport())
    monkeypatch.setattr(
        "baritone_client.actions.boot_sequence.sleep_through_night",
        lambda _client: False,
    )
    waited = []
    monkeypatch.setattr(
        "baritone_client.actions.boot_sequence.wait_for_safe_daylight",
        lambda _client: waited.append(True) or True,
    )

    result = FinalSleepAction().execute(
        SimpleNamespace(client=client, state=SimpleNamespace())
    )

    assert result.success
    assert waited == [True]


def test_optional_boot_hunts_are_deferred_at_low_hunger(monkeypatch):
    class Transport:
        def dispatch(self, route, payload):
            if route == "get_state":
                return {"world_time": 9000, "food_level": 6}
            return {}

    client = SimpleNamespace(transport=Transport())
    monkeypatch.setattr(
        "baritone_client.actions.boot_sequence.hunt_passive_mobs",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("optional hunting must not strand a hungry bot")
        ),
    )
    context = SimpleNamespace(client=client, state=SimpleNamespace())

    assert BedAcquisitionAction().execute(context).success
    assert HuntingAndScoutingAction().execute(context).success


def test_bed_acquisition_reuses_checkpointed_house_bed(monkeypatch):
    class Transport:
        def dispatch(self, route, payload):
            if route == "get_block":
                assert payload == {"x": 12, "y": 65, "z": 22}
                return {"id": "minecraft:black_bed"}
            raise AssertionError(f"unexpected route: {route}")

    state = SimpleNamespace(
        custom_data={
            "structures": {"starter_house": {"bed": [12, 65, 22]}}
        }
    )
    client = SimpleNamespace(transport=Transport())
    monkeypatch.setattr(
        "baritone_client.actions.boot_sequence.count_item", lambda *_args: 0
    )
    monkeypatch.setattr(
        "baritone_client.actions.boot_sequence.hunt_passive_mobs",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("verified house bed must prevent duplicate sheep hunt")
        ),
    )

    result = BedAcquisitionAction().execute(
        SimpleNamespace(client=client, state=state)
    )

    assert result.success
    assert result.message == "House bed already available"


def test_night_safety_uses_shelter_instead_of_bulk_mining(monkeypatch):
    class Transport:
        def dispatch(self, route, payload):
            if route == "get_state":
                return {"world_time": 14000, "health": 20}
            return {}

    client = SimpleNamespace(transport=Transport())
    waited = []
    monkeypatch.setattr(
        "baritone_client.actions.boot_sequence.wait_for_safe_daylight",
        lambda _client: waited.append(True) or True,
    )
    monkeypatch.setattr(
        "baritone_client.actions.boot_sequence.gather_stone",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("surface night safety must not start bulk mining")
        ),
    )

    result = SafetyCheckAction().execute(
        SimpleNamespace(client=client, state=SimpleNamespace())
    )

    assert result.success
    assert waited == [True]
