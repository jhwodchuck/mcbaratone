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

HOME_RESPAWN_KEY = "home_respawn"
#: Only work on the respawn point while actually at home.
HOME_RADIUS = 32.0
#: Storage trips for bed materials stay inside the base.
HOME_STORAGE_RADIUS = 48.0
#: The survival loop calls this every few seconds.
RETRY_INTERVAL = 300.0
BED_COLORS = (
    "white", "orange", "magenta", "light_blue", "yellow", "lime", "pink",
    "gray", "light_gray", "cyan", "purple", "blue", "brown", "green", "red",
    "black",
)
BED_ITEMS = tuple(f"minecraft:{color}_bed" for color in BED_COLORS)
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
    """Interior slots of the starter house, then the base anchor's surroundings."""
    origin = _xyz(_house(state).get("origin")) or anchor
    x, y, z = origin
    return [
        (x + 4, y + 1, z + 3),
        (x + 4, y + 1, z + 4),
        (x + 3, y + 1, z + 4),
        (x + 2, y + 1, z + 2),
    ]


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


def _obtain_bed(client: Any, state: Any) -> Optional[str]:
    """Carry a bed, crafting it from home wool or string when needed."""
    from . import harness_ops
    from .inventory import count_item, withdraw_required_from_catalog

    bed = _carried_bed(client)
    if bed:
        return bed
    home = {"state": state, "max_travel_distance": HOME_STORAGE_RADIUS}
    withdraw_required_from_catalog(client, {_WHITE_BED: 1, _WHITE_WOOL: 3}, **home)
    bed = _carried_bed(client)
    if bed:
        return bed
    missing_wool = max(0, 3 - count_item(client, _WHITE_WOOL))
    if missing_wool:
        withdraw_required_from_catalog(client, {_STRING: 4 * missing_wool}, **home)
        crafts = min(missing_wool, count_item(client, _STRING) // 4)
        if crafts <= 0 or not harness_ops.ensure_crafting_table_open(client):
            print(f"HOME RESPAWN: need {missing_wool} more white wool or {4 * missing_wool} string")
            return None
        harness_ops.craft_recipe_manual(
            client,
            _WHITE_WOOL,
            [(_STRING, 1), (_STRING, 2), (_STRING, 4), (_STRING, 5)],
            crafts=crafts,
        )
        client.transport.dispatch("close_screen", {})
        if count_item(client, _WHITE_WOOL) < 3:
            print("HOME RESPAWN: crafting wool from string did not produce 3 wool")
            return None
    if harness_ops.count_any_planks(client) < 3:
        print("HOME RESPAWN: need 3 planks for a bed")
        return None
    if not harness_ops.ensure_crafting_table_open(client):
        return None
    crafted = harness_ops.craft_bed_manual(client, _WHITE_BED)
    client.transport.dispatch("close_screen", {})
    return _WHITE_BED if crafted and count_item(client, _WHITE_BED) > 0 else None


def _place_home_bed(client: Any, state: Any, anchor) -> Optional[Tuple[int, int, int]]:
    from . import harness_ops
    from .navigation import find_nearby_block

    bed = _obtain_bed(client, state)
    if bed is None:
        return None
    for slot in _bed_slots(state, anchor):
        if harness_ops.place_block_exact(client, *slot, bed, allow_break=False):
            placed = find_nearby_block(client, list(BED_ITEMS), radius=6)
            if placed is not None and _flat(placed, anchor) <= HOME_RADIUS:
                return tuple(placed)
    print("HOME RESPAWN: no starter-house slot accepted the bed")
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
    if bed is not None and _flat(bed, anchor) > HOME_RADIUS:
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


__all__ = ["HOME_RESPAWN_KEY", "secure_home_respawn"]
