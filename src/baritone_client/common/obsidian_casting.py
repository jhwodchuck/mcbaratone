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



#: Survival reach is about 4.5 blocks. ``goto`` reports arrival within its own
#: tolerance, which is not the same thing as being able to touch the target.
REACH = 4.0


def _within_reach(client, target: Position) -> bool:
    """True when the bot can actually interact with ``target``.

    The server silently rejects a use-on-block beyond reach, so a pour issued
    from across the room looks exactly like a pour that was accepted and did
    nothing -- which is how thirteen consecutive sources reported "water did
    not land" while the bucket was never actually spent.
    """
    try:
        state = client.transport.dispatch("get_state", {})
    except Exception:
        return False
    position = (state or {}).get("block_position")
    if not isinstance(position, dict):
        return False
    try:
        dx = float(position["x"]) - target[0]
        dy = float(position["y"]) - target[1]
        dz = float(position["z"]) - target[2]
    except (KeyError, TypeError, ValueError):
        return False
    return (dx * dx + dy * dy + dz * dz) ** 0.5 <= REACH


def cast_one(client, lava: Position, *, timeout: float = 12.0) -> bool:
    """Convert one lava source to obsidian and reclaim the water.

    The water goes into the air block directly above the source: it flows down,
    meets the source, and the source becomes obsidian.
    """
    from .inventory import count_item, select_item

    x, y, z = lava
    above = (x, y + 1, z)
    if not is_lava_source(client, x, y, z) or not _is_air(client, above):
        return False
    if not _within_reach(client, above):
        print(f"  cast: too far from {above} to pour")
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

    # Dispatching the pour is not evidence it happened. If no water landed the
    # bucket was never spent, so return before the reclaim dance rather than
    # right-clicking a dry block and calling it a recovery.
    if "water" not in _block_id(_read(client, *above)):
        print(f"  cast: water did not land above {lava}")
        return False

    converted = False
    deadline = time.monotonic() + max(1.0, float(timeout))
    while time.monotonic() < deadline:
        time.sleep(0.5)
        if _block_id(_read(client, x, y, z)) == OBSIDIAN:
            converted = True
            break

    # Reclaim whether or not the cast worked: the source is standing right
    # there either way, and losing the bucket ends the capability. Verify it
    # came back -- an unnoticed empty bucket turns every later candidate into
    # "no water bucket in hand", which reads like a different bug entirely.
    if select_item(client, EMPTY_BUCKET, allow_swap=True):
        try:
            client.transport.dispatch("interact_block", dict(payload))
        except Exception:
            pass
    if count_item(client, WATER_BUCKET) < 1:
        print(f"  cast: water bucket not recovered at {above}")
    return converted


def _lava_candidates(client, radius: int, limit: int = 256) -> List[Position]:
    """Nearest lava first, searching outward in rings.

    The bridge scans in x/y/z order and does not sort, so a single wide query
    returns an arbitrary member of the batch -- ``find_nearby_block`` rings
    outward for exactly this reason. Rings matter more here than for mining:
    the nearest lava is the shortest walk, and every extra block walked toward
    standing lava is risk.
    """
    for ring in [value for value in (16, 32, 64) if value < radius] + [radius]:
        try:
            data = client.transport.dispatch(
                "find_blocks", {"blocks": [LAVA], "radius": ring, "limit": limit}
            )
        except Exception:
            return []
        found = data.get("found", []) if isinstance(data, dict) else []
        if not isinstance(found, list) or not found:
            continue
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
        if positions:
            return positions
    return []



def _standing_spot(client, lava: Position) -> Optional[Position]:
    """A dry foothold beside the source, or None.

    Walking blindly to a fixed offset is how a bot steps into the lava it came
    to cast: a source in a pool has lava for neighbours. The spot must be air,
    with something solid under it and no lava at foot level.
    """
    x, y, z = lava
    for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        foot = (x + dx, y + 1, z + dz)
        below = (x + dx, y, z + dz)
        below_id = _block_id(_read(client, *below))
        if not _is_air(client, foot) or not below_id:
            continue
        if LAVA in below_id or "air" in below_id or "water" in below_id:
            continue
        return foot
    return None



def _bucket_in_hand(client) -> bool:
    """Prove the water bucket can be held before walking to any lava.

    Carrying a bucket is not the same as being able to select one. A full
    inventory has no free hotbar slot to swap into, so select_item fails --
    and it fails at the pour, after the walk. Live on A1 2026-09-02, 36/36
    slots used: the caster found lava, approached it, and reported

        cast: no water bucket in hand

    having spent the whole trip to learn something it could have checked
    standing still. manage_inventory exists to free exactly one slot.
    """
    from .inventory import select_item

    if select_item(client, WATER_BUCKET, allow_swap=True):
        return True
    from .resources import manage_inventory

    manage_inventory(client, minimum_free_slots=1)
    if select_item(client, WATER_BUCKET, allow_swap=True):
        return True
    print("  cast: water bucket cannot be brought to hand; inventory is full")
    return False


def cast_obsidian(
    client,
    target: int,
    *,
    radius: int = 112,
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
    if not _bucket_in_hand(client):
        return 0

    candidates = _lava_candidates(client, radius)
    if not candidates:
        print(f"  cast: no lava within {radius} blocks")
        return 0

    converted = 0
    # Every skip below is a silent `continue`, and a run that skips all of them
    # prints nothing at all -- live A1 2026-09-02 produced 3.5 minutes of dead
    # air between "No reachable obsidian" and "Could not gather portal
    # materials". Count the reasons so the next failure names itself.
    skipped = {"flowing": 0, "no_foothold": 0, "walk_failed": 0, "pour_failed": 0}
    deadline = time.monotonic() + max(1.0, float(timeout))
    for position in candidates:
        if converted >= target or time.monotonic() >= deadline:
            break
        if not is_lava_source(client, *position):
            skipped["flowing"] += 1
            continue
        if not _safe(client):
            print("  cast: stopping, health or food fell while casting")
            break
        if goto is not None:
            stand = _standing_spot(client, position)
            if stand is None:
                skipped["no_foothold"] += 1
                continue
            try:
                # goto blocks and reports whether it arrived. Ignoring that
                # answer is how every pour got rejected for being out of
                # reach while the log insisted the water "did not land".
                if not goto(client, *stand):
                    skipped["walk_failed"] += 1
                    continue
            except Exception:
                skipped["walk_failed"] += 1
                continue
        if cast_one(client, position):
            converted += 1
        else:
            skipped["pour_failed"] += 1
            print(f"  cast: obsidian at {position} ({converted}/{target})")
        if count_item(client, WATER_BUCKET) < 1:
            print("  cast: stopping, the water bucket was not recovered")
            break
    if converted:
        print(f"  cast: made {converted} obsidian from lava")
    else:
        reasons = ", ".join(f"{k}={v}" for k, v in skipped.items() if v)
        print(
            f"  cast: nothing made from {len(candidates)} candidates"
            + (f" ({reasons})" if reasons else "")
        )
    return converted
