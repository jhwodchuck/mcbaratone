from types import SimpleNamespace

from baritone_client.common import base
from tests.functional.shared import block_ops


class _WorldTransport:
    def __init__(self, blocks=None):
        self.blocks = blocks or {}

    def dispatch(self, command, payload):
        if command == "get_block":
            key = (payload["x"], payload["y"], payload["z"])
            return {"id": self.blocks.get(key, "minecraft:air")}
        if command == "get_state":
            return {"world_time": 1000}
        if command == "break_block":
            key = (payload["x"], payload["y"], payload["z"])
            self.blocks.pop(key, None)
            return {"success": True}
        return {}


def test_door_placement_clears_recovered_wall_from_both_doorway_cells(monkeypatch):
    origin = (10, 64, 20)
    door = (origin[0] + 3, origin[1] + 1, origin[2])
    transport = _WorldTransport(
        {
            door: "minecraft:oak_planks",
            (door[0], door[1] + 1, door[2]): "minecraft:oak_planks",
        }
    )
    client = SimpleNamespace(transport=transport)

    monkeypatch.setattr(
        "baritone_client.common.harness_ops.move_near", lambda *_args, **_kwargs: True
    )
    monkeypatch.setattr(base, "select_item", lambda *_args, **_kwargs: True)

    def dispatch(command, payload):
        result = _WorldTransport.dispatch(transport, command, payload)
        if command == "place_block":
            transport.blocks[door] = payload["block"]
        return result

    transport.dispatch = dispatch

    assert base._place_north_wall_door(client, *door, "minecraft:oak_door")
    assert transport.blocks[door] == "minecraft:oak_door"
    assert (door[0], door[1] + 1, door[2]) not in transport.blocks


def test_good_house_repairs_existing_structure_instead_of_rebuilding(monkeypatch):
    origin = (10, 64, 20)
    plan = base._good_house_plan(*origin)
    blocks = {}
    for x, y, z, role in plan:
        blocks[(x, y, z)] = (
            "minecraft:cobblestone" if role == "floor" else "minecraft:oak_planks"
        )

    missing_floor = next(target for target in plan if target[3] == "floor")
    missing_wall = next(target for target in plan if target[3] == "shell")
    blocks.pop(missing_floor[:3])
    blocks.pop(missing_wall[:3])
    door_pos = (origin[0] + 3, origin[1] + 1, origin[2])
    blocks[door_pos] = "minecraft:oak_door"

    transport = _WorldTransport(blocks)
    client = SimpleNamespace(transport=transport)
    placements = []

    def count(_client, item_id):
        if item_id == "minecraft:cobblestone":
            return 9
        if item_id == "minecraft:oak_planks":
            return 9
        return 0

    def place(_client, x, y, z, item_id):
        placements.append((x, y, z, item_id))
        transport.blocks[(x, y, z)] = item_id
        return True

    monkeypatch.setattr(base, "count_item", count)
    monkeypatch.setattr(base, "robust_place", place)
    monkeypatch.setattr(base, "wait_for_safe_daylight", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        "baritone_client.common.harness_ops.move_near", lambda *_args, **_kwargs: True
    )

    assert base.build_good_house(client, *origin)
    assert {placement[:3] for placement in placements} == {
        missing_floor[:3],
        missing_wall[:3],
    }


def test_house_repair_disables_path_breaking_during_exact_placement(monkeypatch):
    origin = (10, 64, 20)
    plan = base._good_house_plan(*origin)
    blocks = {
        (x, y, z): (
            "minecraft:cobblestone" if role == "floor" else "minecraft:oak_planks"
        )
        for x, y, z, role in plan
    }
    missing = next(target for target in plan if target[3] == "shell")
    blocks.pop(missing[:3])
    blocks[(origin[0] + 3, origin[1] + 1, origin[2])] = "minecraft:oak_door"

    class Transport(_WorldTransport):
        def __init__(self):
            super().__init__(blocks)
            self.allow_break = True

        def dispatch(self, command, payload):
            if command == "chat":
                self.allow_break = payload["message"].endswith(" true")
                return {}
            return super().dispatch(command, payload)

    transport = Transport()
    client = SimpleNamespace(transport=transport)
    monkeypatch.setattr(base, "count_item", lambda *_args: 64)
    monkeypatch.setattr(base, "wait_for_safe_daylight", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        "baritone_client.common.harness_ops.move_near", lambda *_args, **_kwargs: True
    )

    def place(_client, x, y, z, item_id):
        assert transport.allow_break is False
        transport.blocks[(x, y, z)] = item_id
        return True

    monkeypatch.setattr(base, "robust_place", place)

    assert base.build_good_house(client, *origin)
    assert transport.allow_break is True


