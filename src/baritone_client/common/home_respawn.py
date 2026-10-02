"""Make the durable home base the player's respawn point.

Without a bed the player respawns at world spawn. When home is hundreds of
blocks from there, every death costs the whole walk back, and the home farm
stops growing while its chunks are unloaded. The only bed ever recorded for
the starter house was one found far away on an expedition, and nothing ever
used a bed to set the respawn point.

This step works only at home. It crafts a bed from home storage (bed, wool,
or string), places it inside the starter house, uses it, and records the
home respawn only when the game confirms "Respawn point set".
"""

from __future__ import annotations

import time
from math import hypot
from typing import Any, Mapping, Optional, Sequence, Tuple

from .navigation import allow_recovery_navigation

HOME_RESPAWN_KEY = "home_respawn"
#: Only work on the respawn point while actually at home.
HOME_RADIUS = 32.0
#: Storage trips for bed materials stay inside the base.
HOME_STORAGE_RADIUS = 48.0
#: When home storage cannot finish a bed, ordinary storage trips reach this far.
FAR_STORAGE_RADIUS = 160.0
#: ...but only to storage near the player's own level. A live trip to cave
#: chests forty blocks down for three wool's worth of string ended in a death.
FAR_STORAGE_MAX_VERTICAL = 16.0
#: When storage cannot finish the bed, hunt sheep under these bounds.
SHEEP_HUNT_KEY = "home_respawn_sheep_hunt"
SHEEP_HUNT_INTERVAL = 900.0
SHEEP_HUNT_MIN_HEALTH = 18.0
SHEEP_HUNT_PREPARED_RESERVE = 8
SHEEP_HUNT_TIMEOUT = 420
SHEEP_HUNT_LATEST_START = 9000
SHEEP_HUNT_LATEST_WORLD_TIME = 11000
SHEEP_HUNT_SPARE_KILLS = 3
SHEEP_HUNT_RINGS = (128.0, 256.0, 512.0)
SHEEP_HUNT_FAILURES_PER_RING = 3
SHEEP_HUNT_SECTORS = (
    (1, -1), (-1, -1), (1, 1), (-1, 1), (0, -1), (1, 0), (0, 1), (-1, 0),
)
#: How many foot positions near home to consider when placing the bed.
BED_SITE_CANDIDATES = 60
#: Containers this close to the starter-house origin count as inside it.
HOUSE_RADIUS = 8.0
#: One nearby tree for a bed's three planks: short and close, never an expedition.
PLANK_TRIP_TIMEOUT = 120
PLANK_TRIP_RADIUS = 48.0
PLANK_TRIP_MIN_HEALTH = 14.0
#: The survival loop calls this every few seconds.
RETRY_INTERVAL = 300.0
BED_COLORS = (
    "white", "orange", "magenta", "light_blue", "yellow", "lime", "pink",
    "gray", "light_gray", "cyan", "purple", "blue", "brown", "green", "red",
    "black",
)
BED_ITEMS = tuple(f"minecraft:{color}_bed" for color in BED_COLORS)
WOOL_ITEMS = tuple(f"minecraft:{color}_wool" for color in BED_COLORS)
_WHITE_WOOL = "minecraft:white_wool"
_WHITE_BED = "minecraft:white_bed"
_STRING = "minecraft:string"
_SPAWN_SET_MESSAGES = ("respawn point set", "block.minecraft.set_spawn")


def _custom(state: Any) -> dict:
    custom = getattr(state, "custom_data", None)
    return custom if isinstance(custom, dict) else {}


def _xyz(value: Any) -> Optional[Tuple[int, int, int]]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)) and len(value) >= 3:
        try:
            return (int(value[0]), int(value[1]), int(value[2]))
        except (TypeError, ValueError):
            return None
    return None


def _position(live: Mapping[str, Any]) -> Optional[Tuple[float, float, float]]:
    position = live.get("block_position", live.get("position", {}))
    try:
        return (float(position["x"]), float(position["y"]), float(position["z"]))
    except (KeyError, TypeError, ValueError):
        return None


def _flat(a, b) -> float:
    return hypot(float(a[0]) - float(b[0]), float(a[2]) - float(b[2]))


