from pathlib import Path
from types import SimpleNamespace

from baritone_client.operations import perpetual_builder as module
from baritone_client.operations import perpetual_build_runtime as runtime_module
from baritone_client.operations.perpetual_build_runtime import needs_food_provisioning


def test_fleet_lanes_are_disjoint_for_many_cells():
    lanes = module.fleet_lanes()
    cells = {}
    for bot, lane in lanes.items():
        occupied = {
            lane.cell(slot, route_index)
            for route_index in range(6)
            for slot in range(300)
        }
        assert not any(occupied & previous for previous in cells.values())
        cells[bot] = occupied


def test_material_selection_preserves_rare_items():
    inventory = {
        "minecraft:elytra": 1,
        "minecraft:ender_eye": 12,
        "minecraft:birch_planks": 8,
        "minecraft:cobblestone": 3,
    }
    assert module.select_build_material(inventory) == "minecraft:cobblestone"


def test_progress_round_trip_is_atomic(tmp_path):
    path = tmp_path / "Bot07.json"
    progress = module.BuildProgress(
        slot=4,
        expected_y=76,
        blocks_placed=3,
        materials_consumed={"minecraft:cobblestone": 3},
        last_position=(701, 76, -727),
    )
    progress.save(path)
    assert module.BuildProgress.load(path, expected_y=1) == progress
    assert not path.with_suffix(".json.tmp").exists()


def test_old_checkpoint_migrates_without_replaying_attempted_cells(tmp_path):
    path = tmp_path / "Bot07.json"
    path.write_text('{"slot": 1189, "expected_y": 72}', encoding="utf-8")
    progress = module.BuildProgress.load(path, expected_y=75)
    assert progress.route_index == 0
    assert progress.route_slot == 1189
    assert progress.route_frontiers == {"0": 1189}


class FakeTransport:
    def __init__(self, states=None):
        self.blocks = {}
        self.states = list(states or [builder_state()])
        self.calls = []

    def dispatch(self, route, payload):
        self.calls.append((route, payload))
        if route == "get_state":
            if len(self.states) > 1:
                return self.states.pop(0)
            return self.states[0]
        if route == "get_block":
            key = (payload["x"], payload["y"], payload["z"])
            return {"id": self.blocks.get(key, "minecraft:air")}
        return {}


def builder_state(**overrides):
    value = {
        "automation_profile": "non_op_client",
        "server_authority": False,
        "player_name": "Bot07",
        "dimension": "minecraft:overworld",
        "game_mode": "survival",
        "health": 20,
        "food_level": 20,
        "is_dead": False,
    }
    value.update(overrides)
    return value


def builder(tmp_path, transport, progress=None):
    subject = module.PerpetualBuilder(
        SimpleNamespace(transport=transport),
        module.LaneAssignment("Bot07", 700, -727),
        progress or module.BuildProgress(expected_y=75),
        checkpoint_path=Path(tmp_path) / "checkpoint.json",
        sleeper=lambda _seconds: None,
    )
    return subject, subject.client


def test_find_road_target_uses_natural_ground_not_existing_road():
    transport = FakeTransport()
    transport.blocks[(700, 74, -727)] = "minecraft:dirt"
    transport.blocks[(700, 75, -727)] = "minecraft:cobblestone"
    client = SimpleNamespace(transport=transport)
    assert module.find_road_target(client, 700, -727, expected_y=75) == (700, 75, -727)


def test_three_failures_skip_a_bad_cell(tmp_path):
    client = SimpleNamespace(transport=FakeTransport())
    progress = module.BuildProgress(expected_y=75)
    worker = module.PerpetualBuilder(
        client,
        module.LaneAssignment("Bot07", 700, -727),
        progress,
        checkpoint_path=Path(tmp_path) / "checkpoint.json",
        sleeper=lambda _seconds: None,
    )
    first = worker._fail_cell("bad cell")
    second = worker._fail_cell("bad cell")
    third = worker._fail_cell("bad cell")
    assert (first.state, second.state, third.state) == (
        "retrying",
        "retrying",
        "skipped",
    )
    assert progress.slot == 1
    assert progress.skipped_cells == 1


def test_sustained_bad_cells_pivot_to_westbound_route(tmp_path):
    client = SimpleNamespace(transport=FakeTransport())
    progress = module.BuildProgress(expected_y=75)
    worker = module.PerpetualBuilder(
        client,
        module.LaneAssignment("Bot07", 700, -727),
        progress,
        checkpoint_path=Path(tmp_path) / "checkpoint.json",
        sleeper=lambda _seconds: None,
    )
    result = None
    for _ in range(module.MAX_NONPRODUCTIVE_CELLS):
        for _attempt in range(module.MAX_CELL_FAILURES):
            result = worker._fail_cell("ocean")
    assert result is not None and result.state == "rerouted"
    assert progress.route_index == 1
    assert progress.route_slot == 0
    assert progress.reroutes == 1
    assert worker.assignment.cell(progress.route_slot, progress.route_index) == (
        699,
        -728,
    )


