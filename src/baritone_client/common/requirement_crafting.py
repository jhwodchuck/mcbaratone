"""Adapters between supply shortfalls and absolute acquisition targets.

``ensure_supplies`` hands every strategy a *shortfall*: ``_missing_requirements``
reports ``required - current``. Most acquisition helpers take the opposite
thing -- an *absolute carried target* -- and early-return True once
``carried >= target``. Handing one an unconverted shortfall turns a
well-stocked item into a silent no-op that still reports success, so
``ensure_supplies`` re-checks, finds the same shortfall, and spins until it
times out.

Live on A1 2026-08-21: carrying 14 bread and needing 15, the shortfall of 1
satisfied ``14 >= 1`` instantly. FOOD_AND_IRON logged the same
"Ensuring supplies: {'minecraft:bread': 1}" line every ~5.5s for the full 600s
window, failed "Bake durable prepared food", retried the phase, and repeated on
an ~11.5 minute cycle for days -- blocking ``prepared_food_32``.

The gatherers' ``max(qty, N)`` floors do not protect against this. They set a
low bar a stocked inventory clears trivially: needing 30 cobblestone while
carrying 20 gives a shortfall of 10, and ``max(10, 16)`` is 16, which 20 already
satisfies. ``gather_gravel`` has no floor at all.

Callers outside ``DEFAULT_REQUIREMENT_STRATEGIES`` already pass real absolute
targets and must keep calling those helpers directly.
"""

from __future__ import annotations

from typing import Any, Callable


def absolute_requirement(
    client: Any,
    item_id: str,
    shortfall: int,
    *,
    count_item: Callable[[Any, str], int],
) -> int:
    """Recover ``ensure_supplies``' original absolute requirement.

    ``_missing_requirements`` reports ``required - current``, so adding the
    carried count back reconstructs ``required`` exactly.
    """
    return count_item(client, item_id) + max(1, int(shortfall))


def craft_shortfall_with_table(
    client: Any,
    item_id: str,
    shortfall: int,
    *,
    count_item: Callable[[Any, str], int],
    craft_with_table: Callable[[Any, str, int], bool],
) -> bool:
    """Add a requested shortfall to the carried count before crafting."""
    target = absolute_requirement(
        client, item_id, shortfall, count_item=count_item
    )
    return craft_with_table(client, item_id, target)