def _house(state: Any) -> dict:
    structures = _custom(state).get("structures", {})
    house = structures.get("starter_house", {}) if isinstance(structures, dict) else {}
    return house if isinstance(house, dict) else {}


def _bed_slots(state: Any, anchor) -> list:
    """Every interior cell of the starter house, favoured slots first.

    Four fixed slots were all it tried. Live A1 2026-10-01: the house floor had
    holes where the old bed stood, a crafting table sat in the middle and a
    furnace and chest filled two more cells, so all four failed their layout
    check while a whole row of the interior was free. The interior is 5x5 above
    the 7x7 shell; offer all of it, nearest the original corner first.
    """
    origin = _xyz(_house(state).get("origin")) or anchor
    x, y, z = origin
    favoured = [
        (x + 4, y + 1, z + 3),
        (x + 4, y + 1, z + 4),
        (x + 3, y + 1, z + 4),
        (x + 2, y + 1, z + 2),
    ]
    interior = sorted(
        ((x + dx, y + 1, z + dz) for dx in range(1, 6) for dz in range(1, 6)),
        key=lambda cell: (abs(cell[0] - (x + 4)) + abs(cell[2] - (z + 4)), cell),
    )
    return favoured + [cell for cell in interior if cell not in favoured]


def _is_bed(client: Any, position) -> bool:
    try:
        block = client.transport.dispatch(
            "get_block", {"x": position[0], "y": position[1], "z": position[2]}
        )
    except Exception:
        return False
    return str(block.get("id", "")).endswith("_bed")


def _hostile_close(client: Any, live: Mapping[str, Any]) -> bool:
    from .combat import _threat_can_reach_player, scan_for_threats

    try:
        threats = scan_for_threats(client, radius=16, player_state=dict(live))
    except Exception:
        return True
    return any(
        threat.get("distance", 999) <= 12 and _threat_can_reach_player(threat, live)
        for threat in threats
    )


def _carried_bed(client: Any) -> Optional[str]:
    from .inventory import count_item

    return next((item for item in BED_ITEMS if count_item(client, item) > 0), None)


def _withdraw_from_containers(
    client: Any,
    state: Any,
    item: str,
    wanted: int,
    *,
    origin,
    radius: float,
    max_vertical: Optional[float],
    recovery: bool,
    house=None,
) -> None:
    """Withdraw from cataloged containers chosen here, walking beside each.

    The catalog withdrawal keeps only its eight nearest candidates before it
    applies a vertical limit, so deep cave chests could crowd out a usable
    surface chest entirely. Eligible containers are chosen first, then each
    is approached and withdrawn from within reach.
    """
    from .inventory import count_item, withdraw_required_from_catalog
    from .navigation import goto
    from .storage_catalog import catalog_for
    from .tasks import PlayerDeathDetected

    if count_item(client, item) >= wanted:
        return
    try:
        rows = catalog_for(client, state).find_item(item)
    except Exception as exc:
        print(f"HOME RESPAWN: storage catalog unavailable ({exc})")
        return
    here = _position(client.transport.dispatch("get_state", {})) or tuple(origin)
    chests = [
        (int(row["x"]), int(row["y"]), int(row["z"]))
        for row in rows
        if "overworld" in str(row.get("dimension", "overworld"))
    ]
    chests = [
        chest
        for chest in chests
        if _flat(chest, origin) <= radius
        and (max_vertical is None or abs(chest[1] - here[1]) <= max_vertical)
    ]
    center = tuple(house) if house is not None else tuple(origin)
    # Containers inside the starter house come first: a built interior gives
    # clean access, while a chest by the farm can sit behind crops.
    chests.sort(key=lambda chest: (_flat(chest, center) > HOUSE_RADIUS, _flat(chest, here)))
    for chest in chests:
        if count_item(client, item) >= wanted:
            return
        try:
            if recovery and _flat(chest, here) <= 16:
                from . import harness_ops

                harness_ops.move_near(client, *chest, timeout=30.0)
            else:
                goto(client, *chest, timeout=180, tolerance=3.5, radius=3)
            withdraw_required_from_catalog(
                client,
                {item: wanted},
                state=state,
                max_travel_distance=6.0,
                allow_recovery_access=recovery,
            )
        except PlayerDeathDetected:
            raise
        except Exception as exc:
            print(f"HOME RESPAWN: container at {chest} failed ({exc}); trying the next")


