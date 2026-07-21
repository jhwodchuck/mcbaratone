"""
Adapter exposing the battle-tested functional-harness primitives
(tests/functional/shared + tests/utils/mc_harness) to the automator.

The harness library was hardened over hundreds of consecutive live runs and
solves problems the automator's native actions handle poorly: finding real
placement spots, opening containers with GUI verification, and crafting
without recipe listing (unavailable on 1.21.4 clients) via manual grid
clicks.

Imports are lazy: the harness modules import `from baritone_client import
Client`, so pulling them in at module load time creates a circular import
when this module is itself loaded during baritone_client initialization.
Everything degrades gracefully: if the tests package is not importable
(e.g. an installed copy of baritone_client without the repo checkout),
available() is False and callers fall back to their native logic.

Only survival-safe helpers are exposed. The harness also contains
creative/cheat utilities (give_item, set_block, teleport) which must never
be used by the autonomous survival controller - do not add them here.
"""

import os
import sys

_harness = None          # dict of resolved callables once loaded
_import_error = None

# The harness modules import as `tests.functional...`, which requires the
# repo root on sys.path. When the controller runs from the repo root this is
# already true; add it defensively otherwise.
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if os.path.isdir(os.path.join(_REPO_ROOT, "tests")) and _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


def _load():
    """Resolve harness imports on first use; returns dict or None."""
    global _harness, _import_error
    if _harness is not None:
        return _harness
    if _import_error is not None:
        return None
    try:
        from tests.utils.mc_harness.context import TestContext
        from tests.functional.shared import inventory_ops as inv_ops
        from tests.functional.shared import block_ops

        _harness = {
            "TestContext": TestContext,
            "ensure_crafting_table_open": inv_ops.ensure_crafting_table_open,
            "craft_tool_manual_generic": inv_ops._craft_tool_manual_generic,
            "craft_armor_manual_generic": inv_ops._craft_armor_manual_generic,
            "craft_bed_manual": inv_ops.craft_bed_manual,
            "craft_door_manual": inv_ops.craft_door_manual,
            "craft_chest_manual": inv_ops.craft_chest_manual,
            "craft_furnace_manual": inv_ops.craft_furnace_manual,
            "craft_crafting_table_manual": inv_ops.craft_crafting_table_manual,
            "craft_planks_manual": inv_ops.craft_planks_manual,
            "craft_recipe_manual": inv_ops.craft_recipe_manual,
            "ensure_crafting_output_space": inv_ops.ensure_crafting_output_space,
            "smelt_in_furnace": inv_ops.smelt_in_furnace,
            "open_container": inv_ops.do_open_container,
            "count_any_planks": inv_ops.count_any_planks,
            "count_all_logs": inv_ops.count_all_logs,
            "bot_place_block": block_ops.bot_place_block,
            "place_block_at": block_ops.place_block_at,
            "find_place_pos_near": block_ops.find_place_pos_near,
            "move_near": block_ops.move_near,
        }
        return _harness
    except Exception as exc:
        _import_error = exc
        print(f"  harness_ops: harness library unavailable ({exc}); using native fallbacks")
        return None


def available() -> bool:
    return _load() is not None


def make_ctx(client):
    """Wrap an automator Client in the harness TestContext."""
    h = _load()
    if h is None:
        raise RuntimeError(f"harness library unavailable: {_import_error}")
    ctx = h["TestContext"](client=client)

    # TestContext.log_event only records to ctx.events; in the automator we
    # want those diagnostics in the controller log.
    original_log = ctx.log_event

    def _print_log(event):
        print(f"  [harness] {event}")
        original_log(event)

    ctx.log_event = _print_log
    return ctx


def ensure_crafting_table_open(client, table_pos=None) -> bool:
    """Find/place a crafting table nearby and open its GUI (verified)."""
    h = _load()
    return bool(h["ensure_crafting_table_open"](make_ctx(client), table_pos=table_pos))


