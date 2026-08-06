import json

from tests.functional.survival import create_extended_suite_1200
from tests.functional.survival.evidence import (
    PROGRESSION_GATES,
    ProgressionEvidence,
    gate_by_id,
    load_checkpoint,
)


class FakeContext:
    def __init__(self, *, state=None, inventory=None, armor=None, offhand=None, blocks=None):
        self._state = state or {
            "health": 20,
            "is_dead": False,
            "dimension": "minecraft:overworld",
        }
        self._inventory = inventory or []
        self._armor = armor or []
        self._offhand = offhand or []
        self._blocks = blocks or {}

    def get_state(self):
        return dict(self._state)

    def get_inventory(self):
        return {
            "inventory": list(self._inventory),
            "armor": list(self._armor),
            "offhand": list(self._offhand),
        }

    def get_block(self, x, y, z):
        return {"id": self._blocks.get((x, y, z), "minecraft:air")}


def write_checkpoint(tmp_path, payload):
    path = tmp_path / "spawn_to_dragon_checkpoint.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_progression_gate_ids_are_unique_and_ordered():
    ids = [gate.id for gate in PROGRESSION_GATES]
    assert ids == [f"T{number}" for number in range(1200, 1215)]
    assert len(ids) == len(set(ids))
    assert all(gate.checks for gate in PROGRESSION_GATES)


def test_suite_1200_is_read_only_by_construction():
    suite = create_extended_suite_1200()
    assert suite.name == "Suite_1200_Survival_Progression"
    assert [test.id for test in suite.tests] == [gate.id for gate in PROGRESSION_GATES]
    assert all(test.setup is None for test in suite.tests)
    assert all(test.teardown is None for test in suite.tests)
    assert all(test.steps == [] for test in suite.tests)
    assert all(len(test.assertions) == 1 for test in suite.tests)


def test_checkpoint_loader_returns_utf8_json(tmp_path):
    path = write_checkpoint(tmp_path, {"phase": "INITIAL_GATHERING", "world_seed": 123})
    payload, resolved = load_checkpoint(path)
    assert resolved == path.resolve()
    assert payload["phase"] == "INITIAL_GATHERING"
    assert payload["world_seed"] == 123


def test_initial_resources_requires_phase_completion(tmp_path):
    path = write_checkpoint(
        tmp_path,
        {
            "phase": "INITIAL_GATHERING",
            "phase_progress": {"INITIAL_GATHERING": 0.0},
            "inventory_summary": {
                "minecraft:oak_log": 16,
                "minecraft:cobblestone": 64,
                "minecraft:stone_pickaxe": 1,
            },
        },
    )
    evidence = ProgressionEvidence.capture(FakeContext(), path)
    passed, message = gate_by_id("T1202").evaluate(evidence)
    assert not passed
    assert "production phase INITIAL_GATHERING completed" in message

    checkpoint = json.loads(path.read_text(encoding="utf-8"))
    checkpoint["phase_progress"]["INITIAL_GATHERING"] = 1.0
    path.write_text(json.dumps(checkpoint), encoding="utf-8")
    evidence = ProgressionEvidence.capture(FakeContext(), path)
    passed, message = gate_by_id("T1202").evaluate(evidence)
    assert passed, message


def test_house_gate_probes_persisted_plan(tmp_path):
    origin = (10, 64, 20)
    house = {
        "origin": list(origin),
        "door": [13, 65, 20],
        "crafting_table": [11, 65, 21],
        "supply_chest": [11, 65, 22],
    }
    path = write_checkpoint(
        tmp_path,
        {
            "phase": "BOOT_SEQUENCE",
            "phase_progress": {"BASE_CONSTRUCTION": 1.0},
            "custom_data": {
                "base_location": list(origin),
                "structures": {"starter_house": house},
            },
        },
    )
    ox, oy, oz = origin
    blocks = {
        (ox + 1, oy, oz + 1): "minecraft:cobblestone",
        (ox + 5, oy, oz + 5): "minecraft:cobblestone",
        (ox + 1, oy + 2, oz): "minecraft:oak_planks",
        (ox + 5, oy + 2, oz + 6): "minecraft:oak_planks",
        (ox, oy + 1, oz + 3): "minecraft:oak_planks",
        (ox + 3, oy + 4, oz + 3): "minecraft:oak_planks",
        (13, 65, 20): "minecraft:oak_door",
        (11, 65, 21): "minecraft:crafting_table",
        (11, 65, 22): "minecraft:chest",
    }
    evidence = ProgressionEvidence.capture(FakeContext(blocks=blocks), path)
    passed, message = gate_by_id("T1203").evaluate(evidence)
    assert passed, message

    del blocks[(ox + 3, oy + 4, oz + 3)]
    evidence = ProgressionEvidence.capture(FakeContext(blocks=blocks), path)
    passed, message = gate_by_id("T1203").evaluate(evidence)
    assert not passed
    assert "walls, roof, door, crafting table, and chest" in message


def test_eyes_gate_uses_real_minecraft_item_id(tmp_path):
    wrong_path = write_checkpoint(
        tmp_path,
        {"inventory_summary": {"minecraft:eye_of_ender": 12}},
    )
    wrong = ProgressionEvidence.capture(FakeContext(), wrong_path)
    assert not gate_by_id("T1208").evaluate(wrong)[0]

    right_path = write_checkpoint(
        tmp_path,
        {"inventory_summary": {"minecraft:ender_eye": 12}},
    )
    right = ProgressionEvidence.capture(FakeContext(), right_path)
    assert gate_by_id("T1208").evaluate(right)[0]


def test_equipped_armor_and_offhand_count_as_current_inventory(tmp_path):
    path = write_checkpoint(tmp_path, {})
    context = FakeContext(
        armor=[{"id": "minecraft:iron_chestplate", "count": 1}],
        offhand=[{"id": "minecraft:shield", "count": 1}],
    )
    evidence = ProgressionEvidence.capture(context, path)
    assert evidence.current_count("minecraft:iron_chestplate") == 1
    assert evidence.current_count("minecraft:shield") == 1


def test_dragon_gate_requires_explicit_durable_evidence(tmp_path):
    path = write_checkpoint(
        tmp_path,
        {
            "phase": "MEGABASE_INIT",
            "phase_progress": {"WORLD_UNLOCK": 1.0},
            "custom_data": {},
        },
    )
    evidence = ProgressionEvidence.capture(FakeContext(), path)
    assert not gate_by_id("T1211").evaluate(evidence)[0]

    checkpoint = json.loads(path.read_text(encoding="utf-8"))
    checkpoint["custom_data"] = {"milestones": {"dragon_defeated": True}}
    path.write_text(json.dumps(checkpoint), encoding="utf-8")
    evidence = ProgressionEvidence.capture(FakeContext(), path)
    assert gate_by_id("T1211").evaluate(evidence)[0]