#: Public name for other recovery steps (armour) that fetch from home storage.
withdraw_from_home_containers = _withdraw_from_containers


def _withdraw_from_home(client: Any, state: Any, anchor, item: str, wanted: int) -> None:
    """Home containers, reachable even while survival is critical.

    Storage refuses to travel to a container outside the survival margin, and
    a wounded player is exactly who needs the respawn point. It may still
    open a container within reach, so step beside each home container first.
    """
    _withdraw_from_containers(
        client, state, item, wanted,
        origin=anchor, radius=HOME_STORAGE_RADIUS, max_vertical=None, recovery=True,
        house=_xyz(_house(state).get("origin")) or tuple(anchor),
    )


def _withdraw_from_wider_storage(client: Any, state: Any, item: str, wanted: int) -> None:
    """Storage near the player's own level, only while travel is safe."""
    from .storage_safety import storage_travel_safe

    live = client.transport.dispatch("get_state", {})
    here = _position(live) if isinstance(live, Mapping) else None
    if here is None or not storage_travel_safe(live):
        return
    _withdraw_from_containers(
        client, state, item, wanted,
        origin=here, radius=FAR_STORAGE_RADIUS,
        max_vertical=FAR_STORAGE_MAX_VERTICAL, recovery=False,
    )


def _hunt_ready(state: Any, live: Mapping[str, Any], now: float) -> bool:
    record = _custom(state).get(SHEEP_HUNT_KEY, {})
    record = record if isinstance(record, dict) else {}
    try:
        last = float(record.get("last_attempt", 0) or 0)
        health = float(live.get("health", 0) or 0)
        food = int(live.get("food_level", live.get("food", 0)) or 0)
        world_time = int(live.get("world_time", 0) or 0) % 24000
    except (TypeError, ValueError):
        return False
    return (
        now - last >= SHEEP_HUNT_INTERVAL
        and not bool(live.get("is_dead"))
        and health >= SHEEP_HUNT_MIN_HEALTH
        and food >= 18
        and world_time < SHEEP_HUNT_LATEST_START
    )


def _hunt_food_ready(client: Any) -> bool:
    """A full hunger bar is not food packed for a bounded expedition."""
    from ..automator.end_readiness import PREPARED_FOOD_ITEMS
    from .inventory import get_inventory

    try:
        inventory = get_inventory(client)
        if not isinstance(inventory, Mapping):
            return False
        counts = [inventory.get(item, 0) for item in PREPARED_FOOD_ITEMS]
        if any(not isinstance(n, int) or isinstance(n, bool) or n < 0 for n in counts):
            return False
        return sum(counts) >= SHEEP_HUNT_PREPARED_RESERVE
    except Exception:
        return False