def test_good_house_rejects_even_one_unrepaired_survival_shell_hole(monkeypatch):
    origin = (10, 64, 20)
    plan = base._good_house_plan(*origin)
    blocks = {
        (x, y, z): (
            "minecraft:cobblestone" if role == "floor" else "minecraft:oak_planks"
        )
        for x, y, z, role in plan
    }
    missing_wall = next(target for target in plan if target[3] == "shell")
    blocks.pop(missing_wall[:3])
    blocks[(origin[0] + 3, origin[1] + 1, origin[2])] = "minecraft:oak_door"
    client = SimpleNamespace(transport=_WorldTransport(blocks))

    monkeypatch.setattr(base, "count_item", lambda *_args: 64)
    monkeypatch.setattr(base, "wait_for_safe_daylight", lambda *_args, **_kwargs: True)
    # Simulate an acknowledged placement whose world block never appeared.
    monkeypatch.setattr(base, "robust_place", lambda *_args, **_kwargs: True)

    assert not base.build_good_house(client, *origin)


def test_exact_placement_accepts_matching_existing_block_without_inventory(monkeypatch):
    class Context:
        client = SimpleNamespace()

        def get_block(self, *_args):
            return {"id": "minecraft:dark_oak_planks"}

    monkeypatch.setattr(
        block_ops,
        "select_item",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("an existing target must not require inventory")
        ),
    )

    assert block_ops.place_block_at(
        Context(), 1, 2, 3, "minecraft:dark_oak_planks"
    )


def test_house_plan_has_expected_floor_wall_and_roof_counts():
    plan = base._good_house_plan(0, 64, 0)
    roles = [target[3] for target in plan]

    assert len(plan) == 168
    assert roles.count("floor") == 49
    assert roles.count("shell") == 70
    assert roles.count("roof") == 49


def test_cobblestone_is_valid_for_wall_patch_but_not_roof():
    assert base._matches_house_role("minecraft:cobblestone", "shell")
    assert not base._matches_house_role("minecraft:cobblestone", "roof")


def test_starter_house_rejects_east_west_door_alignment():
    class DoorTransport:
        def __init__(self, facing):
            self.facing = facing

        def dispatch(self, _route, _payload):
            return {
                "id": "minecraft:birch_door",
                "state": {"facing": self.facing},
            }

    assert base._house_door_aligned(
        SimpleNamespace(transport=DoorTransport("south")), 0, 65, 0
    )
    assert not base._house_door_aligned(
        SimpleNamespace(transport=DoorTransport("east")), 0, 65, 0
    )


def test_house_crafting_table_is_placed_at_exact_requested_coordinate(monkeypatch):
    blocks = {}

    class Transport:
        def dispatch(self, command, payload):
            if command == "get_block":
                return {
                    "id": blocks.get(
                        (payload["x"], payload["y"], payload["z"]),
                        "minecraft:air",
                    )
                }
            return {}

    client = SimpleNamespace(transport=Transport())
    placed = []
    monkeypatch.setattr(
        base,
        "count_item",
        lambda _client, item_id: 1 if item_id == "minecraft:crafting_table" else 0,
    )
    monkeypatch.setattr(base, "is_position_safe", lambda *_args: True)
    def place(_client, x, y, z, item_id):
        placed.append((x, y, z, item_id))
        blocks[(x, y, z)] = item_id
        return True

    monkeypatch.setattr(base, "robust_place", place)
    monkeypatch.setattr(base.time, "sleep", lambda _seconds: None)

    assert base.place_crafting_table(client, 11, 65, 21)
    assert placed == [(11, 65, 21, "minecraft:crafting_table")]


