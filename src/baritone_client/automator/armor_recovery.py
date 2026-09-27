"""Get armour back when the bot is not carrying the pieces or the iron.

`armor_upkeep` wears carried armour and crafts pieces from carried ingots,
but a bot that carries neither had no way forward: it logged "armour
unchanged at 0/4 with 3 iron carried" while spare iron boots and helmets sat
in chests beside its house, its raw iron stayed raw, and nothing ever went
to get iron. Live A1 on 2026-09-27 fought skeletons and zombies at base in
no armour at all and died over and over.

Recovery, cheapest first:

1. Missing pieces from storage near home (diamond, then iron, then
   chainmail), within reach of the player's level, then wear them.
2. Iron ingots or raw iron from the same storage for pieces still missing.
3. Carried raw iron smelted at the home furnace.
4. Digging exposed iron ore near the player's own level (never a shaft
   into caves), only once some armour is worn and health and food are
   high, on a persisted cooldown so a death cannot turn it into a loop.
   The ingots are kept for armour, not banked.

Each stage is on a persisted cooldown, and the whole step counts as
recovery movement so defense mode does not cancel its short walks.
"""

from __future__ import annotations

import time
from typing import Any, Mapping, Optional

from ..common.navigation import allow_recovery_navigation

SLOTS = ("helmet", "chestplate", "leggings", "boots")
#: Wearable replacements per slot, best first.
SLOT_ITEMS = {
    slot: tuple(f"minecraft:{tier}_{slot}" for tier in ("diamond", "iron", "chainmail"))
    for slot in SLOTS
}
#: Iron cost of the iron piece for each slot.
IRON_COST = {"boots": 4, "helmet": 5, "leggings": 7, "chestplate": 8}
IRON_INGOT = "minecraft:iron_ingot"
RAW_IRON = "minecraft:raw_iron"

RECOVERY_KEY = "armour_recovery"
#: Storage near home: a walk, never a cave expedition.
STORAGE_RADIUS = 96.0
STORAGE_MAX_VERTICAL = 16.0
STORAGE_INTERVAL = 600.0
#: Mining is the risky stage: only partly armoured, healthy, fed and rarely.
MINING_INTERVAL = 1200.0
MINING_MIN_WORN = 2
MINING_MIN_HEALTH = 16.0
MINING_MIN_FOOD = 14
MINING_TIMEOUT = 300
#: Only exposed ore near the player's level: never a shaft into caves.
MINING_RADIUS = 32
MINING_MAX_VERTICAL = 10
MINING_MAX_CHECKS = 24


def _record(state: Any) -> dict:
    custom = getattr(state, "custom_data", None)
    if not isinstance(custom, dict):
        return {}
    record = custom.get(RECOVERY_KEY)
    if not isinstance(record, dict):
        record = {}
        custom[RECOVERY_KEY] = record
    return record


def _ready(state: Any, key: str, interval: float, now: float) -> bool:
    try:
        last = float(_record(state).get(key, 0) or 0)
    except (TypeError, ValueError):
        last = 0.0
    return now - last >= interval


def _count(client: Any, item: str) -> int:
    from ..common.inventory import count_item

    try:
        return max(0, int(count_item(client, item) or 0))
    except Exception:
        return 0


def empty_slots(client: Any) -> list[str]:
    """Armour slots with nothing equipped."""
    from ..common.inventory import get_equipped_armor

    try:
        worn = get_equipped_armor(client)
    except Exception:
        return []
    return [slot for slot in SLOTS if not worn.get(slot)]


def iron_still_needed(client: Any) -> int:
    """Ingots needed for iron pieces covering empty, uncarried slots."""
    need = 0
    for slot in empty_slots(client):
        if any(_count(client, item) > 0 for item in SLOT_ITEMS[slot]):
            continue
        need += IRON_COST[slot]
    return max(0, need - _count(client, IRON_INGOT))


def _home(state: Any):
    from ..common.food_supply import _base_anchor

    return _base_anchor(state)


def _live(client: Any) -> Mapping[str, Any]:
    try:
        live = client.transport.dispatch("get_state", {})
    except Exception:
        return {}
    return live if isinstance(live, Mapping) else {}


