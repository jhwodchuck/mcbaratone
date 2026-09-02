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
    # The head material must be requested alongside the tool: the crafter does
    # not gather ore for a recipe, so asking for the pickaxe alone dead-ends on
    # "Missing ingredient 'minecraft:diamond'" without ever mining one.
    assert acquired == [{"minecraft:diamond_pickaxe": 1, "minecraft:diamond": 3}]


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
    # ...and through the completion-waiting miner, not fire-and-forget.
    assert "mine=_mine_until_satisfied" in entry, entry
    assert "_default_mine" not in entry, entry


def test_mining_waits_for_completion_instead_of_fire_and_forget(monkeypatch):
    """A1 spun on obsidian for over a day because nothing waited for a break.

    `_default_mine` dispatched Baritone's mine route and returned True for the
    dispatch alone, and `ensure_supplies` discards that answer anyway -- it
    only re-reads the inventory. So every ~6s poll re-entered Baritone's mine
    process, clearing its target bookkeeping, while obsidian needs ~9.4s of
    uninterrupted breaking at diamond tier. Live 2026-09-02: 0 carried after a
    full day, wandering 90 blocks out and back with a stale iron_sword in hand
    while obsidian sat within 64 blocks throughout.
    """
    from types import SimpleNamespace

    from baritone_client.common import resources as api
    from baritone_client.common.requirement_crafting import mine_until_satisfied

    counts = {"n": 0}
    starts = []
    equipped = []

    monkeypatch.setattr(api, "count_item", lambda _c, _i: counts["n"])
    monkeypatch.setattr(
        api, "equip_best_pickaxe", lambda _c, ids: equipped.append(ids) or True
    )
    monkeypatch.setattr(
        api, "_start_mine_process", lambda _c, blocks, qty: starts.append((blocks, qty))
    )
    monkeypatch.setattr(api, "_serialized_dispatch", lambda *_a, **_k: {})
    monkeypatch.setattr(api.time, "sleep", lambda _s: None)
    monkeypatch.setattr(
        "baritone_client.common.requirement_crafting.time.sleep", lambda _s: None
    )

    # The break lands on the third poll, exactly the case a 6s re-dispatch
    # could never reach.
    def state(*_a, **_k):
        counts["n"] += 1
        return {"is_pathing": True, "health": 20.0, "is_dead": False}

    monkeypatch.setattr(api, "_read_state_optional", state)

    assert mine_until_satisfied(SimpleNamespace(), "minecraft:obsidian", 2, timeout=30)
    assert len(starts) == 1, "must start the mine process exactly once, not per poll"
    assert starts[0][0] == ["minecraft:obsidian"]
    assert equipped == [["minecraft:diamond_pickaxe", "minecraft:netherite_pickaxe"]], (
        "must put a diamond-tier pickaxe in hand; A1 was holding an iron_sword"
    )


def test_mining_gives_up_on_a_bounded_idle_rather_than_spinning(monkeypatch):
    """Unreachable target must end the attempt, not hold the phase open."""
    from types import SimpleNamespace

    from baritone_client.common import resources as api
    from baritone_client.common.requirement_crafting import mine_until_satisfied

    monkeypatch.setattr(api, "count_item", lambda _c, _i: 0)
    monkeypatch.setattr(api, "equip_best_pickaxe", lambda *_a, **_k: True)
    monkeypatch.setattr(api, "_start_mine_process", lambda *_a, **_k: None)
    cancels = []
    monkeypatch.setattr(
        api, "_serialized_dispatch",
        lambda _c, route, _p, **_k: cancels.append(route) or {},
    )
    monkeypatch.setattr(
        "baritone_client.common.requirement_crafting.time.sleep", lambda _s: None
    )
    monkeypatch.setattr(
        api, "_read_state_optional",
        lambda *_a, **_k: {"is_pathing": False, "health": 20.0, "is_dead": False},
    )

    assert not mine_until_satisfied(
        SimpleNamespace(), "minecraft:obsidian", 14, timeout=600
    )
    assert "cancel" in cancels, "must cancel the mine process on every exit path"