def _hunt_sheep_for_wool(client: Any, state: Any, anchor) -> None:
    """Hunt sheep for the wool home storage cannot supply, then come home.

    Bounded on every axis: full health and regenerating food to start,
    daylight only, passive hunting abandoned when a hostile closes in, a
    kill cap and a timeout, and a persisted cooldown between attempts so a
    death on the way cannot turn into a loop. The radius widens after
    fruitless attempts, and the search sector rotates around home.
    """
    from .combat import hunt_mobs
    from .navigation import goto

    wanted = _missing_wool(client)
    live = client.transport.dispatch("get_state", {})
    now = time.time()
    if not wanted or not isinstance(live, Mapping) or not _hunt_ready(state, live, now):
        return
    if not _hunt_food_ready(client):
        print(f"HOME RESPAWN: deferring sheep hunt until {SHEEP_HUNT_PREPARED_RESERVE} prepared food are carried")
        return
    custom = _custom(state)
    record = custom.get(SHEEP_HUNT_KEY)
    record = record if isinstance(record, dict) else {}
    failures = int(record.get("failures", 0) or 0)
    sector = int(record.get("sector", 0) or 0)
    ring = SHEEP_HUNT_RINGS[min(failures // SHEEP_HUNT_FAILURES_PER_RING, len(SHEEP_HUNT_RINGS) - 1)]
    dx, dz = SHEEP_HUNT_SECTORS[sector % len(SHEEP_HUNT_SECTORS)]
    center = (int(anchor[0] + dx * ring / 2), int(anchor[2] + dz * ring / 2))
    # Recorded before leaving: a death during the hunt still waits out the
    # cooldown instead of sending the respawned player straight back out.
    record.update(last_attempt=now, sector=sector + 1)
    custom[SHEEP_HUNT_KEY] = record
    before = _missing_wool(client)
    wool = _best_wool(client)[0]  # only after the safety gates: it reads the inventory
    print(
        f"HOME RESPAWN: hunting sheep for {wanted} {_wool_name(wool)} within {ring:.0f} "
        f"blocks, searching toward {center}"
    )
    result = None
    try:
        result = hunt_mobs(
            client,
            mob_types=["sheep"],
            required_loot={wool: wanted},
            search_radius=64,
            timeout=SHEEP_HUNT_TIMEOUT,
            heal_threshold=12.0,
            abort_on_other_hostiles=True,
            latest_world_time=SHEEP_HUNT_LATEST_WORLD_TIME,
            max_distance_from_origin=ring,
            exploration_center=center,
            max_kills=wanted + SHEEP_HUNT_SPARE_KILLS,
        )
    finally:
        try:
            client.transport.dispatch("cancel", {})
        except Exception:
            pass
        gained = _missing_wool(client) < before
        reason = str(getattr(result, "reason", "") or "no result")
        # A hostile or the daylight boundary cut the search short; that says
        # nothing about whether sheep live here, so it must not widen the ring.
        interrupted = reason.startswith("Hostile") or "Daylight" in reason or "Night" in reason
        if gained:
            record["failures"] = 0
        elif not interrupted:
            record["failures"] = failures + 1
        print(
            "HOME RESPAWN: sheep hunt "
            + ("gained wool" if gained else f"found no {_wool_name(wool)} ({reason})")
            + "; returning home"
        )
        goto(client, int(anchor[0]), int(anchor[1]), int(anchor[2]),
             timeout=300, tolerance=6.0, radius=4)


def _wool_name(wool: str) -> str:
    return wool.split(":")[-1].replace("_", " ")


def _best_wool(client: Any) -> Tuple[str, int]:
    """The wool colour closest to the three a bed takes, and how many are carried.

    A bed takes any three wool of ONE colour. This used to count white only, so
    live A1 spent days from 2026-09-29 failing "storage cannot supply 3 more
    white wool" while holding other colours. White keeps a one-wool head start
    (most sheep are white and string crafts into it), so a stray black wool does
    not redirect the search away from white.
    """
    from .inventory import count_item

    best = (_WHITE_WOOL, 0)
    best_key = (-1, False)
    for wool in WOOL_ITEMS:
        carried = count_item(client, wool)
        key = (carried + (1 if wool == _WHITE_WOOL else 0), wool == _WHITE_WOOL)
        if key > best_key:
            best, best_key = (wool, carried), key
    return best


def _missing_wool(client: Any) -> int:
    return max(0, 3 - _best_wool(client)[1])


def _withdraw_any_wool(client: Any, withdraw, *, wanted: int = 3) -> None:
    """Fetch wool of the best colour first, then any other colour storage holds."""
    first = _best_wool(client)[0]
    for wool in (first, *[w for w in WOOL_ITEMS if w != first]):
        if not _missing_wool(client):
            return
        withdraw(wool, wanted)


def _craft_wool_from_string(client: Any) -> None:
    from . import harness_ops
    from .inventory import count_item

    if _best_wool(client)[0] != _WHITE_WOOL:
        return  # string makes white wool, which would not add to another colour
    crafts = min(_missing_wool(client), count_item(client, _STRING) // 4)
    if crafts <= 0 or not harness_ops.ensure_crafting_table_open(client):
        return
    harness_ops.craft_recipe_manual(
        client,
        _WHITE_WOOL,
        [(_STRING, 1), (_STRING, 2), (_STRING, 4), (_STRING, 5)],
        crafts=crafts,
    )
    client.transport.dispatch("close_screen", {})


def _obtain_bed(client: Any, state: Any, anchor) -> Optional[str]:
    """Carry a bed, crafting it from home wool or string when needed."""
    from . import harness_ops
    from .inventory import count_item

    bed = _carried_bed(client)
    if bed:
        return bed
    _withdraw_from_home(client, state, anchor, _WHITE_BED, 1)
    bed = _carried_bed(client)
    if bed:
        return bed
    # String first: it has no other use here, and it usually sits in the
    # house's own supply chest beside the crafting table.
    _withdraw_from_home(client, state, anchor, _STRING, 4 * _missing_wool(client))
    _craft_wool_from_string(client)
    _withdraw_any_wool(client, lambda wool, n: _withdraw_from_home(client, state, anchor, wool, n))
    if _missing_wool(client):
        # Home cannot finish the bed. Reach wider storage near the player's
        # own level, but only while survival allows travel.
        _withdraw_from_wider_storage(client, state, _STRING, 4 * _missing_wool(client))
        _craft_wool_from_string(client)
        if _missing_wool(client):
            _withdraw_any_wool(
                client, lambda wool, n: _withdraw_from_wider_storage(client, state, wool, n)
            )
        if _missing_wool(client):
            _hunt_sheep_for_wool(client, state, anchor)
    if _missing_wool(client):
        print(
            f"HOME RESPAWN: storage cannot supply {_missing_wool(client)} more "
            f"{_wool_name(_best_wool(client)[0])} (or string for it)"
        )
        return None
    if harness_ops.count_any_planks(client) < 3 and not _fetch_planks(client, state, anchor):
        print("HOME RESPAWN: need 3 planks for a bed")
        return None
    if not harness_ops.ensure_crafting_table_open(client):
        return None
    wool = _best_wool(client)[0]
    crafted = harness_ops.craft_bed_manual(client, wool.replace("_wool", "_bed"))
    client.transport.dispatch("close_screen", {})
    return _carried_bed(client) if crafted else None


def _fetch_planks(client: Any, state: Any, anchor) -> bool:
    """Three planks for the bed: home storage, carried logs, or one nearby tree.

    Live A1 2026-10-01 held the wool and had no planks or logs at all, so the
    step gave up at "need 3 planks". The tree trip is bounded tightly: healthy,
    fed, daylight, a short timeout, a small radius and threat aborts.
    """
    from . import harness_ops
    from . import inventory
    from .resources import gather_wood

    _withdraw_from_home(client, state, anchor, "minecraft:oak_planks", 3)
    if harness_ops.count_any_planks(client) >= 3:
        return True
    live = client.transport.dispatch("get_state", {})
    try:
        ready = (
            isinstance(live, Mapping)
            and not live.get("is_dead")
            and float(live.get("health", 0) or 0) >= PLANK_TRIP_MIN_HEALTH
            and int(live.get("food_level", live.get("food", 0)) or 0) >= 14
            and int(live.get("world_time", 0) or 0) % 24000 < SHEEP_HUNT_LATEST_START
        )
    except (TypeError, ValueError):
        ready = False
    if not ready:
        return False
    try:
        gather_wood(
            client, count=1, timeout=PLANK_TRIP_TIMEOUT, max_distance_from_origin=PLANK_TRIP_RADIUS,
            abort_on_threats=True, minimum_health=PLANK_TRIP_MIN_HEALTH,
        )
        inventory._ensure_raw_planks(client, 3)
    finally:
        try:
            client.transport.dispatch("cancel", {})
        except Exception:
            pass
    return harness_ops.count_any_planks(client) >= 3


_AIRLIKE = {"minecraft:air", "minecraft:cave_air", "minecraft:short_grass",
            "minecraft:tall_grass", "minecraft:fern", "minecraft:snow",
            "minecraft:leaf_litter", "minecraft:dandelion", "minecraft:poppy"}
_NOT_FLOOR = _AIRLIKE | {"minecraft:water", "minecraft:lava", "minecraft:void_air",
                         "minecraft:powder_snow", "minecraft:farmland", ""}
_DIRECTIONS = ((1, 0), (-1, 0), (0, 1), (0, -1))


def _block(client: Any, x: int, y: int, z: int) -> str:
    try:
        return str(client.transport.dispatch("get_block", {"x": x, "y": y, "z": z}).get("id", ""))
    except Exception:
        return ""


def _open_cell(client: Any, cell) -> bool:
    """Air (or plant) at the cell and above it, standing on solid ground."""
    x, y, z = cell
    return (
        _block(client, x, y, z) in _AIRLIKE
        and _block(client, x, y + 1, z) in _AIRLIKE
        and _block(client, x, y - 1, z) not in _NOT_FLOOR
    )


def _bed_candidates(state: Any, anchor) -> list:
    """House slots first, then open ground in rings around the base anchor."""
    seen, ordered = set(), []
    for slot in _bed_slots(state, anchor):
        if slot not in seen:
            seen.add(slot)
            ordered.append(slot)
    # Around the house when it is known: the base anchor can sit well outside it
    # (A1's moved 6 blocks north), over a farm or shelter roof.
    house = _xyz(_house(state).get("origin"))
    ax, ay, az = (house[0] + 3, house[1], house[2] + 3) if house else (int(v) for v in anchor)
    ring = sorted(
        ((ax + dx, ay + 1, az + dz) for dx in range(-6, 7) for dz in range(-6, 7)),
        key=lambda cell: (abs(cell[0] - ax) + abs(cell[2] - az)),
    )
    for cell in ring:
        if cell not in seen:
            seen.add(cell)
            ordered.append(cell)
    return ordered[:BED_SITE_CANDIDATES]


def _bed_layout(client: Any, foot):
    """Return (direction, stand) giving a free head cell and a stand behind the foot."""
    if not _open_cell(client, foot):
        return None
    for dx, dz in _DIRECTIONS:
        head = (foot[0] + dx, foot[1], foot[2] + dz)
        stand = (foot[0] - dx, foot[1], foot[2] - dz)
        if _open_cell(client, head) and _open_cell(client, stand):
            return (dx, dz), stand
    return None


def _place_bed_at(client: Any, bed_item: str, foot, direction, stand) -> bool:
    """Stand behind the foot facing the head's direction, place, verify both halves.

    The bridge places without rotating the player, and a bed's head goes one
    block in the player's facing direction. A blocked head cell makes the
    server reject the bed while the client briefly shows it, so both halves
    are read back after a short delay rather than trusting the placement.
    """
    from .inventory import select_item
    from .navigation import goto

    dx, dz = direction
    goto(client, *stand, timeout=30, tolerance=0.8)
    if not select_item(client, bed_item, allow_swap=True):
        return False
    client.transport.dispatch(
        "look_at",
        {"x": foot[0] + 0.5 + dx * 6, "y": foot[1] + 1.0, "z": foot[2] + 0.5 + dz * 6},
    )
    time.sleep(0.2)
    try:
        client.transport.dispatch("place_block", {"x": foot[0], "y": foot[1], "z": foot[2]})
    except Exception as exc:
        print(f"HOME RESPAWN: bed placement at {foot} refused ({exc})")
        return False
    time.sleep(1.0)
    head = (foot[0] + dx, foot[1], foot[2] + dz)
    return _is_bed(client, foot) and _is_bed(client, head)


def _place_home_bed(client: Any, state: Any, anchor) -> Optional[Tuple[int, int, int]]:
    bed = _obtain_bed(client, state, anchor)
    if bed is None:
        return None
    for foot in _bed_candidates(state, anchor):
        layout = _bed_layout(client, foot)
        if layout is None:
            continue
        direction, stand = layout
        if _place_bed_at(client, bed, foot, direction, stand):
            return tuple(foot)
        print(f"HOME RESPAWN: bed did not stay at {foot}; trying another site")
    print("HOME RESPAWN: no site near home accepted the bed")
    return None


def _chat_cursor(client: Any) -> Optional[int]:
    try:
        data = client.transport.dispatch("get_events", {"after_seq": 0, "type": "chat", "limit": 1})
        return int(data.get("latest_seq"))
    except Exception:
        return None


def _spawn_set_confirmed(client: Any, cursor: Optional[int], timeout: float = 4.0) -> bool:
    """Read (never drain) chat after ``cursor`` for the respawn confirmation."""
    if cursor is None:
        return False
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        time.sleep(0.5)
        try:
            data = client.transport.dispatch(
                "get_events", {"after_seq": cursor, "type": "chat", "limit": 50}
            )
        except Exception:
            continue
        for event in data.get("events", []):
            message = str((event.get("data") or {}).get("message", "")).lower()
            if any(marker in message for marker in _SPAWN_SET_MESSAGES):
                return True
    return False


def _use_bed(client: Any, bed) -> bool:
    from . import harness_ops

    harness_ops.move_near(client, *bed, timeout=20.0)
    cursor = _chat_cursor(client)
    client.transport.dispatch("look_at", {"x": bed[0] + 0.5, "y": bed[1] + 0.3, "z": bed[2] + 0.5})
    time.sleep(0.2)
    try:
        client.transport.dispatch("interact_block", {"x": bed[0], "y": bed[1], "z": bed[2]})
    except Exception as exc:
        print(f"HOME RESPAWN: bed at {bed} refused interaction ({exc})")
        return False
    confirmed = _spawn_set_confirmed(client, cursor)
    try:
        client.transport.dispatch("close_screen", {})  # leave the bed if asleep
    except Exception:
        pass
    return confirmed


@allow_recovery_navigation
def secure_home_respawn(client: Any, state: Any, *, now: Optional[float] = None) -> bool:
    """Ensure a verified home bed is the respawn point; True once it is."""
    from .food_supply import _base_anchor
    from .navigation import find_nearby_block

    custom = _custom(state)
    anchor = _base_anchor(state)
    if anchor is None:
        return False
    record = custom.get(HOME_RESPAWN_KEY)
    recorded_bed = _xyz(record.get("bed")) if isinstance(record, dict) else None

    current = time.monotonic() if now is None else float(now)
    last = getattr(client, "_home_respawn_last", None)
    if isinstance(last, (int, float)) and current - last < RETRY_INTERVAL:
        return recorded_bed is not None
    client._home_respawn_last = current

    live = client.transport.dispatch("get_state", {})
    here = _position(live) if isinstance(live, Mapping) else None
    if here is None or _flat(here, anchor) > HOME_RADIUS:
        return recorded_bed is not None
    if recorded_bed is not None:
        if _is_bed(client, recorded_bed):
            return True
        print(f"HOME RESPAWN: recorded bed at {recorded_bed} is gone; replacing it")
        custom.pop(HOME_RESPAWN_KEY, None)
    if bool(live.get("is_dead")) or "overworld" not in str(live.get("dimension", "overworld")):
        return False
    if _hostile_close(client, live):
        return False

    bed = find_nearby_block(client, list(BED_ITEMS), radius=16)
    # A search hit is not proof: re-read the block before relying on it.
    if bed is not None and (_flat(bed, anchor) > HOME_RADIUS or not _is_bed(client, bed)):
        bed = None
    if bed is None:
        bed = _place_home_bed(client, state, anchor)
    if bed is None:
        return False
    if not _use_bed(client, bed):
        print(f"HOME RESPAWN: used bed at {tuple(bed)} but the game did not confirm a respawn point")
        return False
    custom[HOME_RESPAWN_KEY] = {"bed": list(bed), "verified_at": time.time(), "evidence": "chat"}
    structures = custom.setdefault("structures", {})
    if isinstance(structures, dict) and isinstance(structures.get("starter_house"), dict):
        structures["starter_house"]["bed"] = list(bed)
    print(f"HOME RESPAWN: respawn point set at home bed {tuple(bed)}")
    return True


__all__ = ["HOME_RESPAWN_KEY", "secure_home_respawn", "withdraw_from_home_containers"]