def _storage_holds(client: Any, state: Any, items) -> bool:
    """Whether cataloged storage near home last held any of ``items``."""
    from math import hypot

    from ..common.storage_catalog import catalog_for

    anchor = _home(state)
    if anchor is None:
        return False
    try:
        catalog = catalog_for(client, state)
    except Exception:
        return False
    for item in items:
        try:
            rows = catalog.find_item(item)
        except Exception:
            continue
        for row in rows:
            if (
                "overworld" in str(row.get("dimension", "overworld"))
                and hypot(row["x"] - anchor[0], row["z"] - anchor[2]) <= STORAGE_RADIUS
                and abs(row["y"] - anchor[1]) <= STORAGE_MAX_VERTICAL
            ):
                return True
    return False


def recovery_ready(client: Any, state: Any, *, now: Optional[float] = None) -> bool:
    """True when a recovery stage has something to try right now."""
    current = time.time() if now is None else float(now)
    slots = empty_slots(client)
    if not slots:
        return False
    if _count(client, RAW_IRON) > 0:
        return True
    if _ready(state, "storage", STORAGE_INTERVAL, current):
        wanted = [item for slot in slots for item in SLOT_ITEMS[slot]]
        if iron_still_needed(client):
            wanted += [IRON_INGOT, RAW_IRON]
        if _storage_holds(client, state, wanted):
            return True
    return _mining_allowed(client, state, current)


def _mining_allowed(client: Any, state: Any, now: float) -> bool:
    if not iron_still_needed(client):
        return False
    if not _ready(state, "mining", MINING_INTERVAL, now):
        return False
    if len(SLOTS) - len(empty_slots(client)) < MINING_MIN_WORN:
        return False
    live = _live(client)
    try:
        return (
            not bool(live.get("is_dead"))
            and "overworld" in str(live.get("dimension", "overworld"))
            and float(live.get("health", 0) or 0) >= MINING_MIN_HEALTH
            and int(live.get("food_level", live.get("food", 0)) or 0) >= MINING_MIN_FOOD
        )
    except (TypeError, ValueError):
        return False


def _withdraw(client: Any, state: Any, anchor, item: str, wanted: int) -> None:
    from ..common.home_respawn import withdraw_from_home_containers

    withdraw_from_home_containers(
        client, state, item, wanted,
        origin=anchor, radius=STORAGE_RADIUS,
        max_vertical=STORAGE_MAX_VERTICAL, recovery=True,
    )


def _recover_from_storage(client: Any, state: Any) -> list[str]:
    from ..common.inventory import equip_best_armor

    anchor = _home(state)
    if anchor is None:
        return []
    fetched = []
    for slot in empty_slots(client):
        for item in SLOT_ITEMS[slot]:
            if _count(client, item) > 0:
                break
            _withdraw(client, state, anchor, item, 1)
            if _count(client, item) > 0:
                fetched.append(item.split(":")[1])
                break
    try:
        equip_best_armor(client)
    except Exception:
        pass
    need = iron_still_needed(client)
    if need:
        _withdraw(client, state, anchor, IRON_INGOT, _count(client, IRON_INGOT) + need)
        need = iron_still_needed(client)
        if need:
            _withdraw(client, state, anchor, RAW_IRON, _count(client, RAW_IRON) + need)
    return fetched


def _smelt_raw_iron(client: Any, state: Any) -> int:
    from ..common.livestock_food import _home_furnace
    from ..common.resources import _smelt_with_furnace

    raw = _count(client, RAW_IRON)
    if raw <= 0:
        return 0
    before = _count(client, IRON_INGOT)
    try:
        _smelt_with_furnace(client, IRON_INGOT, before + raw, _home_furnace(client, state))
    except Exception as exc:
        print(f"ARMOUR RECOVERY: smelting raw iron failed ({exc})")
    return max(0, _count(client, IRON_INGOT) - before)


_IRON_ORES = ("minecraft:iron_ore", "minecraft:deepslate_iron_ore")
_OPEN = ("minecraft:air", "minecraft:cave_air")


def _block(client: Any, x: int, y: int, z: int) -> str:
    try:
        return str(client.transport.dispatch("get_block", {"x": x, "y": y, "z": z}).get("id", ""))
    except Exception:
        return ""