def test_verified_advance_resets_skip_streak(tmp_path):
    progress = module.BuildProgress(
        expected_y=75,
        consecutive_skips=3,
        nonproductive_cells=11,
    )
    worker = module.PerpetualBuilder(
        SimpleNamespace(transport=FakeTransport()),
        module.LaneAssignment("Bot07", 700, -727),
        progress,
        checkpoint_path=Path(tmp_path) / "checkpoint.json",
    )
    assert worker._advance(productive=True) is False
    assert progress.consecutive_skips == 0
    assert progress.nonproductive_cells == 0
    assert progress.route_slot == 1


def test_existing_cells_do_not_hide_a_stalled_frontier(tmp_path):
    progress = module.BuildProgress(expected_y=75, nonproductive_cells=11)
    worker = module.PerpetualBuilder(
        SimpleNamespace(transport=FakeTransport()),
        module.LaneAssignment("Bot07", 700, -727),
        progress,
        checkpoint_path=Path(tmp_path) / "checkpoint.json",
    )
    assert worker._advance() is True
    assert progress.route_index == 1
    assert progress.reroutes == 1


def test_critical_health_requires_food_recovery_even_when_hunger_is_above_floor():
    assert needs_food_provisioning(health=2, food=17, reserve=0) is True
    assert needs_food_provisioning(health=20, food=15, reserve=0) is True
    assert needs_food_provisioning(health=20, food=17, reserve=0) is False
    assert needs_food_provisioning(health=2, food=17, reserve=8) is False


def test_wrong_dimension_cancels_and_persists_manual_recovery_hold(tmp_path):
    transport = FakeTransport([builder_state(dimension="minecraft:the_end")])
    subject, _client = builder(tmp_path, transport)

    turn = subject.run_turn()

    assert turn.state == "manual_recovery_hold"
    assert "outside the Overworld" in turn.detail
    assert ("cancel", {}) in transport.calls
    restored = module.BuildProgress.load(
        Path(tmp_path) / "checkpoint.json",
        expected_y=75,
    )
    assert restored.manual_recovery_reason == turn.detail


def test_identity_mismatch_holds_without_sending_any_mutation(tmp_path):
    transport = FakeTransport([builder_state(player_name="Bot19")])
    subject, _client = builder(tmp_path, transport)

    turn = subject.run_turn()

    assert turn.state == "manual_recovery_hold"
    assert "identity mismatch" in turn.detail
    assert transport.calls == [("get_state", {})]


def test_missing_bridge_state_holds_without_sending_any_mutation(tmp_path):
    transport = FakeTransport([{}])
    subject, _client = builder(tmp_path, transport)

    turn = subject.run_turn()

    assert turn.state == "manual_recovery_hold"
    assert turn.detail == "bridge state is unavailable"
    assert transport.calls == [("get_state", {})]


def test_critical_health_cancels_and_holds_without_movement(tmp_path):
    transport = FakeTransport([builder_state(health=5, food_level=20)])
    subject, _client = builder(tmp_path, transport)

    turn = subject.run_turn()

    assert turn.state == "manual_recovery_hold"
    assert "critical survival margin" in turn.detail
    assert transport.calls == [("get_state", {}), ("cancel", {})]


def test_verified_manual_recovery_clears_persisted_hold(tmp_path, monkeypatch):
    progress = module.BuildProgress(
        expected_y=75,
        manual_recovery_reason="operator action required",
        manual_recovery_at=1.0,
    )
    transport = FakeTransport([builder_state(health=18, food_level=18)])
    subject, _client = builder(tmp_path, transport, progress=progress)
    monkeypatch.setattr(module, "defend_or_flee", lambda *_args, **_kwargs: True)

    turn = subject.run_turn()

    assert turn.state == "defending"
    assert progress.manual_recovery_reason is None
    assert progress.manual_recovery_at is None


def test_normal_respawn_returns_manual_hold_without_admin_restage(
    tmp_path,
    monkeypatch,
):
    transport = FakeTransport(
        [
            builder_state(health=0, is_dead=True),
            builder_state(health=20, is_dead=False),
        ]
    )
    subject, client = builder(tmp_path, transport)
    runtime = object.__new__(runtime_module.PerpetualBuildRuntime)
    runtime.bot_name = "Bot07"
    runtime.worker = subject
    monkeypatch.setattr(runtime_module.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(runtime_module, "reset_inventory_cache", lambda: None)

    turn = runtime._recover(client)

    assert turn is not None and turn.state == "manual_recovery_hold"
    assert "respawned normally" in turn.detail
    assert ("respawn", {}) in transport.calls
    assert {route for route, _payload in transport.calls} <= {
        "get_state",
        "cancel",
        "respawn",
    }


def test_runtime_has_no_rcon_or_admin_movement_surface():
    source = Path(runtime_module.__file__).read_text(encoding="utf-8")
    assert "RconClient" not in source
    assert "spreadplayers" not in source
    assert 'command(f"tp ' not in source
    assert "_restage" not in source
