"""An unloaded chunk must never retire a catalogued container.

The bridge answers `get_block` for any coordinate outside the client's loaded
chunks with "minecraft:void_air". That is an *unknown* read. Treating it as
"the chest is gone" calls `StorageCatalog.mark_missing`, whose own contract is
"a loaded, readable coordinate" -- and because `list_containers` and
`find_item` both filter `status!='missing'`, the entry becomes invisible to
every later lookup. Nothing re-probes an entry it can no longer see, so the
deletion is permanent.

Measured on A1Bot 2026-08-16: 29 of 49 catalogued containers had been retired,
13 of them through this path. One of the retired coordinates, (-351, 79, 95),
had been opened successfully by the same bot earlier the same afternoon --
proof the chest was real. With every nearby entry erased, the bot set out on a
~400 block walk to a spawn-era chest at (-8, 152, 39) to unload a full
inventory, and `FOOD_AND_IRON` made no progress for hours.
"""

from types import SimpleNamespace

import pytest

from baritone_client.common import inventory


# The coordinate A1Bot opened at 16:10 and had retired by 17:01.
REAL_CHEST = (-351, 79, 95)


class _Catalog:
    """Records mark_missing calls instead of touching a real sqlite catalog."""

    def __init__(self):
        self.retired = []

    def mark_missing(self, position, *, dimension, observed_block_id=None):
        self.retired.append(tuple(position))


@pytest.fixture
def catalog(monkeypatch):
    entry = _Catalog()
    monkeypatch.setattr(
        "baritone_client.common.storage_catalog.catalog_for",
        lambda *_a, **_k: entry,
    )
    return entry


def _client(block_id):
    """A client whose every get_block answers with `block_id`."""

    def dispatch(route, payload=None):
        if route == "get_block":
            return {"id": block_id}
        if route == "get_state":
            return {
                "dimension": "minecraft:overworld",
                "block_position": {"x": 0, "y": 64, "z": 0},
            }
        return {}

    return SimpleNamespace(transport=SimpleNamespace(dispatch=dispatch))


@pytest.mark.parametrize(
    "block_id, expected",
    [
        ("minecraft:void_air", "unknown"),
        ("", "unknown"),
        (None, "unknown"),
        ("minecraft:chest", "container"),
        ("minecraft:barrel", "container"),
        ("minecraft:shulker_box", "container"),
        ("minecraft:stone", "absent"),
        ("minecraft:air", "absent"),
    ],
)
def test_reads_are_classified_by_what_they_actually_prove(block_id, expected):
    """`air` proves absence; `void_air` proves only that nothing was loaded."""
    assert inventory._classify_container_read(block_id) == expected


def test_an_unloaded_read_does_not_retire_the_container(catalog):
    """THE A1Bot bug: void_air erased a chest the bot had just used."""
    inventory._forget_missing_container(
        _client("minecraft:void_air"),
        REAL_CHEST,
        block_id="minecraft:void_air",
    )

    assert catalog.retired == [], (
        f"retired {catalog.retired} on an unloaded-chunk read; the chest at "
        f"{REAL_CHEST} was real and had been opened minutes earlier"
    )


def test_an_empty_read_does_not_retire_the_container(catalog):
    """An absent id is as unproven as void_air, and reached the same branch."""
    inventory._forget_missing_container(
        _client(""), REAL_CHEST, block_id=""
    )

    assert catalog.retired == []


def test_a_confirmed_absence_still_retires_the_container(catalog):
    """The fix must not disarm the original purpose: real phantoms must go.

    Without this the bot re-walks to genuinely destroyed chests forever, which
    is the stall this helper was written to end.
    """
    inventory._forget_missing_container(
        _client("minecraft:stone"),
        REAL_CHEST,
        block_id="minecraft:stone",
    )

    assert catalog.retired == [REAL_CHEST]


def test_withdraw_leaves_the_entry_alone_when_the_chunk_never_loads(
    catalog, monkeypatch
):
    """The live path: unreachable chunk aborts the attempt, not the catalog.

    `withdraw_required_from_chest` previously read void_air, concluded the
    container was missing, and retired it before ever trying to load the chunk.
    """
    monkeypatch.setattr(inventory, "count_item", lambda _c, _i: 0)
    monkeypatch.setattr(
        "baritone_client.common.storage_safety.load_storage_chunk",
        lambda *_a, **_k: False,
    )
    monkeypatch.setattr(
        "baritone_client.common.navigation.goto", lambda *_a, **_k: False
    )

    moved = inventory.withdraw_required_from_chest(
        _client("minecraft:void_air"),
        REAL_CHEST,
        {"minecraft:iron_ingot": 8},
    )

    assert moved == -1, "an unreachable chunk is a failed attempt"
    assert catalog.retired == [], (
        f"retired {catalog.retired} without ever loading the chunk"
    )
