"""Shared charged-ranged combat actions with strict loadout checks."""

from __future__ import annotations

import time
from typing import Any

from . import combat_telemetry
from .combat_action import (
    dispatch_held_item_use,
    exclusive_combat_action,
    held_item_quarantine_seconds,
)
from .combat_loadout import remaining_durability, select_exact_item
from .combat_targeting import normalize_mob_type
from .tasks import PlayerDeathDetected

BOW_CHARGE_MS = 1100
_ARROWS = {
    "minecraft:arrow",
    "minecraft:spectral_arrow",
    "minecraft:tipped_arrow",
}
_THROWABLES = ("minecraft:snowball", "minecraft:egg")
_AIM_HEIGHT = {
    "ender_crystal": 1.0,
    "ender_dragon": 2.0,
    "enderman": 1.5,
    "wither": 1.5,
}
_RANGED_READY_AT = "_mcbaratone_ranged_ready_at"


def ranged_attack_in_flight(client: Any) -> bool:
    """Return whether the bridge is still holding a prior ranged use."""
    return time.monotonic() < float(getattr(client, _RANGED_READY_AT, 0.0) or 0.0)


def _inventory_entries(client: Any) -> list[dict]:
    response = client.transport.dispatch("get_inventory", {})
    if not isinstance(response, dict) or response.get("error"):
        return []
    data = response.get("data", response)
    if not isinstance(data, dict):
        return []
    return [
        entry
        for entry in data.get("inventory", [])
        if isinstance(entry, dict) and int(entry.get("count", 0) or 0) > 0
    ]


def _find_ranged_item(
    entries: list[dict], *, allow_throwables: bool
) -> tuple[dict, int] | None:
    bows = [entry for entry in entries if entry.get("id") == "minecraft:bow"]
    usable_bows = [
        entry
        for entry in bows
        if remaining_durability(entry) is None or remaining_durability(entry) > 3
    ]
    usable_bow = max(
        usable_bows,
        key=lambda entry: (
            float("inf")
            if remaining_durability(entry) is None
            else remaining_durability(entry),
            -int(entry.get("slot", -1)),
        ),
        default=None,
    )
    arrow_count = sum(
        int(entry.get("count", 0) or 0)
        for entry in entries
        if entry.get("id") in _ARROWS
    )
    if usable_bow is not None and arrow_count > 0:
        return usable_bow, BOW_CHARGE_MS
    if allow_throwables:
        for item_id in _THROWABLES:
            selected = next(
                (entry for entry in entries if entry.get("id") == item_id), None
            )
            if selected is not None:
                return selected, 0
    return None


def ranged_loadout_ready(client: Any, *, minimum_arrows: int = 1) -> bool:
    """Verify a durable bow and arrow reserve from one fresh inventory read."""
    try:
        entries = _inventory_entries(client)
        bow = next(
            (
                entry
                for entry in entries
                if entry.get("id") == "minecraft:bow"
                and (
                    remaining_durability(entry) is None
                    or remaining_durability(entry) > 3
                )
            ),
            None,
        )
        arrows = sum(
            int(entry.get("count", 0) or 0)
            for entry in entries
            if entry.get("id") in _ARROWS
        )
        return bow is not None and arrows >= max(1, int(minimum_arrows))
    except Exception:
        return False


def _aim_point(target: dict) -> tuple[float, float, float] | None:
    position = target.get("position")
    if not isinstance(position, dict) or not all(
        axis in position for axis in ("x", "y", "z")
    ):
        return None
    velocity = (
        target.get("velocity") if isinstance(target.get("velocity"), dict) else {}
    )
    try:
        distance = max(0.0, float(target.get("distance", 0) or 0))
        # Bridge velocity is Minecraft delta movement in blocks/tick, while
        # projectile flight is estimated in seconds.
        lead_ticks = min(25.0, distance / 35.0 * 20.0)
        target_type = normalize_mob_type(target.get("type"))
        return (
            float(position["x"]) + float(velocity.get("x", 0) or 0) * lead_ticks,
            float(position["y"])
            + _AIM_HEIGHT.get(target_type, 1.0)
            + float(velocity.get("y", 0) or 0) * lead_ticks,
            float(position["z"]) + float(velocity.get("z", 0) or 0) * lead_ticks,
        )
    except (TypeError, ValueError):
        return None


