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


def _normalize_player_inventory_screen(response):
    """Map current Minecraft's player-menu name to the harness ABI."""
    if not isinstance(response, dict):
        return response
    screen = response.get("data", response)
    if not isinstance(screen, dict):
        return response
    slots = screen.get("slots")
    if screen.get("type") != "InventoryMenu" or not isinstance(slots, list):
        return response
    if len(slots) < 46:
        return response

    normalized_screen = dict(screen)
    normalized_screen["type"] = "PlayerScreenHandler"
    if screen is response:
        return normalized_screen
    normalized_response = dict(response)
    normalized_response["data"] = normalized_screen
    return normalized_response


class _HarnessTransportAdapter:
    """Normalize version-specific bridge responses used by harness helpers."""

    def __init__(self, transport):
        self._transport = transport

    def dispatch(self, route, payload, **kwargs):
        response = self._transport.dispatch(route, payload, **kwargs)
        if route == "get_screen":
            return _normalize_player_inventory_screen(response)
        return response

    def __getattr__(self, name):
        return getattr(self._transport, name)


class _HarnessClientAdapter:
    """Preserve the Client API while adapting its transport for the harness."""

    def __init__(self, client):
        self._client = client
        self.transport = _HarnessTransportAdapter(client.transport)

    def __getattr__(self, name):
        return getattr(self._client, name)


