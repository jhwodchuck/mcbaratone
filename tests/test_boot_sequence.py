from types import SimpleNamespace
from unittest.mock import mock_open

import pytest

from baritone_client.automator.phases import boot_sequence
from baritone_client.automator.phases.boot_sequence import BootSequenceHandler
from baritone_client.actions.boot_sequence import (
    BaseRecoveryAction,
    BedAcquisitionAction,
    ConditionalWoodGatheringAction,
    FinalSleepAction,
    HuntingAndScoutingAction,
    InfrastructurePlacementAction,
    PlankCraftingAction,
    SafetyCheckAction,
    StorageOrganizationAction,
    StoneToolCraftingAction,
)
from baritone_client.common.tasks import (
    PlayerDeathDetected,
    SurvivalRecoveryRequired,
    IncrementalProgressRequired,
)


class RecordingState:
    def __init__(self):
        self.payloads = []

    def record_phase_payload(self, phase, payload):
        self.payloads.append((phase, payload))


def test_boot_sequence_advances_only_first_unverified_step(monkeypatch):
    state = RecordingState()
    state.custom_data = {}
    state.record_phase_payload = state.record_phase_payload
    dry_anchor_calls = []

    def run_dry_anchor(_self, _client, _state, homestead):
        dry_anchor_calls.append("dry_anchor")
        homestead.setdefault("steps", {}).setdefault("dry_anchor", {})["verified"] = True
        homestead["anchor"] = [0, 64, 0]
        return True

    def forbidden_step(*_args, **_kwargs):
        pytest.fail("only one boot step should execute per invocation")

    handler = BootSequenceHandler()
    monkeypatch.setattr(BootSequenceHandler, "_run_dry_anchor_step", run_dry_anchor)
    monkeypatch.setattr(handler, "_run_wood_reserve_step", forbidden_step)
    monkeypatch.setattr(handler, "_run_stone_reserve_step", forbidden_step)
    monkeypatch.setattr(handler, "_run_infrastructure_step", forbidden_step)
    monkeypatch.setattr(handler, "_run_micro_farm_step", forbidden_step)
    monkeypatch.setattr(handler, "_run_charcoal_supply_step", forbidden_step)
    monkeypatch.setattr(handler, "_run_torch_supply_step", forbidden_step)
    monkeypatch.setattr(handler, "_run_light_perimeter_step", forbidden_step)
    monkeypatch.setattr(handler, "_ensure_dry_anchor", lambda *_args: (0, 64, 0))
    monkeypatch.setattr(handler, "_enforce_anchor_and_pacing", lambda *_args, **_kwargs: None)

    with pytest.raises(IncrementalProgressRequired):
        handler.execute(
            SimpleNamespace(
                transport=SimpleNamespace(
                    dispatch=lambda *args, **kwargs: {
                        "world_time": 1000,
                        "dimension": "minecraft:overworld",
                        "health": 20,
                        "food_level": 20,
                    }
                )
            ),
            SimpleNamespace(),
            state,
        )

    assert dry_anchor_calls == ["dry_anchor"]
    assert state.payloads


