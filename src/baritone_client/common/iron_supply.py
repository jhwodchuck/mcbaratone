"""Bounded production mining for a dedicated fleet iron supplier.

The functional and unit suites already prove the useful pieces independently:
bounded ore gathering, stall recovery, furnace smelting, and verified storage.
This module composes those primitives into one repeatable production cycle
without running an unbounded Baritone mine process against the fleet world.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .inventory import (
    deposit_excess_to_chest,
    get_inventory,
    resolve_storage_location,
)
from .resources import _smelt_with_furnace, gather_ores


RAW_IRON = "minecraft:raw_iron"
IRON_INGOT = "minecraft:iron_ingot"


@dataclass(frozen=True)
class IronCycleResult:
    """Verified outcomes from one mine, smelt, and storage pass."""

    success: bool
    detail: str
    raw_iron_mined: int = 0
    iron_ingots_smelted: int = 0
    raw_iron_banked: int = 0
    iron_ingots_banked: int = 0
    iron_banked: int = 0
    total_iron_banked: int = 0


def _unwrap(response: Any) -> Mapping[str, Any]:
    if not isinstance(response, Mapping):
        return {}
    data = response.get("data", response)
    return data if isinstance(data, Mapping) else {}


def _count(inventory: Mapping[str, Any], item_id: str) -> int:
    try:
        return max(0, int(inventory.get(item_id, 0) or 0))
    except (TypeError, ValueError):
        return 0


def run_iron_cycle(
    client: Any,
    state: Any,
    *,
    mining_target: int = 24,
) -> IronCycleResult:
    """Mine, optionally smelt, and bank one bounded batch of iron.

    Success requires both newly mined raw iron and a verified storage transfer.
    Smelting is best effort: when furnace or fuel preparation cannot be proven,
    the raw iron is still useful team supply and is banked instead.
    """

    snapshot = _unwrap(client.transport.dispatch("get_state", {}))
    if (
        snapshot.get("is_dead")
        or "overworld" not in str(snapshot.get("dimension", ""))
        or float(snapshot.get("health", 0) or 0) < 16.0
        or int(snapshot.get("food_level", snapshot.get("food", 0)) or 0) < 14
    ):
        return IronCycleResult(False, "survival margin is not ready for mining")

    before = get_inventory(client)
    before_raw = _count(before, RAW_IRON)
    target = before_raw + max(1, int(mining_target))
    gathered = gather_ores(client, "iron", count=target, timeout=600)
    after_mining = get_inventory(client)
    raw_mined = max(0, _count(after_mining, RAW_IRON) - before_raw)
    if not gathered and raw_mined <= 0:
        return IronCycleResult(
            False,
            "no raw iron was mined before the bounded gather stopped",
        )

    ingots_before_smelt = _count(after_mining, IRON_INGOT)
    smelt_target = ingots_before_smelt + raw_mined
    if raw_mined > 0:
        _smelt_with_furnace(client, IRON_INGOT, smelt_target)
    after_processing = get_inventory(client)
    ingots_smelted = min(
        raw_mined,
        max(0, _count(after_processing, IRON_INGOT) - ingots_before_smelt),
    )

    chest = resolve_storage_location(client, state=state, verify=False)
    if chest is None:
        return IronCycleResult(
            False,
            "mined iron but no checkpointed storage was available",
            raw_iron_mined=raw_mined,
            iron_ingots_smelted=ingots_smelted,
        )
    deposited = deposit_excess_to_chest(
        client,
        chest,
        deposit_items={RAW_IRON, IRON_INGOT},
        state=state,
    )
    if deposited < 0:
        return IronCycleResult(
            False,
            "mined iron but the storage deposit was not verified",
            raw_iron_mined=raw_mined,
            iron_ingots_smelted=ingots_smelted,
        )

    after_storage = get_inventory(client)
    raw_banked = max(
        0,
        _count(after_processing, RAW_IRON) - _count(after_storage, RAW_IRON),
    )
    ingots_banked = max(
        0,
        _count(after_processing, IRON_INGOT)
        - _count(after_storage, IRON_INGOT),
    )
    # A first run may also move iron carried before this cycle. Credit only
    # newly mined units so telemetry cannot manufacture production progress.
    iron_banked = min(raw_mined, raw_banked + ingots_banked)

    custom = getattr(state, "custom_data", None)
    if not isinstance(custom, dict):
        custom = {}
        state.custom_data = custom
    stats = custom.setdefault("iron_worker", {})
    if not isinstance(stats, dict):
        stats = {}
        custom["iron_worker"] = stats
    stats["cycles"] = int(stats.get("cycles", 0) or 0) + 1
    stats["raw_iron_mined"] = (
        int(stats.get("raw_iron_mined", 0) or 0) + raw_mined
    )
    stats["iron_ingots_smelted"] = (
        int(stats.get("iron_ingots_smelted", 0) or 0) + ingots_smelted
    )
    stats["iron_banked"] = int(stats.get("iron_banked", 0) or 0) + iron_banked
    detail = (
        f"mined {raw_mined} raw iron, smelted {ingots_smelted} ingots, "
        f"banked {raw_banked} raw and {ingots_banked} ingots "
        f"({iron_banked} newly mined units credited)"
    )
    return IronCycleResult(
        bool(raw_mined > 0 and iron_banked > 0),
        detail,
        raw_iron_mined=raw_mined,
        iron_ingots_smelted=ingots_smelted,
        raw_iron_banked=raw_banked,
        iron_ingots_banked=ingots_banked,
        iron_banked=iron_banked,
        total_iron_banked=int(stats["iron_banked"]),
    )


__all__ = ["IRON_INGOT", "RAW_IRON", "IronCycleResult", "run_iron_cycle"]