def test_open_crafting_table_uses_verified_adjacent_harness_not_solid_goal(monkeypatch):
    client = SimpleNamespace(transport=SimpleNamespace())
    monkeypatch.setattr(
        base,
        "find_nearby_block",
        lambda *_args, **_kwargs: (-8, 79, -121),
    )
    monkeypatch.setattr(
        "baritone_client.common.harness_ops.available", lambda: True
    )
    opened = []
    monkeypatch.setattr(
        "baritone_client.common.harness_ops.ensure_crafting_table_open",
        lambda _client, table_pos: opened.append(table_pos) or True,
    )
    monkeypatch.setattr(
        base,
        "goto",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("must not path to the solid table block")
        ),
    )

    assert base.open_crafting_table(client)
    assert opened == [(-8, 79, -121)]


def test_house_stages_beside_door_before_placing_it(monkeypatch):
    origin = (10, 64, 20)
    plan = base._good_house_plan(*origin)
    blocks = {
        (x, y, z): (
            "minecraft:cobblestone" if role == "floor" else "minecraft:oak_planks"
        )
        for x, y, z, role in plan
    }
    transport = _WorldTransport(blocks)
    client = SimpleNamespace(transport=transport)
    staged = []

    def count(_client, item_id):
        if item_id == "minecraft:oak_door":
            return 1
        if item_id == "minecraft:oak_planks":
            return 18
        if item_id == "minecraft:cobblestone":
            return 8
        return 0

    def place(_client, x, y, z, item_id):
        transport.blocks[(x, y, z)] = item_id
        return True

    monkeypatch.setattr(base, "count_item", count)
    monkeypatch.setattr(base, "robust_place", place)
    monkeypatch.setattr(base, "wait_for_safe_daylight", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        "baritone_client.common.harness_ops.move_near",
        lambda _client, x, y, z, timeout: staged.append((x, y, z, timeout)) or True,
    )
    monkeypatch.setattr(
        base,
        "_place_north_wall_door",
        lambda _client, x, y, z, item_id: staged.append((x, y, z, 20.0))
        or transport.blocks.__setitem__((x, y, z), item_id)
        or True,
    )

    assert base.build_good_house(client, *origin)
    assert staged == [(13, 65, 18, 20.0), (13, 65, 20, 20.0)]


def test_house_repair_stages_outside_before_gathering(monkeypatch):
    origin = (10, 64, 20)
    plan = base._good_house_plan(*origin)
    blocks = {
        (x, y, z): (
            "minecraft:cobblestone" if role == "floor" else "minecraft:oak_planks"
        )
        for x, y, z, role in plan
    }
    missing = next(target for target in plan if target[3] == "floor")
    blocks.pop(missing[:3])
    blocks[(origin[0] + 3, origin[1] + 1, origin[2])] = "minecraft:oak_door"

    class Transport(_WorldTransport):
        def __init__(self):
            super().__init__(blocks)
            self.allow_break = True

        def dispatch(self, command, payload):
            if command == "chat":
                self.allow_break = payload["message"].endswith(" true")
                return {}
            return super().dispatch(command, payload)

    transport = Transport()
    client = SimpleNamespace(transport=transport)
    staged = []
    monkeypatch.setattr(base, "count_item", lambda *_args: 64)
    monkeypatch.setattr(base, "wait_for_safe_daylight", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        "baritone_client.common.harness_ops.move_near",
        lambda _client, x, y, z, timeout: staged.append(
            (x, y, z, timeout, transport.allow_break)
        )
        or True,
    )

    def place(_client, x, y, z, item_id):
        transport.blocks[(x, y, z)] = item_id
        return True

    monkeypatch.setattr(base, "robust_place", place)

    assert base.build_good_house(client, *origin)
    assert staged[0] == (13, 65, 18, 20.0, False)
    assert transport.allow_break is True