def test_boot_handler_preserves_survival_recovery_signal(monkeypatch):
    handler = BootSequenceHandler()
    state = RecordingState()
    state.custom_data = {}
    state.record_phase_payload = state.record_phase_payload

    def run_dry_anchor(*_args, **_kwargs):
        raise SurvivalRecoveryRequired("recover before boot")

    monkeypatch.setattr(BootSequenceHandler, "_run_dry_anchor_step", run_dry_anchor)
    monkeypatch.setattr(BootSequenceHandler, "_enforce_anchor_and_pacing", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(BootSequenceHandler, "_ensure_dry_anchor", lambda *_args: (0, 64, 0))

    with pytest.raises(SurvivalRecoveryRequired):
        handler.execute(
            SimpleNamespace(
                transport=SimpleNamespace(
                    dispatch=lambda *args, **kwargs: {
                        "world_time": 1000,
                        "dimension": "minecraft:overworld",
                        "health": 20,
                        "food_level": 20,
                    }
                )
            ),
            SimpleNamespace(),
            state,
        )


def test_boot_handler_preserves_player_death_signal(monkeypatch):
    handler = BootSequenceHandler()
    state = RecordingState()
    state.custom_data = {}
    state.record_phase_payload = state.record_phase_payload

    def run_dry_anchor(*_args, **_kwargs):
        raise PlayerDeathDetected("died during boot")

    monkeypatch.setattr(BootSequenceHandler, "_run_dry_anchor_step", run_dry_anchor)
    monkeypatch.setattr(BootSequenceHandler, "_enforce_anchor_and_pacing", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(BootSequenceHandler, "_ensure_dry_anchor", lambda *_args: (0, 64, 0))

    with pytest.raises(PlayerDeathDetected):
        handler.execute(
            SimpleNamespace(
                transport=SimpleNamespace(
                    dispatch=lambda *args, **kwargs: {
                        "world_time": 1000,
                        "dimension": "minecraft:overworld",
                        "health": 20,
                        "food_level": 20,
                    }
                )
            ),
            SimpleNamespace(),
            state,
        )


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
                return {
                    "is_pathing": False,
                    "block_position": {"x": 10, "y": 65, "z": 20},
                }
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
            if route == "get_state":
                return {
                    "block_position": {"x": 10, "y": 65, "z": 20},
                }
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


def test_boot_infrastructure_falls_back_locally_when_house_is_unreachable(
    monkeypatch,
):
    state = SimpleNamespace(
        custom_data={
            "structures": {
                "starter_house": {
                    "origin": [300, 68, 300],
                    "crafting_table": [301, 68, 301],
                }
            }
        }
    )
    client = SimpleNamespace(transport=SimpleNamespace())
    monkeypatch.setattr(
        "baritone_client.actions.boot_sequence.nearby_infrastructure_record",
        lambda _context: state.custom_data["structures"]["starter_house"],
    )
    monkeypatch.setattr(
        "baritone_client.actions.boot_sequence.goto",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        "baritone_client.actions.boot_sequence.find_flat_ground",
        lambda *_args, **_kwargs: (-40, 64, -315),
    )
    monkeypatch.setattr(
        "baritone_client.actions.boot_sequence.setup_base",
        lambda *_args, **_kwargs: (True, (-40, 64, -315)),
    )

    result = InfrastructurePlacementAction().execute(
        SimpleNamespace(client=client, state=state)
    )

    assert result.success
    assert state.custom_data["bootstrap_base_location"] == [-40, 64, -315]


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


def test_boot_plank_crafting_uses_mangrove_logs_detected_by_wood_check(monkeypatch):
    crafted = []

    monkeypatch.setattr(
        "baritone_client.actions.boot_sequence.count_item",
        lambda _client, item_id: 4 if item_id == "minecraft:mangrove_log" else 0,
    )
    monkeypatch.setattr(
        "baritone_client.actions.boot_sequence.craft",
        lambda _client, item_id, count: crafted.append((item_id, count)) or True,
    )

    result = PlankCraftingAction().execute(
        SimpleNamespace(client=SimpleNamespace())
    )

    assert result.success
    assert crafted == [("minecraft:mangrove_planks", 4)]


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


def test_boot_wood_check_yields_to_low_food_recovery(monkeypatch):
    client = SimpleNamespace(
        transport=SimpleNamespace(
            dispatch=lambda route, _payload: {
                "health": 20,
                "food_level": 5,
            }
        )
    )
    monkeypatch.setattr(
        "baritone_client.actions.boot_sequence.count_item",
        lambda *_args: 0,
    )
    monkeypatch.setattr(
        "baritone_client.actions.boot_sequence.find_nearby_block",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "baritone_client.actions.boot_sequence.gather_wood",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("unsafe outdoor gathering must not begin")
        ),
    )

    with pytest.raises(SurvivalRecoveryRequired):
        ConditionalWoodGatheringAction(needed_logs=4).execute(
            SimpleNamespace(client=client, state=SimpleNamespace(custom_data={}))
        )


def test_boot_safety_preserves_survival_recovery_signal():
    client = SimpleNamespace(
        transport=SimpleNamespace(
            dispatch=lambda route, _payload: {
                "world_time": 1000,
                "health": 2.5,
                "food_level": 15,
            }
        )
    )

    with pytest.raises(SurvivalRecoveryRequired):
        SafetyCheckAction().execute(SimpleNamespace(client=client))


def test_boot_reuses_durable_tools_and_verified_infrastructure(monkeypatch):
    durable = {
        "minecraft:stone_pickaxe",
        "minecraft:stone_axe",
        "minecraft:stone_shovel",
        "minecraft:stone_sword",
    }
    monkeypatch.setattr(
        "baritone_client.actions.boot_readiness.count_item",
        lambda _client, item_id: int(item_id in durable),
    )
    monkeypatch.setattr(
        "baritone_client.actions.boot_sequence.gather_wood",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("owned durable capabilities must not be recreated")
        ),
    )
    blocks = {
        (11, 65, 21): "minecraft:crafting_table",
        (12, 65, 21): "minecraft:furnace",
        (11, 65, 22): "minecraft:chest",
    }

    class Transport:
        def dispatch(self, route, payload):
            if route == "get_state":
                return {
                    "block_position": {"x": 10, "y": 65, "z": 20},
                }
            if route == "get_block":
                return {
                    "id": blocks.get(
                        (payload["x"], payload["y"], payload["z"]),
                        "minecraft:air",
                    )
                }
            return {}

    context = SimpleNamespace(
        client=SimpleNamespace(transport=Transport()),
        state=SimpleNamespace(
            custom_data={
                "structures": {
                    "bootstrap_base": {
                        "origin": [10, 65, 20],
                        "crafting_table": [11, 65, 21],
                        "furnace": [12, 65, 21],
                        "supply_chest": [11, 65, 22],
                        "verified": True,
                    },
                }
            }
        ),
    )

    assert ConditionalWoodGatheringAction(needed_logs=4).execute(context).success
    assert StoneToolCraftingAction().execute(context).success