def place_block(client, x, y, z, block_type) -> bool:
    """Survival-safe placement NEAR (x,y,z) - picks a suitable nearby spot.
    Use place_block_exact when the exact coordinate matters (structures)."""
    h = _load()
    return bool(h["bot_place_block"](make_ctx(client), x, y, z, block_type, allow_move=True))


def place_block_exact(client, x, y, z, block_type, allow_break=True) -> bool:
    """Survival-safe placement AT exactly (x,y,z): moves in range, clears
    obstructions, retries, and verifies the block appeared."""
    h = _load()
    return bool(h["place_block_at"](make_ctx(client), x, y, z, block_type,
                                    allow_break=allow_break, allow_move=True))


def find_place_pos_near(client, x, y, z, radius=4):
    h = _load()
    return h["find_place_pos_near"](make_ctx(client), x, y, z, radius=radius)


def move_near(client, x, y, z, timeout=20.0) -> bool:
    h = _load()
    return bool(h["move_near"](make_ctx(client), x, y, z, timeout=timeout))


# Tool id -> (material substring for the open-screen ingredient scan, tool type)
_TOOL_MATERIALS = {
    "wooden": "_planks",
    "stone": "minecraft:cobblestone",
    "iron": "minecraft:iron_ingot",
    "golden": "minecraft:gold_ingot",
    "diamond": "minecraft:diamond",
}
_TOOL_TYPES = ("pickaxe", "axe", "shovel", "sword", "hoe")

_ARMOR_MATERIALS = {
    "leather": "minecraft:leather",
    "iron": "minecraft:iron_ingot",
    "golden": "minecraft:gold_ingot",
    "diamond": "minecraft:diamond",
}
_ARMOR_TYPES = ("helmet", "chestplate", "leggings", "boots")


def parse_tool_id(item_id: str):
    """'minecraft:stone_pickaxe' -> ('minecraft:cobblestone', 'pickaxe') or None."""
    name = item_id.split(":", 1)[-1]
    for tier, material in _TOOL_MATERIALS.items():
        for tool in _TOOL_TYPES:
            if name == f"{tier}_{tool}":
                return material, tool
    return None


def parse_armor_id(item_id: str):
    """'minecraft:iron_helmet' -> ('minecraft:iron_ingot', 'helmet')."""
    name = item_id.split(":", 1)[-1]
    for tier, material in _ARMOR_MATERIALS.items():
        for armor_type in _ARMOR_TYPES:
            if name == f"{tier}_{armor_type}":
                return material, armor_type
    return None


def craft_tool_manual(client, item_id: str) -> bool:
    """
    Craft a tool by clicking ingredients into the open crafting-table grid.
    Requires the table GUI to already be open (see ensure_crafting_table_open).
    Immune to the missing recipe-listing on 1.21.4.
    """
    parsed = parse_tool_id(item_id)
    if not parsed:
        return False
    material, tool = parsed
    h = _load()
    return bool(h["craft_tool_manual_generic"](make_ctx(client), material, tool, item_id))


def craft_armor_manual(client, item_id: str) -> bool:
    """Craft one armor piece in a verified, already-open 3x3 table."""
    parsed = parse_armor_id(item_id)
    if not parsed:
        return False
    material, armor_type = parsed
    h = _load()
    return bool(
        h["craft_armor_manual_generic"](
            make_ctx(client), material, armor_type, item_id
        )
    )


def craft_bed_manual(client, bed_id="minecraft:white_bed") -> bool:
    return bool(_load()["craft_bed_manual"](make_ctx(client), bed_id))


def craft_door_manual(client, door_id="minecraft:oak_door") -> bool:
    return bool(_load()["craft_door_manual"](make_ctx(client), door_id))


def craft_chest_manual(client) -> bool:
    return bool(_load()["craft_chest_manual"](make_ctx(client)))


def craft_furnace_manual(client) -> bool:
    return bool(_load()["craft_furnace_manual"](make_ctx(client)))


