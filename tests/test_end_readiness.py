from types import SimpleNamespace

from baritone_client.automator.end_readiness import (
    FleetRole,
    fleet_role,
    readiness_snapshot,
    record_readiness,
    role_focused_candidates,
)
from baritone_client.automator.objective import ObjStatus, ObjectivePlanner, default_objectives
from baritone_client.automator.state_manager import Phase


def _post_food_planner() -> ObjectivePlanner:
    planner = ObjectivePlanner(default_objectives())
    for phase in (
        Phase.BRIDGE_CHECK,
        Phase.SPAWN_BOOTSTRAP,
        Phase.INITIAL_GATHERING,
        Phase.BOOT_SEQUENCE,
        Phase.BASE_CONSTRUCTION,
        Phase.FOOD_AND_IRON,
    ):
        planner._by_phase[phase].status = ObjStatus.DONE
    return planner


def test_bot_number_shards_current_fleet_into_stable_roles(tmp_path):
    expected = {
        "Bot07": FleetRole.END_RUNNER,
        "Bot16": FleetRole.NETHER_SUPPLY,
        "Bot17": FleetRole.ENCHANTING,
        "Bot18": FleetRole.VILLAGE_FOOD,
    }
    for bot, role in expected.items():
        state = SimpleNamespace(checkpoint_dir=tmp_path / bot / "controller")
        assert fleet_role(state) is role


def test_checkpoint_role_file_persists_manual_wood_assignment(tmp_path, monkeypatch):
    monkeypatch.delenv("MC_FLEET_ROLE", raising=False)
    controller = tmp_path / "Bot20" / "controller"
    controller.mkdir(parents=True)
    (controller / "fleet-role.txt").write_text("wood_supply\n", encoding="utf-8")

    assert fleet_role(SimpleNamespace(checkpoint_dir=controller)) is FleetRole.WOOD_SUPPLY


def test_checkpoint_role_file_persists_manual_iron_assignment(tmp_path, monkeypatch):
    monkeypatch.delenv("MC_FLEET_ROLE", raising=False)
    controller = tmp_path / "Bot15" / "controller"
    controller.mkdir(parents=True)
    (controller / "fleet-role.txt").write_text("iron_supply\n", encoding="utf-8")

    assert fleet_role(SimpleNamespace(checkpoint_dir=controller)) is FleetRole.IRON_SUPPLY