def test_boot_does_not_reuse_remote_checkpointed_infrastructure(monkeypatch):
    durable = {
        "minecraft:stone_pickaxe",
        "minecraft:stone_axe",
        "minecraft:stone_shovel",
        "minecraft:stone_sword",
    }
    monkeypatch.setattr(
        "baritone_client.actions.boot_readiness.count_item",
        lambda _client, item_id: int(item_id in durable),
    )
    gathered = []
    monkeypatch.setattr(
        "baritone_client.actions.boot_sequence.count_item",
        lambda *_args, **_kwargs: 0,
    )
    monkeypatch.setattr(
        "baritone_client.actions.boot_sequence.find_nearby_block",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "baritone_client.actions.boot_sequence.require_survival_margin",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "baritone_client.actions.boot_sequence.gather_wood",
        lambda _client, count: gathered.append(count) or True,
    )
    context = SimpleNamespace(
        client=SimpleNamespace(
            transport=SimpleNamespace(
                dispatch=lambda route, _payload: {
                    "block_position": {"x": 0, "y": 65, "z": 0}
                }
                if route == "get_state"
                else {"id": "minecraft:air"}
            )
        ),
        state=SimpleNamespace(
            custom_data={
                "structures": {
                    "bootstrap_base": {
                        "origin": [300, 65, 300],
                        "crafting_table": [301, 65, 301],
                        "furnace": [302, 65, 301],
                        "supply_chest": [301, 65, 302],
                        "verified": True,
                    }
                }
            }
        ),
    )

    assert ConditionalWoodGatheringAction(needed_logs=4).execute(context).success
    assert gathered == [4]


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


def test_boot_base_recovery_stages_at_remote_checkpoint_before_resource_work(
    monkeypatch,
):
    class Transport:
        def dispatch(self, route, payload):
            if route == "get_block":
                return {"id": "minecraft:crafting_table"}
            if route == "get_state":
                return {
                    "block_position": {"x": 400, "y": 64, "z": -300},
                    "health": 20,
                    "food_level": 18,
                }
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
    staged = []
    monkeypatch.setattr(
        "baritone_client.actions.boot_sequence.append_world_map_entry",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        "baritone_client.actions.boot_sequence.require_survival_margin",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "baritone_client.actions.boot_sequence.goto",
        lambda _client, x, y, z, **kwargs: staged.append(
            (x, y, z, kwargs["timeout"])
        )
        or True,
    )

    result = BaseRecoveryAction().execute(
        SimpleNamespace(client=SimpleNamespace(transport=Transport()), state=state)
    )

    assert result.success
    assert staged == [(-8, 79, -121, 300)]


