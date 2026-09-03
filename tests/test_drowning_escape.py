"""A drowning bot must break the ceiling it is trapped under.

dragon-a 2026-09-02: 38 drownings and 53 supervisor restarts on a peaceful
world where only ONE death was a mob. The reflex fired correctly every time
and then failed the same way:

    SURVIVAL: head underwater for 3 supervision ticks; surfacing to avoid drowning
    SURVIVAL: starting upward Y-level ascent to y=62
    SURVIVAL: loaded-column ascent made no vertical progress; falling back to #surface
    SURVIVAL: surface route moved downward; aborting it
    [repeat until dead]

Two defects stacked. `reach_breathing_air` defaults require_stable_support to
False and the drowning path never overrides it, so the obstruction clearer was
skipped exactly when drowning -- the bot routed upward into a ceiling and waited
for air it could not reach. And the clearer hard-required a pickaxe, which a bot
that just drowned and respawned naked does not have, even though what sits above
a submerged bot near shore is usually dirt, sand or gravel.
"""

from __future__ import annotations

import inspect

from baritone_client.common import surface_recovery
from baritone_client.common.automation_utils import HAND_BREAKABLE_BLOCKS


def test_the_ceiling_is_cleared_even_when_footing_is_not_required():
    """Drowning is the case that skipped the clearing."""
    source = inspect.getsource(surface_recovery._start_loaded_column_ascent)

    assert "cleared = _clear_reachable_ascent_obstructions(" in source
    # The old form gated the clearing itself on require_stable_support.
    assert "if require_stable_support and not _clear_reachable" not in source
    # Aborting still only happens when stable footing was actually asked for.
    assert "if require_stable_support and not cleared:" in source


def test_a_naked_bot_can_still_dig_out_of_soft_ground():
    source = inspect.getsource(surface_recovery._clear_reachable_ascent_obstructions)

    assert "HAND_BREAKABLE_BLOCKS" in source
    # A missing pickaxe must no longer be an unconditional refusal.
    assert "for pickaxe in (" in source
    assert "and not all(name in HAND_BREAKABLE_BLOCKS" in source


def test_the_hand_breakable_set_covers_what_sits_over_drowned_bots():
    """Shoreline and riverbed cover, specifically."""
    for block in ("dirt", "sand", "gravel", "clay", "grass_block", "mud"):
        assert block in HAND_BREAKABLE_BLOCKS, block

    # Stone-family blocks genuinely need a pickaxe; claiming otherwise would
    # send a naked bot swinging at a block it cannot break while it suffocates.
    for block in ("stone", "deepslate", "obsidian", "andesite", "cobblestone"):
        assert block not in HAND_BREAKABLE_BLOCKS, block

    # Never claim the unbreakables.
    assert HAND_BREAKABLE_BLOCKS.isdisjoint(
        surface_recovery._UNBREAKABLE_EGRESS_BLOCKS
    )


def test_unbreakable_ceilings_still_refuse_rather_than_swing_forever():
    source = inspect.getsource(surface_recovery._clear_reachable_ascent_obstructions)
    assert "_UNBREAKABLE_EGRESS_BLOCKS" in source
    assert "return False" in source