def _boss_attack_authorized(client: Any, target: dict, state: dict) -> bool:
    """Require explicit, exact intent before a ranged boss attack."""
    from .combat_intent import current_combat_intent
    from .defense import AttackStyle, assess_threats

    assessments = assess_threats([target], state)
    if not assessments or assessments[0].style != AttackStyle.BOSS:
        return True
    intent = current_combat_intent(client)
    return bool(
        intent is not None
        and intent.allow_boss
        and intent.authorizes(target)
    )


def fire_best_ranged_attack(
    client: Any,
    target: dict,
    *,
    allow_throwables: bool = False,
) -> bool:
    """Aim, charge, and release the best verified carried ranged option."""
    target_id = target.get("id")
    aim = _aim_point(target)
    if target_id is None or aim is None:
        return False
    if ranged_attack_in_flight(client):
        return False
    try:
        with exclusive_combat_action(client, blocking=False) as acquired:
            if not acquired:
                return False
            return _fire_owned_ranged_attack(
                client, target, aim, allow_throwables=allow_throwables
            )
    except PlayerDeathDetected:
        raise
    except Exception as exc:
        combat_telemetry.record_combat_action(
            client,
            "ranged_attack",
            outcome="error",
            target=target,
            error=str(exc),
        )
        return False


def _fire_owned_ranged_attack(
    client: Any,
    target: dict,
    aim: tuple[float, float, float],
    *,
    allow_throwables: bool,
) -> bool:
    """Dispatch one ranged action while the caller owns item use."""
    try:
        from .combat import ensure_alive

        state = client.transport.dispatch("get_state", {})
        ensure_alive(client, state)
        if not _boss_attack_authorized(client, target, state):
            combat_telemetry.record_combat_action(
                client,
                "ranged_attack",
                outcome="boss_not_authorized",
                target=target,
            )
            return False
        loadout = _find_ranged_item(
            _inventory_entries(client), allow_throwables=allow_throwables
        )
        if loadout is None:
            return False
        item, duration_ms = loadout
        item_id = str(item.get("id", ""))
        if not select_exact_item(client, item):
            return False
        client.transport.dispatch("look_at", {"x": aim[0], "y": aim[1], "z": aim[2]})
        # Mark the action in flight before dispatch. A timeout or declined
        # response is ambiguous because the bridge may already have scheduled
        # its key release; the shared helper keeps ownership through that
        # entire window in every outcome.
        hold_seconds = held_item_quarantine_seconds(duration_ms)
        setattr(
            client,
            _RANGED_READY_AT,
            time.monotonic() + hold_seconds,
        )
        result = dispatch_held_item_use(client, duration_ms)
        data = result.get("data", result) if isinstance(result, dict) else {}
        declined = (
            not isinstance(data, dict)
            or bool(result.get("error"))
            or data.get("held_item") != item_id
            or (duration_ms > 0 and data.get("holding") is not True)
            or (duration_ms == 0 and data.get("used") is not True)
        )
        if not declined:
            ensure_alive(client, client.transport.dispatch("get_state", {}))
        combat_telemetry.record_combat_action(
            client,
            "ranged_attack",
            outcome="declined" if declined else "dispatched",
            target=target,
            weapon=item_id,
            charge_ms=duration_ms,
            bridge_result=result,
        )
        return not declined
    except PlayerDeathDetected:
        raise
    except Exception:
        raise


__all__ = [
    "BOW_CHARGE_MS",
    "fire_best_ranged_attack",
    "ranged_loadout_ready",
    "ranged_attack_in_flight",
]
