from types import SimpleNamespace

from baritone_client.common import workstation_safety


class WaterTransport:
    def dispatch(self, route, payload):
        if route == "get_state":
            return {"block_position": {"x": 8, "y": 62, "z": -4}}
        if route == "get_block":
            return {"id": "minecraft:water"}
        return {}


class LedgeTransport:
    def dispatch(self, route, payload):
        if route == "get_state":
            return {"block_position": {"x": 4, "y": 101, "z": -10}}
        if route == "get_block":
            position = (payload["x"], payload["y"], payload["z"])
            if position == (4, 100, -10):
                return {"id": "minecraft:dirt"}
            return {"id": "minecraft:air"}
        return {}


def test_ledge_workstation_extends_support_before_placing_table(monkeypatch):
    client = SimpleNamespace(transport=LedgeTransport())
    placements = []
    monkeypatch.setattr(
        workstation_safety,
        "count_item",
        lambda _client, item_id: (
            24
            if item_id == "minecraft:dirt"
            else 1
            if item_id == "minecraft:crafting_table"
            else 0
        ),
    )
    monkeypatch.setattr(
        workstation_safety.harness_ops,
        "place_block_exact",
        lambda _client, x, y, z, block, allow_break: placements.append(
            (x, y, z, block, allow_break)
        )
        or True,
    )

    assert workstation_safety.place_ledge_supported_workstation(client) == (
        5,
        101,
        -10,
    )
    assert placements == [
        (5, 100, -10, "minecraft:dirt", False),
        (5, 101, -10, "minecraft:crafting_table", False),
    ]


def test_descent_repositions_from_temporary_ledge_to_backed_column(monkeypatch):
    class DescentTransport:
        def dispatch(self, route, payload):
            if route != "get_block":
                return {}
            position = (payload["x"], payload["y"], payload["z"])
            blocks = {
                (5, 100, -10): "minecraft:dirt",
                (4, 100, -10): "minecraft:cobblestone",
                (4, 99, -10): "minecraft:cobblestone",
            }
            return {"id": blocks.get(position, "minecraft:air")}

    client = SimpleNamespace(transport=DescentTransport())
    moves = []
    monkeypatch.setattr(
        workstation_safety,
        "goto",
        lambda _client, x, y, z, **_kwargs: moves.append((x, y, z)) or True,
    )

    assert workstation_safety.move_to_supported_descent_column(
        client,
        5,
        101,
        -10,
    ) == (4, 101, -10)
    assert moves == [(4, 101, -10)]


def test_descent_can_step_down_to_a_shortened_adjacent_column(monkeypatch):
    class ShortColumnTransport:
        def dispatch(self, route, payload):
            if route != "get_block":
                return {}
            position = (payload["x"], payload["y"], payload["z"])
            blocks = {
                (5, 100, -10): "minecraft:dirt",
                (4, 99, -10): "minecraft:cobblestone",
                (4, 98, -10): "minecraft:cobblestone",
            }
            return {"id": blocks.get(position, "minecraft:air")}

    client = SimpleNamespace(transport=ShortColumnTransport())
    monkeypatch.setattr(workstation_safety, "goto", lambda *_args, **_kwargs: True)

    assert workstation_safety.move_to_supported_descent_column(
        client,
        5,
        101,
        -10,
    ) == (4, 100, -10)


def test_liquid_workstation_builds_supported_two_block_pad(monkeypatch):
    client = SimpleNamespace(transport=WaterTransport())
    placements = []
    monkeypatch.setattr(
        workstation_safety,
        "count_item",
        lambda _client, item_id: (
            13 if item_id == "minecraft:cobblestone" else 1
        ),
    )
    monkeypatch.setattr(
        workstation_safety.harness_ops,
        "place_block_exact",
        lambda _client, x, y, z, block, allow_break: placements.append(
            (x, y, z, block, allow_break)
        )
        or True,
    )
    monkeypatch.setattr(workstation_safety, "goto", lambda *_args, **_kwargs: True)

    assert workstation_safety.place_liquid_supported_workstation(client) == (
        10,
        63,
        -4,
    )
    assert placements == [
        (9, 62, -4, "minecraft:cobblestone", False),
        (10, 62, -4, "minecraft:cobblestone", False),
        (10, 63, -4, "minecraft:crafting_table", False),
    ]