def test_boot_base_recovery_accepts_proven_horizontal_arrival(monkeypatch):
    states = iter(
        [
            {"block_position": {"x": 40, "y": 64, "z": 40}},
            {"block_position": {"x": -5, "y": 64, "z": -121}},
        ]
    )

    class Transport:
        def dispatch(self, route, payload):
            if route == "get_block":
                return {"id": "minecraft:crafting_table"}
            if route == "get_state":
                return next(states)
            return {}

    client = SimpleNamespace(transport=Transport())
    state = SimpleNamespace(
        custom_data={
            "structures": {
                "starter_house": {
                    "crafting_table": [-8, 79, -121],
                }
            }
        }
    )
    monkeypatch.setattr(
        "baritone_client.actions.boot_sequence.append_world_map_entry",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        "baritone_client.actions.boot_sequence.require_survival_margin",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "baritone_client.actions.boot_sequence.goto",
        lambda *_args, **_kwargs: False,
    )

    result = BaseRecoveryAction().execute(
        SimpleNamespace(client=client, state=state)
    )

    assert result.success


def test_boot_base_recovery_does_not_chase_opportunistic_remote_table(
    monkeypatch,
):
    class Transport:
        def dispatch(self, route, payload):
            if route == "waypoint":
                return {}
            if route == "get_state":
                return {
                    "block_position": {"x": 0, "y": 64, "z": 0},
                    "health": 20,
                    "food_level": 18,
                }
            return {}

    client = SimpleNamespace(transport=Transport())
    state = SimpleNamespace(custom_data={"structures": {}})
    monkeypatch.setattr(
        "baritone_client.actions.boot_sequence.find_nearby_block",
        lambda *_args, **_kwargs: (60, 64, 60),
    )
    monkeypatch.setattr(
        "baritone_client.actions.boot_sequence.append_world_map_entry",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        "baritone_client.actions.boot_sequence.goto",
        lambda *_args, **_kwargs: pytest.fail(
            "an opportunistic table is not a durable return anchor"
        ),
    )

    result = BaseRecoveryAction().execute(
        SimpleNamespace(client=client, state=state)
    )

    assert result.success


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


def test_storage_organization_uses_nearby_bootstrap_chest():
    local_chest = [318, 64, -415]
    remote_chest = [-213, 69, 137]

    class Transport:
        def dispatch(self, route, payload):
            if route == "get_state":
                return {
                    "block_position": {"x": 310, "y": 62, "z": -418}
                }
            if route == "get_block":
                position = [payload["x"], payload["y"], payload["z"]]
                return {
                    "id": (
                        "minecraft:chest"
                        if position == local_chest
                        else "minecraft:air"
                    )
                }
            return {}

    state = SimpleNamespace(
        custom_data={
            "structures": {
                "starter_house": {
                    "origin": [-214, 68, 135],
                    "supply_chest": remote_chest,
                },
                "bootstrap_base": {
                    "origin": [317, 64, -417],
                    "crafting_table": [318, 64, -416],
                    "furnace": [319, 64, -416],
                    "supply_chest": local_chest,
                },
            }
        }
    )
    context = SimpleNamespace(
        client=SimpleNamespace(transport=Transport()),
        state=state,
    )

    assert StorageOrganizationAction().execute(context).success


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


def test_bed_acquisition_uses_one_bounded_direct_craft(monkeypatch):
    inventory = {
        "minecraft:white_wool": 3,
        "minecraft:oak_planks": 3,
    }
    monkeypatch.setattr(
        "baritone_client.actions.boot_sequence.count_item",
        lambda _client, item_id: inventory.get(item_id, 0),
    )
    monkeypatch.setattr(
        "baritone_client.actions.boot_sequence.hunt_passive_mobs",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("existing wool must not trigger a hunt")
        ),
    )
    craft_calls = []
    monkeypatch.setattr(
        "baritone_client.actions.boot_sequence.craft",
        lambda _client, item_id, count: craft_calls.append((item_id, count))
        or False,
    )
    client = SimpleNamespace(
        transport=SimpleNamespace(
            dispatch=lambda route, _payload: {"world_time": 1000}
        )
    )

    result = BedAcquisitionAction().execute(
        SimpleNamespace(
            client=client,
            state=SimpleNamespace(custom_data={}),
        )
    )

    assert result.success
    assert craft_calls == [("minecraft:white_bed", 1)]


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