def test_checkpoint_role_file_persists_manual_quartermaster_assignment(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("MC_FLEET_ROLE", raising=False)
    controller = tmp_path / "Bot04" / "controller"
    controller.mkdir(parents=True)
    (controller / "fleet-role.txt").write_text(
        "quartermaster\n", encoding="utf-8"
    )

    assert fleet_role(SimpleNamespace(checkpoint_dir=controller)) is FleetRole.QUARTERMASTER


def test_end_runner_stays_on_nether_and_does_not_rotate_when_abandoned():
    planner = _post_food_planner()
    candidates, reason, complete = role_focused_candidates(
        planner.runnable(),
        planner.objectives,
        planner.completed_phases(),
        FleetRole.END_RUNNER,
    )
    assert [candidate.phase for candidate in candidates] == [Phase.NETHER_AND_BLAZE]
    assert "end_runner" in reason
    assert complete is False

    planner._by_phase[Phase.NETHER_AND_BLAZE].status = ObjStatus.ABANDONED
    candidates, _reason, complete = role_focused_candidates(
        planner.runnable(),
        planner.objectives,
        planner.completed_phases(),
        FleetRole.END_RUNNER,
    )
    assert candidates == []
    assert complete is False


def test_end_runner_advances_to_world_unlock_after_verified_nether():
    planner = _post_food_planner()
    planner._by_phase[Phase.NETHER_AND_BLAZE].status = ObjStatus.DONE

    candidates, reason, complete = role_focused_candidates(
        planner.runnable(),
        planner.objectives,
        planner.completed_phases(),
        FleetRole.END_RUNNER,
    )

    assert [candidate.phase for candidate in candidates] == [Phase.WORLD_UNLOCK]
    assert "WORLD_UNLOCK" in reason
    assert complete is False


def test_completed_nether_supplier_waits_for_recurring_cycle_without_graph_churn():
    planner = _post_food_planner()
    planner._by_phase[Phase.NETHER_AND_BLAZE].status = ObjStatus.DONE

    candidates, reason, complete = role_focused_candidates(
        planner.runnable(),
        planner.objectives,
        planner.completed_phases(),
        FleetRole.NETHER_SUPPLY,
    )

    assert candidates == []
    assert "recurring supply cycle" in reason
    assert complete is True


def test_wood_supplier_holds_for_renewable_work_after_safe_bootstrap():
    planner = _post_food_planner()

    candidates, reason, complete = role_focused_candidates(
        planner.runnable(),
        planner.objectives,
        planner.completed_phases(),
        FleetRole.WOOD_SUPPLY,
    )

    assert candidates == []
    assert "renewable forestry" in reason
    assert complete is True


def test_wood_supplier_does_not_skip_bootstrap_dependencies():
    planner = ObjectivePlanner(default_objectives())
    planner.restore([Phase.BRIDGE_CHECK, Phase.SPAWN_BOOTSTRAP])

    candidates, reason, complete = role_focused_candidates(
        planner.runnable(),
        planner.objectives,
        planner.completed_phases(),
        FleetRole.WOOD_SUPPLY,
    )

    assert candidates
    assert reason == ""
    assert complete is False


def test_iron_supplier_holds_for_bounded_mining_after_initial_setup():
    planner = ObjectivePlanner(default_objectives())
    planner.restore(
        [Phase.BRIDGE_CHECK, Phase.SPAWN_BOOTSTRAP, Phase.INITIAL_GATHERING]
    )

    candidates, reason, complete = role_focused_candidates(
        planner.runnable(),
        planner.objectives,
        planner.completed_phases(),
        FleetRole.IRON_SUPPLY,
    )

    assert candidates == []
    assert "bounded team mining" in reason
    assert complete is True


def test_iron_supplier_does_not_skip_initial_gathering():
    planner = ObjectivePlanner(default_objectives())
    planner.restore([Phase.BRIDGE_CHECK, Phase.SPAWN_BOOTSTRAP])

    candidates, reason, complete = role_focused_candidates(
        planner.runnable(),
        planner.objectives,
        planner.completed_phases(),
        FleetRole.IRON_SUPPLY,
    )

    assert candidates
    assert reason == ""
    assert complete is False


def test_quartermaster_holds_for_recurring_storage_after_boot_sequence():
    planner = _post_food_planner()

    candidates, reason, complete = role_focused_candidates(
        planner.runnable(),
        planner.objectives,
        planner.completed_phases(),
        FleetRole.QUARTERMASTER,
    )

    assert candidates == []
    assert "shared storage" in reason
    assert complete is True


def test_readiness_requires_eye_path_and_complete_launch_loadout():
    inventory = {
        "minecraft:blaze_rod": 6,
        "minecraft:ender_pearl": 12,
        "minecraft:obsidian": 10,
        "minecraft:iron_ingot": 24,
        "minecraft:cooked_beef": 32,
        "minecraft:iron_helmet": 1,
        "minecraft:iron_chestplate": 1,
        "minecraft:iron_leggings": 1,
        "minecraft:iron_boots": 1,
        "minecraft:shield": 1,
        "minecraft:iron_sword": 1,
        "minecraft:iron_pickaxe": 1,
        "minecraft:bow": 1,
        "minecraft:arrow": 32,
        "minecraft:water_bucket": 1,
    }

    snapshot = readiness_snapshot(inventory, role=FleetRole.END_RUNNER)

    assert snapshot["craftable_eye_total"] == 12
    assert snapshot["launch_ready"] is True
    assert snapshot["missing"] == []


def test_readiness_persists_baseline_and_net_supply_change(tmp_path):
    state = SimpleNamespace(custom_data={}, checkpoint_dir=tmp_path / "Bot07" / "controller")
    completed = {Phase.BRIDGE_CHECK, Phase.FOOD_AND_IRON}
    record_readiness(
        state,
        {"minecraft:blaze_rod": 1},
        completed=completed,
        known_portal=False,
        now=100.0,
    )
    snapshot = record_readiness(
        state,
        {"minecraft:blaze_rod": 7},
        completed={*completed, Phase.NETHER_AND_BLAZE},
        known_portal=True,
        now=200.0,
    )

    assert snapshot["baseline"]["supplies"]["blaze_rods"] == 1
    assert snapshot["net_since_baseline"]["blaze_rods"] == 6
    assert snapshot["net_verified_objectives"] == 1
    assert snapshot["last_progress_at"] == 200.0

    unchanged = record_readiness(
        state,
        {"minecraft:blaze_rod": 7},
        completed={*completed, Phase.NETHER_AND_BLAZE},
        known_portal=True,
        now=300.0,
    )
    assert unchanged["last_progress_at"] == 200.0
