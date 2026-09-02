"""Cast obsidian from lava instead of depending on a depleting natural stock.

Natural obsidian only exists where water has already met lava, so it is a
fixed, non-renewing supply. A1 exhausted what was within reach of its base:
live 2026-09-02 it wandered 330 blocks over hours, logged "No reachable
minecraft:obsidian in the bounded search", and failed NETHER_AND_BLAZE on a
~3 minute cycle -- while carrying a water bucket, an empty bucket and a diamond
pickaxe, at y=-51, where lava is abundant.

Pouring water onto a lava source turns it to obsidian, which makes the supply
renewable and removes the blocker permanently rather than widening a search
radius. This needs no bridge change: `interact_block` calls `useItemOn` with
the held item, which is exactly a right-click with a bucket, and `get_block`
already returns the full blockstate.

Only the *source* matters. Water meeting flowing lava produces cobblestone, not
obsidian, and a caster that skips that check spends its bucket and reports a
stone block as success -- the same shape as mining a target that was never
valid. `level` is 0 on a source, non-zero on flowing lava.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple

LAVA = "minecraft:lava"
OBSIDIAN = "minecraft:obsidian"
WATER_BUCKET = "minecraft:water_bucket"
EMPTY_BUCKET = "minecraft:bucket"

#: Casting walks the bot to standing lava, which is where it dies. These are
#: deliberately stricter than the mining gates.
MIN_HEALTH = 16.0
MIN_FOOD = 12

Position = Tuple[int, int, int]


def _read(client, x: int, y: int, z: int) -> Dict[str, Any]:
    try:
        response = client.transport.dispatch("get_block", {"x": x, "y": y, "z": z})
    except Exception:
        return {}
    data = response.get("data", response) if isinstance(response, dict) else {}
    return data if isinstance(data, dict) else {}


def _block_id(info: Dict[str, Any]) -> str:
    return str(info.get("id") or info.get("block") or "")


def is_lava_source(client, x: int, y: int, z: int) -> bool:
    """True only for a lava *source* block.

    Flowing lava becomes cobblestone under water, so casting against it burns
    the bucket and yields nothing worth mining.
    """
    info = _read(client, x, y, z)
    if _block_id(info) != LAVA:
        return False
    state = info.get("state") or {}
    if not isinstance(state, dict):
        return False
    return str(state.get("level", "")).strip() == "0"


def _is_air(client, position: Position) -> bool:
    return "air" in _block_id(_read(client, *position))


def _safe(client) -> bool:
    try:
        state = client.transport.dispatch("get_state", {})
    except Exception:
        return False
    if not isinstance(state, dict) or state.get("is_dead", False):
        return False
    return (
        float(state.get("health", 0) or 0) >= MIN_HEALTH
        and int(state.get("food_level", 0) or 0) >= MIN_FOOD
    )


def cast_one(client, lava: Position, *, timeout: float = 12.0) -> bool:
    """Convert one lava source to obsidian and reclaim the water.

    The water goes into the air block directly above the source: it flows down,
    meets the source, and the source becomes obsidian.
    """
    from .inventory import select_item

    x, y, z = lava
    above = (x, y + 1, z)
    if not is_lava_source(client, x, y, z) or not _is_air(client, above):
        return False
    if not select_item(client, WATER_BUCKET, allow_swap=True):
        print("  cast: no water bucket in hand")
        return False

    payload = {"x": above[0], "y": above[1], "z": above[2]}
    try:
        client.transport.dispatch("interact_block", dict(payload))
    except Exception as exc:
        print(f"  cast: pouring water failed: {exc}")
        return False

    converted = False
    deadline = time.monotonic() + max(1.0, float(timeout))
    while time.monotonic() < deadline:
        time.sleep(0.5)
        if _block_id(_read(client, x, y, z)) == OBSIDIAN:
            converted = True
            break

    # Reclaim the water whether or not the cast worked. Losing the bucket ends
    # the whole capability, and the placed source is standing right there
    # either way -- there is no cheaper moment to pick it back up.
    if select_item(client, EMPTY_BUCKET, allow_swap=True):
        try:
            client.transport.dispatch("interact_block", dict(payload))
        except Exception:
            pass
    return converted


def _lava_candidates(client, radius: int, limit: int = 256) -> List[Position]:
    try:
        data = client.transport.dispatch(
            "find_blocks", {"blocks": [LAVA], "radius": radius, "limit": limit}
        )
    except Exception:
        return []
    found = data.get("found", []) if isinstance(data, dict) else []
    if not isinstance(found, list):
        return []
    ordered = sorted(
        (entry for entry in found if isinstance(entry, dict)),
        key=lambda entry: float(entry.get("distance", float("inf"))),
    )
    positions: List[Position] = []
    for entry in ordered:
        try:
            positions.append((int(entry["x"]), int(entry["y"]), int(entry["z"])))
        except (KeyError, TypeError, ValueError):
            continue
    return positions


def cast_obsidian(
    client,
    target: int,
    *,
    radius: int = 24,
    timeout: float = 240.0,
    goto: Optional[Any] = None,
) -> int:
    """Cast up to ``target`` obsidian blocks from nearby lava sources.

    Returns the number converted. The blocks are left in the world for the
    normal miner to collect: casting's job is to make obsidian *exist* within
    reach, which is precisely what "No reachable obsidian" reported was missing.
    """
    from .inventory import count_item

    if target <= 0:
        return 0
    if count_item(client, WATER_BUCKET) < 1:
        print("  cast: no water bucket carried; cannot make obsidian")
        return 0
    if not _safe(client):
        print("  cast: health or food too low to approach lava")
        return 0

    candidates = _lava_candidates(client, radius)
    if not candidates:
        print(f"  cast: no lava within {radius} blocks")
        return 0

    converted = 0
    deadline = time.monotonic() + max(1.0, float(timeout))
    for position in candidates:
        if converted >= target or time.monotonic() >= deadline:
            break
        if not is_lava_source(client, *position):
            continue
        if not _safe(client):
            print("  cast: stopping, health or food fell while casting")
            break
        if goto is not None:
            # Stand on the block above the source's neighbour rather than in
            # the lava's own column.
            try:
                goto(client, position[0] + 1, position[1] + 1, position[2])
            except Exception:
                continue
        if cast_one(client, position):
            converted += 1
            print(f"  cast: obsidian at {position} ({converted}/{target})")
    if converted:
        print(f"  cast: made {converted} obsidian from lava")
    return converted
