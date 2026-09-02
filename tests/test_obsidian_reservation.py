"""Two phases compete for one scarce obsidian stock; the portal wins.

Total demand is 14: ten for the Nether portal frame and four for an enchanting
table. Nothing reserved the portal's share -- `_craft_enchanting_table` only
asked `count_item(...) < 4`, which a bot holding exactly the portal's ten
passes trivially. It then spends four on the table, drops to six, fails
NETHER_AND_BLAZE's ten-gate, and goes back to mine obsidian that is not there.

Live on A1 2026-09-02: holding exactly 10 obsidian -- days of mining, and the
first time the portal gate had ever been satisfiable -- while sitting in
ENCHANTING_PIPELINE with no table crafted in three days.

Blaze rods are on the critical path to the End and the table is a gear upgrade,
so the portal outranks it. Once a portal exists the reserve is free.
"""

from __future__ import annotations

import inspect

from baritone_client.automator.phases import enchanting, nether_prep


def _source() -> str:
    return inspect.getsource(enchanting.EnchantingPipelineHandler._craft_enchanting_table)


def test_the_table_reserves_the_portal_frame_unless_a_portal_exists():
    source = _source()

    assert "_known_portal(state)" in source, "the reserve must lift once a portal exists"
    assert "4 + PORTAL_FRAME_OBSIDIAN" in source, "reserve the frame on top of the table"

    # The bare literal is what let a 10-obsidian bot through.
    assert 'count_item(client, "minecraft:obsidian") < 4:' not in source
    assert 'count_item(client, "minecraft:obsidian") < need:' in source
    # And the mining target must chase the reserved figure, not the table's 4.
    assert "self._mine_obsidian(client, target=need)" in source


def test_the_reserve_tracks_the_real_frame_cost():
    """A second hardcoded 10 would drift the moment the frame cost changes."""
    assert nether_prep.PORTAL_FRAME_OBSIDIAN == 10
    assert "PORTAL_FRAME_OBSIDIAN" in _source()
    assert "+ 10" not in _source(), "use the named constant, not a literal"


def test_the_reserved_total_is_what_a_bot_actually_needs():
    """Ten for the frame plus four for the table."""
    need_without_portal = 4 + nether_prep.PORTAL_FRAME_OBSIDIAN
    assert need_without_portal == 14

    # A bot holding exactly the portal's stock must NOT clear the table's bar.
    assert nether_prep.PORTAL_FRAME_OBSIDIAN < need_without_portal


def test_local_imports_keep_the_scheduler_cycle_broken():
    """adaptive_scheduler reaches phases through aid_response.

    A module-level `from ..adaptive_scheduler import _known_portal` here raises
    ImportError on a partially initialised module, taking the whole controller
    down at startup rather than failing a single phase.
    """
    module = inspect.getsource(enchanting)
    header = module[: module.index("class ")]
    assert "from ..adaptive_scheduler import" not in header
    assert "from .nether_prep import" not in header
    # ...but they are imported where they are used.
    assert "from ..adaptive_scheduler import _known_portal" in _source()
