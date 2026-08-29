"""A mine the carried tools cannot break must not report success.

Baritone's ``mine`` route silently does nothing when no carried tool can
break the target, and ``_default_mine`` returns True merely for dispatching
it. ``ensure_supplies`` then re-checks an unchanged inventory and respins for
its whole timeout.

Live on A1 2026-08-29, carrying only a stone pickaxe: NETHER_AND_BLAZE logged
"Gathering minecraft:obsidian x14" every ~6s across three full 180s windows
per attempt before giving up with "Could not gather portal materials".
Obsidian is diamond-tier, so not one of those swings could ever have landed.
"""

from __future__ import annotations

from types import SimpleNamespace

from baritone_client.common import resources
from baritone_client.common.requirement_crafting import mine_requiring_pickaxe


def _result(success: bool):
    return SimpleNamespace(success=success)


def test_a_diamond_tier_block_is_not_mined_with_a_lesser_pickaxe():
    """The whole point: refuse, do not dispatch a swing that cannot land."""
    mined = []
    acquired = []

    assert not mine_requiring_pickaxe(
        SimpleNamespace(),
        "minecraft:obsidian",
        14,
        durability=lambda _c, _p: 0,
        ensure=lambda _c, kit, **_k: acquired.append(kit) or _result(False),
        mine=lambda *_a: mined.append(True) or True,
    )
    assert mined == [], "must not dispatch a mine the tool cannot complete"
    assert acquired == [{"minecraft:diamond_pickaxe": 1}]


def test_an_adequate_pickaxe_mines_immediately_without_reacquiring():
    """Do not pay for an acquisition the bot does not need."""
    mined = []

    assert mine_requiring_pickaxe(
        SimpleNamespace(),
        "minecraft:obsidian",
        14,
        durability=lambda _c, _p: 400,
        ensure=lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("must not acquire when a usable pickaxe is carried")
        ),
        mine=lambda _c, block, qty: mined.append((block, qty)) or True,
    )
    assert mined == [("minecraft:obsidian", 14)]


def test_a_successful_acquisition_then_proceeds_to_mine():
    """Acquiring the tier is only useful if the mine actually follows."""
    mined = []

    assert mine_requiring_pickaxe(
        SimpleNamespace(),
        "minecraft:obsidian",
        14,
        durability=lambda _c, _p: 0,
        ensure=lambda *_a, **_k: _result(True),
        mine=lambda _c, block, qty: mined.append((block, qty)) or True,
    )
    assert mined == [("minecraft:obsidian", 14)]


def test_the_obsidian_strategy_is_wired_through_the_tier_gate():
    """The live blocker was the strategy table entry, not the helper."""
    import inspect

    source = inspect.getsource(resources)
    entry = source[source.index('"minecraft:obsidian": lambda') :].split("\n", 1)[0]
    assert "_mine_requiring_pickaxe" in entry, entry
    assert "remaining_pickaxe_durability" in entry, entry