def _load():
    """Resolve harness imports on first use; returns dict or None."""
    global _harness, _import_error
    if _harness is not None:
        return _harness
    if _import_error is not None:
        return None
    try:
        from tests.utils.mc_harness.context import TestContext
        from tests.utils.mc_harness import inventory as mc_inventory
        from tests.functional.shared import inventory_ops as inv_ops
        from tests.functional.shared import block_ops

        _harness = {
            "TestContext": TestContext,
            "ensure_item_in_hotbar": mc_inventory.ensure_item_in_hotbar,
            "select_hotbar_item": mc_inventory.select_hotbar_item,
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
            "bot_build_hollow_box": block_ops.bot_build_hollow_box,
            "deposit_inventory_to_supply_chest": inv_ops.deposit_inventory_to_supply_chest,
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
    ctx = h["TestContext"](client=_HarnessClientAdapter(client))

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
    """Move near a coordinate, surfacing a death to the controller.

    The harness itself only returns False when the player is dead, which is
    right for the read-only functional suites but useless to the automator:
    the caller just treats it as "move failed" and retries. Live blocker --
    Bot07/Bot08 died mid-move and their controllers kept retrying movement on
    a corpse for minutes instead of running death recovery. Translating it to
    PlayerDeathDetected here (the adapter boundary) puts the controller into
    its normal death path immediately, without making the harness raise on
    the test suites.
    """
    h = _load()
    moved = bool(h["move_near"](make_ctx(client), x, y, z, timeout=timeout))
    if not moved:
        from .tasks import PlayerDeathDetected

        try:
            state = client.transport.dispatch("get_state", {})
        except Exception:
            return moved
        if state.get("is_dead", False) or float(state.get("health", 20) or 0) <= 0:
            raise PlayerDeathDetected("player died during harness movement")
    return moved


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


_SELECTOR_MEMBERS = {
    "#coals": ("minecraft:coal", "minecraft:charcoal"),
    "any_coal": ("minecraft:coal", "minecraft:charcoal"),
}


def _resolve_placement_selectors(client, placements):
    """Replace tag selectors with a concrete item the player is carrying.

    The bridge's native ``place_recipe`` only understands literal item ids --
    it rejected a "#coals" selector outright with "Missing ingredient
    '#coals' for grid slot 1", forcing every such craft down the slow Python
    per-click path (or failing entirely). Resolving here lets both paths work
    and keeps the recipe tables tag-based. Selectors with no carried member,
    and ones the harness resolves itself such as "#planks", pass through
    untouched.
    """
    from .inventory import count_item

    resolved = []
    for selector, slot in placements:
        members = _SELECTOR_MEMBERS.get(selector)
        if members:
            carried = next(
                (item for item in members if count_item(client, item) > 0),
                None,
            )
            if carried is not None:
                resolved.append((carried, slot))
                continue
        resolved.append((selector, slot))
    return resolved


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
    placements_list = _resolve_placement_selectors(client, placements)

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


# ---------------------------------------------------------------------------
# Storage capacity and double-chest overflow.
#
# Ported from tests/functional/extended_suite_1100.py, which has run this
# logic over 100+ consecutive live sessions. Production had reimplemented
# storage without any of it: no notion of a chest being full, no double
# chests, and no way to grow capacity -- so when carried slots ran out the
# only recourse was dropping items on the ground. Live 2026-08-02 that had
# all four bots aborting the deep-mining descent roughly hourly to shed ore
# they had nowhere to put.
# ---------------------------------------------------------------------------

# A container screen carries the 36 player slots after the container's own.
# 27 chest slots -> 63 total (single), 54 -> 90 (double).
_PLAYER_SCREEN_SLOTS = 36

_NON_SUPPORT_TOKENS = (
    "grass", "flower", "fern", "sapling", "dead_bush", "torch",
    "fire", "leaf_litter", "snow", "leaves",
)


def _is_solid_support_block(block_id) -> bool:
    """Whether a block can carry a chest placed on top of it."""
    value = str(block_id or "")
    if not value:
        return False
    if "air" in value or "water" in value or "lava" in value:
        return False
    # grass_block is real ground; short_grass and friends are not.
    if any(token in value for token in _NON_SUPPORT_TOKENS) and "grass_block" not in value:
        return False
    return True


def _is_placeable_target(block_id) -> bool:
    """Whether a chest may be placed into this coordinate."""
    value = str(block_id or "")
    if not value:
        return False
    if "chest" in value:
        return False
    return "air" in value or "water" in value or "lava" in value


def _block_at(client, x, y, z) -> str:
    try:
        return str(
            client.transport.dispatch("get_block", {"x": int(x), "y": int(y), "z": int(z)}).get("id", "")
        )
    except Exception:
        return ""


def open_container_slots(client):
    """Return ``(slots, chest_slots)`` for the currently open container.

    Returns None when the open screen is not a container -- the player's own
    inventory screen also reports slots, which is exactly how a blocked chest
    was mistaken for an open one.
    """
    try:
        screen = client.transport.dispatch("get_screen", {})
    except Exception:
        return None
    data = screen.get("data", screen)
    slots = data.get("slots", []) or []
    total = int(data.get("total_slots") or 0) or len(slots)
    if total <= _PLAYER_SCREEN_SLOTS:
        return None
    return slots, total - _PLAYER_SCREEN_SLOTS


def chest_is_full(client, chest_pos, timeout=4.0):
    """Return True/False, or None when the chest could not be inspected."""
    h = _load()
    if h is None:
        return None
    if not open_container(client, tuple(chest_pos), timeout=timeout):
        return None
    info = open_container_slots(client)
    if not info:
        close_container(client)
        return None
    slots, chest_slots = info
    occupied = sum(
        1
        for item in slots
        if 0 <= int(item.get("slot", -1)) < chest_slots
        and item.get("id") not in (None, "", "minecraft:air")
    )
    close_container(client)
    return occupied >= chest_slots


def close_container(client) -> None:
    try:
        client.transport.dispatch("close_screen", {})
    except Exception:
        pass


def find_double_chest_spot(client, radius: int = 8):
    """Find an adjacent pair of coordinates that can hold a double chest.

    Searched from the player's current position rather than a stored home:
    a persisted anchor can sit in an unloaded or floating region, which is
    how bots ended up trying to build storage in mid-air.
    """
    try:
        state = client.transport.dispatch("get_state", {})
        pos = state.get("block_position") or state.get("position") or {}
        px, py, pz = (int(pos.get(a, 0)) for a in ("x", "y", "z"))
    except Exception:
        return None

    for dx in range(-radius, radius + 1):
        for dz in range(-radius, radius + 1):
            first = (px + dx, py, pz + dz)
            if not _is_placeable_target(_block_at(client, *first)):
                continue
            if not _is_solid_support_block(_block_at(client, first[0], first[1] - 1, first[2])):
                continue
            for ox, oz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                second = (first[0] + ox, first[1], first[2] + oz)
                if not _is_placeable_target(_block_at(client, *second)):
                    continue
                if not _is_solid_support_block(_block_at(client, second[0], second[1] - 1, second[2])):
                    continue
                return first, second
    return None


def find_single_chest_spot(client, radius: int = 8):
    """Find the nearest supported tile for emergency single-chest overflow."""
    try:
        state = client.transport.dispatch("get_state", {})
        pos = state.get("block_position") or state.get("position") or {}
        px, py, pz = (int(pos.get(axis, 0)) for axis in ("x", "y", "z"))
    except Exception:
        return None

    offsets = sorted(
        (
            (dx, dz)
            for dx in range(-radius, radius + 1)
            for dz in range(-radius, radius + 1)
            if dx or dz
        ),
        key=lambda offset: offset[0] ** 2 + offset[1] ** 2,
    )
    # Some bridge states report the top solid block as the player's integer
    # Y (live Bot16 stood at Y=64 while get_block Y=64 was mud). Try the
    # nominal feet layer first, then the air layer directly above that surface.
    for dx, dz in offsets:
        for target_y in (py, py + 1):
            target = (px + dx, target_y, pz + dz)
            if not _is_placeable_target(_block_at(client, *target)):
                continue
            support = _block_at(client, target[0], target[1] - 1, target[2])
            if _is_solid_support_block(support):
                return target
    return None


def create_double_chest(client):
    """Place a merged double chest and return ``(first, second)``.

    The second chest is placed from a position perpendicular to the pair.
    Standing in line with them makes Minecraft resolve the placement against
    the first chest's face, which yields two separate single chests instead
    of one 54-slot double.
    """
    from .inventory import count_item

    h = _load()
    if h is None:
        return None
    if count_item(client, "minecraft:chest") < 2:
        return None
    spot = find_double_chest_spot(client)
    if spot is None:
        print("  STORAGE: no room for a double chest nearby")
        return None
    first, second = spot

    if not move_near(client, *first, timeout=20.0):
        return None
    if not place_block_exact(client, first[0], first[1], first[2], "minecraft:chest"):
        print(f"  STORAGE: failed to place first chest at {first}")
        return None

    dx = second[0] - first[0]
    dz = second[2] - first[2]
    perp = (first[0], first[1], first[2] + 1) if dx else (first[0] + 1, first[1], first[2])
    move_near(client, *perp, timeout=10.0)

    if not place_block_exact(client, second[0], second[1], second[2], "minecraft:chest"):
        print(f"  STORAGE: failed to place second chest at {second}")
        return None
    print(f"  STORAGE: built double chest at {first}/{second}")
    return first, second


def ensure_item_in_hotbar(client, item_id: str):
    """Move ``item_id`` into the hotbar and return the slot it now occupies.

    Returns None when the item is not carried at all.

    Production's own swap hardcoded hotbar slot 0 and then issued
    ``select_slot 0`` regardless of where the item actually landed. This
    picks an *empty* hotbar slot when one exists, only displaces an occupied
    slot when it must, and reports the real slot so the caller selects the
    right one. Only the hotbar can reach the main hand, so an item stranded
    in slots 9-35 is unusable -- live 2026-07-31 that stranded Bot16's
    torches, Bot18's furnace and chest, and Bot05's crafting table.
    """
    h = _load()
    if h is None:
        return None
    return h["ensure_item_in_hotbar"](make_ctx(client), item_id)


def select_hotbar_item(client, item_id: str) -> bool:
    """Ensure ``item_id`` is in the hotbar and select that exact slot."""
    h = _load()
    if h is None:
        return False
    return bool(h["select_hotbar_item"](make_ctx(client), item_id))


def build_hollow_box(client, min_x, min_y, min_z, max_x, max_y, max_z, wall_block) -> bool:
    """Build the four walls of a box using survival placement.

    The harness moves the bot around the perimeter, keeps out of liquids and
    checks support before each placement. BASE_CONSTRUCTION places its shell
    a block at a time from wherever it happens to stand, which is why walls
    stall when a course starts out of reach.

    Survival-safe: this is block_ops (real placement), not the mc_harness
    world module, whose fill/clear_box run the server's /fill command and
    must never be exposed to the survival controller.
    """
    h = _load()
    if h is None:
        return False
    return bool(
        h["bot_build_hollow_box"](
            make_ctx(client),
            int(min_x), int(min_y), int(min_z),
            int(max_x), int(max_y), int(max_z),
            wall_block,
        )
    )


def deposit_across_chests(client, chest_positions, chest_meta=None) -> bool:
    """Empty the carried inventory across several chests, not just one.

    ``deposit_excess_to_chest`` targets a single chest with an allow-list, so
    once that chest is full the bot has nowhere to put anything and falls back
    to dropping. This walks a list of chests in order, which pairs with the
    double-chest overflow: build capacity, then actually use all of it.
    """
    h = _load()
    if h is None:
        return False
    positions = [tuple(int(v) for v in pos) for pos in chest_positions]
    if not positions:
        return False
    return bool(
        h["deposit_inventory_to_supply_chest"](
            make_ctx(client), positions, chest_meta or {}
        )
    )
