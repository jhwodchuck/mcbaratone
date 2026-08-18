"""A night shelter a bot can afford when it owns nothing.

Every other shelter primitive in this package demands blocks up front:
``build_compact_night_shelter`` needs 10 cobblestone or dirt,
``build_dirt_shelter`` needs 30, ``establish_dry_footing`` needs one. A bot
that has just respawned has none of them, so all of those return False on
their first line, and ``wait_for_safe_daylight`` is left with no protection at
all -- it spends the night calling ``defend_or_flee`` at 0/4 armour.

dragon-a and dragon-b died that way six times across 2026-08-14/15, each run
ending on the supervisor's terminal safety circuit. This module breaks the
circularity by inverting the order: hand-mine first, then place what the
mining produced.
"""

from __future__ import annotations

import time
from math import floor

from .base import (
    _has_existing_enclosure,
    _house_block_id,
    robust_place,
)


# Ground that breaks bare-handed and drops a placeable block. A bot with an
# empty inventory can mine these with nothing in hand, which is what makes a
# self-financing shelter possible. Gravel is excluded on purpose: it can drop
# flint instead of itself, so it cannot be relied on to pay for the cap.
_HAND_MINEABLE_GROUND = (
    "minecraft:dirt",
    "minecraft:grass_block",
    "minecraft:coarse_dirt",
    "minecraft:rooted_dirt",
    "minecraft:podzol",
    "minecraft:mycelium",
    "minecraft:mud",
    "minecraft:sand",
    "minecraft:red_sand",
    "minecraft:soul_sand",
    "minecraft:soul_soil",
    "minecraft:clay",
    "minecraft:snow_block",
)
_UNSAFE_SHELTER_GROUND = ("lava", "water", "bedrock")


#: How far to look for diggable ground when the floor underfoot is stone. Kept
#: small: this runs at night with no armour, so a long walk is worse than no
#: shelter, and each candidate column costs a bridge read.
DIGGABLE_SEARCH_RADIUS = 3


def _nearest_diggable_column(client, x: int, feet_y: int, z: int):
    """Nearest standable column whose floor can be broken by hand, or None.

    A bot that respawns on a mountain has stone underfoot and no pickaxe --
    which is the very tool it is trying to rebuild -- so the shaft cannot be
    started where it stands. Dirt or sand a few blocks away is usually enough.
    Candidates are searched nearest-first so the bot moves as little as
    possible in the dark.
    """
    candidates = sorted(
        (dx * dx + dz * dz, x + dx, z + dz)
        for dx in range(-DIGGABLE_SEARCH_RADIUS, DIGGABLE_SEARCH_RADIUS + 1)
        for dz in range(-DIGGABLE_SEARCH_RADIUS, DIGGABLE_SEARCH_RADIUS + 1)
        if dx or dz
    )
    for _distance, cx, cz in candidates:
        floor = str(_house_block_id(client, cx, feet_y - 1, cz) or "")
        if floor not in _HAND_MINEABLE_GROUND:
            continue
        # The bot has to be able to stand there, and it must not be another
        # puddle -- water would defeat the descent exactly as it did before.
        standing = str(_house_block_id(client, cx, feet_y, cz) or "")
        if "air" not in standing:
            continue
        return (cx, feet_y, cz)
    return None


def _read_position(client):
    """Return (x, feet_y, z, precise_x, precise_z), or None if unreadable."""
    try:
        state = client.transport.dispatch("get_state", {})
        block = state.get("block_position", state.get("position", {}))
        precise = state.get("position", block)
        return (
            int(block["x"]),
            int(block["y"]),
            int(block["z"]),
            float(precise.get("x", block["x"])),
            float(precise.get("z", block["z"])),
        )
    except Exception:
        return None


def _footprint_columns(precise_x: float, precise_z: float):
    """Every column the player's collision box rests on.

    The hitbox is 0.6 wide, so a player is very often straddling two columns
    on each axis and is held up by as many as four. Digging only the column
    under ``block_position`` leaves the others supporting the body, which never
    falls -- exactly what stalled dragon-a at x=-1.894, z=19.882, where the one
    column that got dug went to air while three snow blocks kept holding it.
    ``shelf_escape._supporting_column`` resolves the same footprint for the
    same reason.
    """
    return sorted(
        {
            (floor(precise_x + dx), floor(precise_z + dz))
            for dx in (-0.3, 0.3)
            for dz in (-0.3, 0.3)
        }
    )