def test_house_door_uses_plank_family_with_enough_material(monkeypatch):
    origin = (10, 64, 20)
    plan = base._good_house_plan(*origin)
    blocks = {
        (x, y, z): (
            "minecraft:cobblestone" if role == "floor" else "minecraft:oak_planks"
        )
        for x, y, z, role in plan
    }
    transport = _WorldTransport(blocks)
    client = SimpleNamespace(transport=transport)
    crafted = []

    def count(_client, item_id):
        counts = {
            "minecraft:oak_planks": 3,
            "minecraft:birch_planks": 16,
            "minecraft:cobblestone": 8,
        }
        if crafted and item_id == "minecraft:birch_door":
            return 3
        return counts.get(item_id, 0)

    def craft_item(_client, item_id, count):
        crafted.append((item_id, count))
        return item_id == "minecraft:birch_door"

    def place(_client, x, y, z, item_id):
        transport.blocks[(x, y, z)] = item_id
        return True

    monkeypatch.setattr(base, "count_item", count)
    monkeypatch.setattr(base, "craft", craft_item)
    monkeypatch.setattr(base, "robust_place", place)
    monkeypatch.setattr(
        base,
        "_place_north_wall_door",
        lambda _client, x, y, z, item_id: transport.blocks.__setitem__(
            (x, y, z), item_id
        )
        or True,
    )
    monkeypatch.setattr(base, "wait_for_safe_daylight", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        "baritone_client.common.harness_ops.move_near", lambda *_args, **_kwargs: True
    )

    assert base.build_good_house(client, *origin)
    assert crafted == [("minecraft:birch_door", 1)]


def test_house_plank_gather_uses_absolute_target_and_converts_matching_logs(monkeypatch):
    inventory = {
        "minecraft:birch_planks": 12,
        "minecraft:birch_log": 0,
    }
    gather_targets = []
    crafted = []
    client = SimpleNamespace(transport=_WorldTransport())

    monkeypatch.setattr(
        base, "count_item", lambda _client, item_id: inventory.get(item_id, 0)
    )

    def gather(_client, count):
        gather_targets.append(count)
        inventory["minecraft:birch_log"] = 2
        return True

    def craft_item(_client, item_id, count):
        crafted.append((item_id, count))
        inventory["minecraft:birch_log"] -= count // 4
        inventory[item_id] = inventory.get(item_id, 0) + count
        return True

    monkeypatch.setattr("baritone_client.common.resources.gather_wood", gather)
    monkeypatch.setattr(base, "craft", craft_item)

    assert base._prepare_house_planks(client, 18)
    assert gather_targets == [5]
    assert crafted == [("minecraft:birch_planks", 8)]


def test_existing_house_enclosure_is_recognized():
    player = (0, 65, 0)
    blocks = {(0, 68, 0): "minecraft:oak_planks"}
    for distance, position in (
        (2, (2, 65, 0)),
        (2, (-2, 65, 0)),
        (3, (0, 65, 3)),
        (3, (0, 65, -3)),
    ):
        _ = distance
        blocks[position] = "minecraft:oak_planks"
        blocks[(position[0], position[1] + 1, position[2])] = "minecraft:oak_planks"

    transport = _WorldTransport(blocks)
    client = SimpleNamespace(transport=transport)
    state = {
        "block_position": {"x": player[0], "y": player[1], "z": player[2]}
    }

    assert base._has_existing_enclosure(client, state)


def test_loot_chest_does_not_shift_click_player_inventory(monkeypatch):
    clicks = []

    class Transport:
        def dispatch(self, command, payload):
            if command == "find_blocks":
                return {"found": [{"x": 1, "y": 65, "z": 1}]}
            if command == "get_screen":
                return {
                    "data": {
                        "total_slots": 63,
                        "slots": [{"slot": i} for i in range(63)],
                    }
                }
            if command == "inventory_click":
                clicks.append(payload["slot"])
            return {}

    client = SimpleNamespace(transport=Transport())
    monkeypatch.setattr(base, "goto", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(base, "open_chest", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(base.time, "sleep", lambda _seconds: None)

    assert base.loot_nearby_chests(client, radius=16)
    assert clicks == list(range(27))
