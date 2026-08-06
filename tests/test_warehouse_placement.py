"""Tests for exact, interruption-safe warehouse chest placement."""

from types import SimpleNamespace

from baritone_client.common import warehouse_placement as placement


def test_exact_pair_never_breaks_existing_blocks(monkeypatch):
    placed = []
    monkeypatch.setattr(placement.harness_ops, "available", lambda: True)
    monkeypatch.setattr(placement, "count_item", lambda *_args: 2)
    monkeypatch.setattr(
        placement.harness_ops,
        "_block_at",
        lambda _client, x, y, _z: (
            "minecraft:stone"
            if y == 63
            else "minecraft:air"
        ),
    )
    monkeypatch.setattr(placement.harness_ops, "move_near", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        placement.harness_ops,
        "place_block_exact",
        lambda _client, x, y, z, block, **kwargs: placed.append(
            ((x, y, z), block, kwargs)
        )
        or True,
    )

    result = placement.create_double_chest_at(
        SimpleNamespace(), (0, 64, 0), (1, 64, 0)
    )

    assert result == ((0, 64, 0), (1, 64, 0))
    assert placed == [
        ((0, 64, 0), "minecraft:chest", {"allow_break": False}),
        ((1, 64, 0), "minecraft:chest", {"allow_break": False}),
    ]


def test_exact_pair_refuses_to_adopt_an_existing_chest(monkeypatch):
    monkeypatch.setattr(placement.harness_ops, "available", lambda: True)
    monkeypatch.setattr(placement, "count_item", lambda *_args: 2)
    monkeypatch.setattr(
        placement.harness_ops,
        "_block_at",
        lambda _client, x, _y, _z: (
            "minecraft:chest" if x == 0 else "minecraft:air"
        ),
    )

    assert placement.create_double_chest_at(
        SimpleNamespace(), (0, 64, 0), (1, 64, 0)
    ) is None


def test_exact_pair_never_treats_a_trapped_chest_as_owned(monkeypatch):
    monkeypatch.setattr(placement.harness_ops, "available", lambda: True)
    monkeypatch.setattr(placement, "count_item", lambda *_args: 1)
    monkeypatch.setattr(
        placement.harness_ops,
        "_block_at",
        lambda _client, x, _y, _z: (
            "minecraft:trapped_chest" if x == 0 else "minecraft:air"
        ),
    )

    assert placement.create_double_chest_at(
        SimpleNamespace(),
        (0, 64, 0),
        (1, 64, 0),
        allowed_existing=((0, 64, 0),),
    ) is None


def test_exact_pair_resumes_only_a_catalog_confirmed_partial_pair(monkeypatch):
    placed = []
    confirmed = []
    monkeypatch.setattr(placement.harness_ops, "available", lambda: True)
    monkeypatch.setattr(placement, "count_item", lambda *_args: 1)
    monkeypatch.setattr(
        placement.harness_ops,
        "_block_at",
        lambda _client, x, y, _z: (
            "minecraft:stone"
            if y == 63
            else "minecraft:chest"
            if x == 0
            else "minecraft:air"
        ),
    )
    monkeypatch.setattr(placement.harness_ops, "move_near", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        placement.harness_ops,
        "place_block_exact",
        lambda _client, x, y, z, _block, **_kwargs: placed.append((x, y, z)) or True,
    )

    result = placement.create_double_chest_at(
        SimpleNamespace(),
        (0, 64, 0),
        (1, 64, 0),
        allowed_existing=((0, 64, 0),),
        on_placed=confirmed.append,
    )

    assert result == ((0, 64, 0), (1, 64, 0))
    assert placed == [(1, 64, 0)]
    assert confirmed == [(1, 64, 0)]


def test_exact_pair_refuses_an_obstructed_coordinate(monkeypatch):
    monkeypatch.setattr(placement.harness_ops, "available", lambda: True)
    monkeypatch.setattr(placement, "count_item", lambda *_args: 2)
    monkeypatch.setattr(
        placement.harness_ops,
        "_block_at",
        lambda *_args: "minecraft:crafting_table",
    )

    assert placement.create_double_chest_at(
        SimpleNamespace(), (0, 64, 0), (1, 64, 0)
    ) is None