def dig_and_seal_night_hole(client, depth: int = 3) -> bool:
    """Enclose a bot that owns nothing by mining the blocks that seal it in.

    Every other shelter primitive here demands blocks up front --
    ``build_compact_night_shelter`` needs 10 cobblestone or dirt,
    ``build_dirt_shelter`` needs 30, ``establish_dry_footing`` needs one -- so
    all of them return False on their first line for a freshly respawned bot.
    ``wait_for_safe_daylight`` then has no protection left and spends the whole
    night calling ``defend_or_flee`` at 0/4 armour, which is how A1Bot's
    dragon-lab siblings died six times in two days before the supervisor gave
    up permanently.

    This breaks that circularity by inverting the order: hand-mine first, then
    place what the mining produced. Dirt, sand and the rest of
    ``_HAND_MINEABLE_GROUND`` break with an empty hand and drop themselves, so
    the shaft pays for its own lid.

    A block is only dug once the previous one is verified gone and the body is
    verified to have fallen, and the first block is dug before committing to
    depth so the cap material is in hand before the hole is deep enough to
    trap anything. Returns True only when a solid cap is confirmed overhead.
    """
    from .shelf_escape import _first_placeable_item

    start = _read_position(client)
    if start is None:
        print("Night hole: cannot determine player position")
        return False
    x, feet_y, z, precise_x, precise_z = start

    # Standing in liquid defeats the whole method: water holds the body up, so
    # removing the block underneath never drops it and the descent check can
    # never pass -- and the shaft would flood anyway. Observed live on
    # dragon-b 2026-08-17, which respawned with its feet in water and logged
    # "body did not fall into the shaft" on ground that was perfectly good
    # dirt. Get to dry land first; if that is impossible, say so rather than
    # digging a hole that cannot work.
    feet = str(_house_block_id(client, x, feet_y, z) or "")
    if any(token in feet for token in ("water", "lava")):
        print(f"Night hole: standing in {feet}; seeking dry ground before digging")
        from .navigation import goto
        from .surface_recovery import reach_dry_surface

        try:
            landed = reach_dry_surface(
                client, origin=(x, feet_y, z), expected_y=feet_y, goto=goto
            )
        except Exception as exc:
            print(f"Night hole: dry-surface recovery failed ({exc})")
            landed = None
        if not landed:
            print("Night hole: could not reach dry ground; refusing to dig in liquid")
            return False
        dried = _read_position(client)
        if dried is None:
            return False
        x, feet_y, z, precise_x, precise_z = dried

    dug = 0
    relocated = False
    while dug < max(1, int(depth)):
        floor_y = feet_y - 1
        # Every column under the collision box has to go, or the body simply
        # stands on whichever one is left.
        columns = _footprint_columns(precise_x, precise_z)
        grounds = {
            column: str(_house_block_id(client, column[0], floor_y, column[1]) or "")
            for column in columns
        }
        blocking = {
            column: ground
            for column, ground in grounds.items()
            if "air" not in ground
        }
        if any(
            token in ground
            for ground in blocking.values()
            for token in _UNSAFE_SHELTER_GROUND
        ):
            print(f"Night hole: refusing to dig into {sorted(set(blocking.values()))}")
            break
        undiggable = {
            column: ground
            for column, ground in blocking.items()
            if ground not in _HAND_MINEABLE_GROUND
        }
        if undiggable:
            if dug == 0 and not relocated:
                spot = _nearest_diggable_column(client, x, feet_y, z)
                if spot is None:
                    print(
                        "Night hole: no hand-mineable ground within "
                        f"{DIGGABLE_SEARCH_RADIUS} blocks"
                    )
                else:
                    from .navigation import goto

                    print(
                        f"Night hole: {sorted(set(undiggable.values()))} underfoot "
                        f"cannot be hand-mined; moving to diggable ground at {spot}"
                    )
                    relocated = True
                    if goto(client, spot[0], spot[1], spot[2], timeout=30, tolerance=1.5):
                        refreshed = _read_position(client)
                        if refreshed is not None:
                            x, feet_y, z, precise_x, precise_z = refreshed
                            continue
                    print("Night hole: could not reach the diggable ground")
            print(
                f"Night hole: {sorted(set(undiggable.values()))} at y={floor_y} "
                "is not hand-mineable; stopping descent"
            )
            break

        for column, ground in blocking.items():
            client.transport.dispatch(
                "dig_block",
                {"x": column[0], "y": floor_y, "z": column[1], "face": "UP",
                 "max_ticks": 160},
            )
            deadline = time.monotonic() + 8.0
            while time.monotonic() < deadline:
                time.sleep(0.3)
                if "air" in str(
                    _house_block_id(client, column[0], floor_y, column[1]) or ""
                ):
                    break
            else:
                print(
                    f"Night hole: dig_block did not verify a break at {column}; "
                    "stopping descent"
                )
                blocking = None
                break
        if blocking is None:
            break

        settled = time.monotonic() + 5.0
        while time.monotonic() < settled:
            time.sleep(0.3)
            refreshed = _read_position(client)
            if refreshed is None:
                continue
            if refreshed[1] <= feet_y - 1:
                x, feet_y, z, precise_x, precise_z = refreshed
                dug += 1
                break
        else:
            print("Night hole: body did not fall into the shaft; stopping descent")
            break

        if _first_placeable_item(client) is None:
            print("Night hole: no placeable block recovered from the dig; stopping")
            break

    if dug <= 0:
        return False

    material = _first_placeable_item(client)
    if material is None:
        print("Night hole: nothing placeable to cap the shaft with")
        return False

    cap_y = feet_y + 2
    if not robust_place(client, x, cap_y, z, material):
        print(f"Night hole: could not cap the shaft at y={cap_y}")
        return False

    if not _has_existing_enclosure(client):
        print("Night hole: cap placed but the enclosure did not verify")
        return False
    print(f"Night hole: sealed {dug} blocks down under {material} at y={cap_y}")
    return True