def exposed_iron_near_level(client: Any) -> list:
    """Iron ore open to the air, near the player's own level, nearest first.

    Baritone's `mine` goes to the nearest ore anywhere, which under A1's base
    meant a shaft to y=31 and a zombie within two minutes. Only ore that is
    exposed (a free neighbour) within MINING_MAX_VERTICAL of the player is
    worth a trip; if there is none, recovery does not mine at all.
    """
    live = _live(client)
    position = live.get("block_position", live.get("position", {})) or {}
    try:
        px, py, pz = (int(float(position[axis])) for axis in ("x", "y", "z"))
    except (KeyError, TypeError, ValueError):
        return []
    try:
        found = client.transport.dispatch(
            "find_blocks", {"blocks": list(_IRON_ORES), "radius": MINING_RADIUS, "limit": 256}
        ).get("found", [])
    except Exception:
        return []
    candidates = sorted(
        (
            (int(b["x"]), int(b["y"]), int(b["z"]))
            for b in found
            if abs(int(b["y"]) - py) <= MINING_MAX_VERTICAL
        ),
        key=lambda ore: (ore[0] - px) ** 2 + (ore[1] - py) ** 2 + (ore[2] - pz) ** 2,
    )
    exposed = []
    for x, y, z in candidates[:MINING_MAX_CHECKS]:
        neighbours = ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1))
        if any(_block(client, x + dx, y + dy, z + dz) in _OPEN for dx, dy, dz in neighbours):
            exposed.append((x, y, z))
    return exposed


def _mine_iron(client: Any, state: Any, now: float) -> int:
    from ..common.navigation import goto
    from ..common.resources import equip_best_pickaxe

    need = iron_still_needed(client)
    _record(state)["mining"] = now  # before leaving: a death still waits
    ores = exposed_iron_near_level(client)
    if not ores:
        print("ARMOUR RECOVERY: no exposed iron near the player's level; not mining")
        return 0
    before = _count(client, RAW_IRON)
    print(f"ARMOUR RECOVERY: digging up to {need} exposed iron ore near its level")
    deadline = time.monotonic() + MINING_TIMEOUT
    for ore in ores:
        if _count(client, RAW_IRON) - before >= need or time.monotonic() > deadline:
            break
        goto(client, *ore, timeout=60, tolerance=3.5, radius=2)
        if not equip_best_pickaxe(client):
            print("ARMOUR RECOVERY: no pickaxe that can mine iron")
            break
        client.transport.dispatch("dig_block", {"x": ore[0], "y": ore[1], "z": ore[2], "max_ticks": 200})
        stop = time.monotonic() + 10.0
        while time.monotonic() < stop and _block(client, *ore) in _IRON_ORES:
            time.sleep(0.3)
        # The drop lands on the ore's cell; step onto it to pick it up.
        goto(client, *ore, timeout=10, tolerance=1.0)
    try:
        client.transport.dispatch("cancel", {})
    except Exception:
        pass
    return max(0, _count(client, RAW_IRON) - before)


@allow_recovery_navigation
def recover_armor_materials(client: Any, state: Any, *, now: Optional[float] = None) -> str:
    """Run the recovery stages that are due; return a short summary."""
    from ..common.tasks import PlayerDeathDetected

    current = time.time() if now is None else float(now)
    notes = []
    if not empty_slots(client):
        return ""
    try:
        if _ready(state, "storage", STORAGE_INTERVAL, current):
            _record(state)["storage"] = current
            fetched = _recover_from_storage(client, state)
            if fetched:
                notes.append("fetched " + ", ".join(fetched))
        smelted = _smelt_raw_iron(client, state)
        if smelted:
            notes.append(f"smelted {smelted} iron")
        if _mining_allowed(client, state, current):
            mined = _mine_iron(client, state, current)
            if mined:
                notes.append(f"mined {mined} raw iron")
            smelted = _smelt_raw_iron(client, state)
            if smelted:
                notes.append(f"smelted {smelted} iron")
    except PlayerDeathDetected:
        raise
    except Exception as exc:
        notes.append(f"recovery interrupted ({exc})")
    return "; ".join(notes)


__all__ = [
    "IRON_COST",
    "SLOT_ITEMS",
    "empty_slots",
    "iron_still_needed",
    "recover_armor_materials",
    "recovery_ready",
]
