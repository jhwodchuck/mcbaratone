"""Catalogued containers must be tried nearest-first.

`withdraw_required_from_catalog` collects candidates in catalog order, then
truncates to `max_containers` (8) *before* applying the distance filter. With
325 containers catalogued by the fleet, the eight it looked at were effectively
arbitrary.

Measured on 2026-08-06: Bot15 (iron_supply) was mining at (31, 32, 6) with a
catalogued chest 19.5m away at (20, 48, 4). Its log repeated the same distant
containers hundreds of times -- "305 x 217.1m exceeds the 96.0m recovery
radius" -- and never once considered the near one. It banked 25 loads all day
while Bot07 banked 4,249.
"""

from types import SimpleNamespace

import pytest

from baritone_client.common import inventory


BOT15_MINE = {"x": 31, "y": 32, "z": 6}
NEAR_CHEST = (20, 48, 4)        # 19.5m away -- the one it should have used
FAR_CHESTS = [                  # the ones its log actually cycled through
    (-161, 62, -318), (-180, 94, -368), (-180, 94, -370),
    (-99, -27, -329), (-125, 10, -278), (29, -34, 46),
    (-69, 66, -178), (-173, -55, -542), (-588, 21, 288),
]


class _Catalog:
    """Returns far containers first, exactly as catalog order did live."""

    def __init__(self, positions):
        self.positions = positions

    def _entry(self, position):
        x, y, z = position
        return {"x": x, "y": y, "z": z, "dimension": "minecraft:overworld"}

    def find_item(self, _item_id):
        return []

    def list_containers(self):
        return [self._entry(p) for p in self.positions]


@pytest.fixture
def opened(monkeypatch):
    """Record which containers the routine tries to open.

    Navigation is neutralised deliberately: this loop calls the real goto()
    (60s) and harness_ops.move_near() (90s) for any container past 12m, which
    with a stub client never arrive and hang the suite.
    """
    seen = []

    monkeypatch.setattr(inventory, "count_item", lambda _c, _i: 0)
    monkeypatch.setattr(inventory, "storage_retry_ready", lambda _c, _p: True)
    monkeypatch.setattr(
        "baritone_client.common.navigation.goto", lambda *_a, **_k: True
    )
    monkeypatch.setattr(
        "baritone_client.common.harness_ops.move_near", lambda *_a, **_k: True
    )

    def fake_withdraw(_client, chest_pos, _requirements, **_kwargs):
        seen.append(tuple(chest_pos))
        return 0

    monkeypatch.setattr(inventory, "withdraw_required_from_chest", fake_withdraw)
    return seen


def _client():
    state = {"dimension": "minecraft:overworld", "block_position": BOT15_MINE}
    return SimpleNamespace(
        transport=SimpleNamespace(dispatch=lambda _route, _payload=None: state)
    )


def _run(monkeypatch, positions, opened):
    monkeypatch.setattr(
        "baritone_client.common.storage_catalog.catalog_for",
        lambda *_a, **_k: _Catalog(positions),
    )
    inventory.withdraw_required_from_catalog(
        _client(),
        {"minecraft:iron_ingot": 8},
        max_travel_distance=96.0,
    )


def test_the_near_chest_is_tried_before_distant_ones(monkeypatch, opened):
    """THE Bot15 bug: the useful chest was listed last and never reached."""
    _run(monkeypatch, FAR_CHESTS + [NEAR_CHEST], opened)

    assert opened, "no container was tried at all"
    assert opened[0] == NEAR_CHEST, (
        f"tried {opened[0]} first; the 19.5m chest {NEAR_CHEST} should win"
    )


def test_order_does_not_depend_on_catalog_order(monkeypatch, opened):
    """Same set, near chest listed first -- must still be chosen first."""
    _run(monkeypatch, [NEAR_CHEST] + FAR_CHESTS, opened)

    assert opened[0] == NEAR_CHEST


def test_every_tried_container_is_within_the_travel_limit(monkeypatch, opened):
    """Sorting must not smuggle far containers past the distance filter."""
    _run(monkeypatch, FAR_CHESTS + [NEAR_CHEST], opened)

    for position in opened:
        distance = sum(
            (BOT15_MINE[axis] - position[i]) ** 2
            for i, axis in enumerate(("x", "y", "z"))
        ) ** 0.5
        assert distance <= 96.0, f"{position} is {distance:.1f}m away"


def test_known_item_hits_still_outrank_nearer_unscanned_chests(monkeypatch, opened):
    """Distance orders within a tier; it must not demote a confirmed hit.

    A chest known to hold the item is worth a slightly longer walk than a
    speculative one, or the bot burns its container budget on empty boxes.
    """
    hit = (60, 40, 30)   # confirmed to hold iron, but further than NEAR_CHEST

    class _WithHit(_Catalog):
        def find_item(self, _item_id):
            return [self._entry(hit)]

    monkeypatch.setattr(
        "baritone_client.common.storage_catalog.catalog_for",
        lambda *_a, **_k: _WithHit([NEAR_CHEST] + FAR_CHESTS),
    )
    inventory.withdraw_required_from_catalog(
        _client(), {"minecraft:iron_ingot": 8}, max_travel_distance=96.0
    )

    assert opened[0] == hit, f"confirmed hit {hit} should be tried first"
    assert NEAR_CHEST in opened, "the nearer unscanned chest should still follow"