def craft_crafting_table_manual(client) -> bool:
    """Craft a table in the player's 2x2 grid without recipe lookup."""
    return bool(_load()["craft_crafting_table_manual"](make_ctx(client)))


def craft_planks_manual(client, plank_id: str, output_count: int = 4) -> bool:
    """Craft one wood family's planks in the verified player 2x2 grid."""
    return bool(
        _load()["craft_planks_manual"](
            make_ctx(client), plank_id, output_count=output_count
        )
    )


def craft_recipe_manual(
    client,
    result_id: str,
    placements,
    crafts: int = 1,
    output_per_recipe: int = 1,
) -> bool:
    """Drive an explicit progression recipe in an open crafting table.

    Tries the bridge-native ``place_recipe`` command first (single TCP
    round-trip for the entire click choreography).  Falls back to the
    Python per-click path if the bridge doesn't support the command or
    returns an error.
    """
    placements_list = list(placements)

    if not _load()["ensure_crafting_output_space"](make_ctx(client)):
        return False

    # --- Bridge-native fast path ---
    if _try_bridge_place_recipe(client, result_id, placements_list,
                                crafts, output_per_recipe):
        return True

    # --- Python per-click fallback ---
    return bool(
        _load()["craft_recipe_manual"](
            make_ctx(client),
            result_id,
            placements_list,
            crafts=crafts,
            output_per_recipe=output_per_recipe,
            try_bridge=False,
        )
    )


def _try_bridge_place_recipe(
    client,
    result_id: str,
    placements,
    crafts: int = 1,
    output_per_recipe: int = 1,
) -> bool:
    """Try the bridge-native place_recipe command.  Returns True on verified
    success, False if the bridge doesn't support it or the craft failed
    (caller should fall back to the Python path)."""
    try:
        payload = {
            "placements": [
                {"selector": sel, "grid_slot": slot}
                for sel, slot in placements
            ],
            "expected_output": result_id,
            "expected_count": output_per_recipe,
            "crafts": crafts,
        }
        resp = client.transport.dispatch("place_recipe", payload)
        data = resp.get("data", resp) if isinstance(resp, dict) else {}
        if data.get("crafted"):
            return True
        # Bridge returned a structured error (e.g. missing_ingredient,
        # no_output) — fall through to Python path.
        detail = data.get("error") or (
            resp.get("error") if isinstance(resp, dict) else None
        )
        print(
            f"  [Craft Debug] place_recipe did not craft {result_id}: "
            f"{detail or data or resp}"
        )
        return False
    except Exception as exc:
        # Command not recognised by older bridge, transport error, etc.
        print(f"  [Craft Debug] place_recipe failed for {result_id}: {exc}")
        return False


def place_recipe(
    client,
    result_id: str,
    placements,
    crafts: int = 1,
    output_per_recipe: int = 1,
) -> bool:
    """Dispatch the bridge-native place_recipe command directly.

    Unlike ``craft_recipe_manual``, this does *not* fall back to the
    Python per-click path — use ``craft_recipe_manual`` when you want
    transparent degradation.
    """
    return _try_bridge_place_recipe(
        client, result_id, list(placements), crafts, output_per_recipe
    )


def smelt_in_furnace(client, furnace_pos, input_id, fuel_id, output_id, output_count=1, wait_per_item=10.5) -> bool:
    return bool(_load()["smelt_in_furnace"](make_ctx(client), furnace_pos, input_id, fuel_id, output_id, output_count, wait_per_item=wait_per_item))


def open_container(client, block_pos, timeout=4.0) -> bool:
    """Open a known container and verify its expected screen layout."""
    return bool(
        _load()["open_container"](
            make_ctx(client), tuple(block_pos), timeout=timeout
        )
    )


def count_any_planks(client) -> int:
    return _load()["count_any_planks"](make_ctx(client))


def count_all_logs(client) -> int:
    return _load()["count_all_logs"](make_ctx(client))
