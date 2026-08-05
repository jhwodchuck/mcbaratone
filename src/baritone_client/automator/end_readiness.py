"""Fleet focus and durable evidence for the next End expedition.

The objective graph models one bot's dependencies.  A fleet needs one more
layer: stable roles so every controller does not rotate through the same
post-iron objectives, plus a small material scoreboard that distinguishes
movement from progress toward the End.
"""

from __future__ import annotations

import os
import re
import time
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .state_manager import Phase


class FleetRole(str, Enum):
    """Stable post-iron work assignments for a four-bot fleet."""

    BALANCED = "balanced"
    END_RUNNER = "end_runner"
    NETHER_SUPPLY = "nether_supply"
    ENCHANTING = "enchanting"
    VILLAGE_FOOD = "village_food"


_ROLE_BY_REMAINDER = {
    3: FleetRole.END_RUNNER,
    0: FleetRole.NETHER_SUPPLY,
    1: FleetRole.ENCHANTING,
    2: FleetRole.VILLAGE_FOOD,
}

_ROLE_FOCUS = {
    FleetRole.END_RUNNER: (
        Phase.NETHER_AND_BLAZE,
        Phase.WORLD_UNLOCK,
    ),
    FleetRole.NETHER_SUPPLY: (Phase.NETHER_AND_BLAZE,),
    FleetRole.ENCHANTING: (Phase.ENCHANTING_PIPELINE,),
    FleetRole.VILLAGE_FOOD: (Phase.VILLAGER_INFRA,),
}

PREPARED_FOOD_ITEMS = (
    "minecraft:cooked_beef",
    "minecraft:cooked_porkchop",
    "minecraft:cooked_chicken",
    "minecraft:cooked_mutton",
    "minecraft:cooked_rabbit",
    "minecraft:baked_potato",
    "minecraft:bread",
    "minecraft:golden_carrot",
    "minecraft:golden_apple",
)


def _count(inventory: Mapping[str, Any], item_id: str) -> int:
    try:
        return max(0, int(inventory.get(item_id, 0) or 0))
    except (TypeError, ValueError):
        return 0


def bot_name(state: Any) -> str:
    """Resolve BotNN from its isolated checkpoint directory or environment."""
    checkpoint_dir = getattr(state, "checkpoint_dir", None)
    if checkpoint_dir:
        path = Path(checkpoint_dir)
        candidate = (
            path.parent.name if path.name.lower() == "controller" else path.name
        )
        if re.fullmatch(r"Bot\d{2}", candidate, re.IGNORECASE):
            return f"Bot{int(candidate[-2:]):02d}"
    candidate = str(os.environ.get("MC_BOT_NAME", ""))
    if re.fullmatch(r"Bot\d{2}", candidate, re.IGNORECASE):
        return f"Bot{int(candidate[-2:]):02d}"
    return ""


def fleet_role(state: Any) -> FleetRole:
    """Return an explicit role, or a deterministic BotNN sharding fallback."""
    explicit = str(os.environ.get("MC_FLEET_ROLE", "")).strip().lower()
    try:
        if explicit:
            return FleetRole(explicit)
    except ValueError:
        pass
    name = bot_name(state)
    if not name:
        return FleetRole.BALANCED
    return _ROLE_BY_REMAINDER[int(name[-2:]) % 4]


def role_focused_candidates(
    candidates: Sequence[Any],
    objectives: Iterable[Any],
    completed: Iterable[Phase],
    role: FleetRole,
) -> tuple[list[Any], str]:
    """Keep a post-iron bot on its assigned objective until it is verified.

    Returning an empty list is deliberate when the focused objective is
    abandoned.  The automator's bounded re-arm path then reopens it instead of
    silently selecting a different sibling and restarting the old phase churn.
    """
    completed_set = set(completed)
    available = list(candidates)
    if Phase.FOOD_AND_IRON not in completed_set or role is FleetRole.BALANCED:
        return available, ""

    by_phase = {objective.phase: objective for objective in objectives}
    for phase in _ROLE_FOCUS.get(role, ()):
        if phase in completed_set:
            continue
        if phase not in by_phase:
            continue
        focused = [candidate for candidate in available if candidate.phase is phase]
        return focused, f"fleet role {role.value} is locked to {phase.name}"
    return available, ""


def allows_local_work(role: FleetRole) -> bool:
    """Resource roles may farm locally; critical-path roles stay on mission."""
    return role in {
        FleetRole.BALANCED,
        FleetRole.ENCHANTING,
        FleetRole.VILLAGE_FOOD,
    }


def readiness_snapshot(
    inventory: Mapping[str, Any],
    *,
    role: FleetRole,
    completed: Iterable[Phase] = (),
    known_portal: bool = False,
) -> dict[str, Any]:
    """Build an explainable current-inventory End-readiness snapshot."""
    supplies = {
        "blaze_rods": _count(inventory, "minecraft:blaze_rod"),
        "blaze_powder": _count(inventory, "minecraft:blaze_powder"),
        "ender_pearls": _count(inventory, "minecraft:ender_pearl"),
        "eyes": _count(inventory, "minecraft:ender_eye"),
        "obsidian": _count(inventory, "minecraft:obsidian"),
        "iron": _count(inventory, "minecraft:iron_ingot"),
        "diamonds": _count(inventory, "minecraft:diamond"),
        "prepared_food": sum(_count(inventory, item) for item in PREPARED_FOOD_ITEMS),
    }
    completed_names = sorted(phase.name for phase in set(completed))
    craftable_eyes = min(
        supplies["ender_pearls"],
        supplies["blaze_powder"] + supplies["blaze_rods"] * 2,
    )
    eye_path_total = supplies["eyes"] + craftable_eyes

    armor_ready = all(
        any(
            _count(inventory, f"minecraft:{material}_{piece}") >= 1
            for material in ("iron", "diamond", "netherite")
        )
        for piece in ("helmet", "chestplate", "leggings", "boots")
    )
    sword_ready = any(
        _count(inventory, f"minecraft:{material}_sword") >= 1
        for material in ("iron", "diamond", "netherite")
    )
    pickaxe_ready = any(
        _count(inventory, f"minecraft:{material}_pickaxe") >= 1
        for material in ("iron", "diamond", "netherite")
    )
    combat_loadout = {
        "full_iron_or_better": armor_ready,
        "shield": _count(inventory, "minecraft:shield") >= 1,
        "sword": sword_ready,
        "pickaxe": pickaxe_ready,
        "bow": _count(inventory, "minecraft:bow") >= 1,
        "arrows_32": _count(inventory, "minecraft:arrow") >= 32,
        "water_bucket": _count(inventory, "minecraft:water_bucket") >= 1,
        "prepared_food_32": supplies["prepared_food"] >= 32,
    }
    portal_ready = bool(known_portal or supplies["obsidian"] >= 10)
    launch_ready = bool(
        eye_path_total >= 12
        and portal_ready
        and all(combat_loadout.values())
    )

    missing = []
    if supplies["blaze_rods"] < 6 and supplies["blaze_powder"] < 12:
        missing.append("6 blaze rods")
    if eye_path_total < 12:
        missing.append(f"{12 - eye_path_total} pearl/Eye components")
    if not portal_ready:
        missing.append("verified portal or 10 obsidian")
    missing.extend(name for name, ready in combat_loadout.items() if not ready)

    category_score = sum(
        (
            supplies["blaze_rods"] >= 6 or supplies["blaze_powder"] >= 12,
            eye_path_total >= 12,
            portal_ready,
            supplies["iron"] >= 24,
            supplies["prepared_food"] >= 64,
            armor_ready and combat_loadout["shield"],
            all(combat_loadout.values()),
        )
    )
    return {
        "version": 1,
        "role": role.value,
        "supplies": supplies,
        "craftable_eye_total": eye_path_total,
        "known_portal": bool(known_portal),
        "combat_loadout": combat_loadout,
        "launch_ready": launch_ready,
        "score": int(category_score),
        "score_max": 7,
        "missing": missing,
        "verified_objectives": completed_names,
        "verified_objective_count": len(completed_names),
    }


def record_readiness(
    state: Any,
    inventory: Mapping[str, Any],
    *,
    completed: Iterable[Phase],
    known_portal: bool,
    now: float | None = None,
) -> dict[str, Any]:
    """Persist current totals, a rollout baseline, and net change."""
    observed_at = time.time() if now is None else float(now)
    snapshot = readiness_snapshot(
        inventory,
        role=fleet_role(state),
        completed=completed,
        known_portal=known_portal,
    )
    custom = getattr(state, "custom_data", None)
    if not isinstance(custom, dict):
        custom = {}
        state.custom_data = custom
    previous = custom.get("end_readiness", {})
    previous = previous if isinstance(previous, Mapping) else {}
    baseline = previous.get("baseline")
    if not isinstance(baseline, Mapping):
        baseline = {
            "recorded_at": observed_at,
            "supplies": dict(snapshot["supplies"]),
            "verified_objective_count": snapshot["verified_objective_count"],
        }
    baseline_supplies = baseline.get("supplies", {})
    baseline_supplies = baseline_supplies if isinstance(baseline_supplies, Mapping) else {}
    snapshot["baseline"] = dict(baseline)
    snapshot["net_since_baseline"] = {
        item: count - _count(baseline_supplies, item)
        for item, count in snapshot["supplies"].items()
    }
    try:
        baseline_objectives = int(baseline.get("verified_objective_count", 0) or 0)
    except (TypeError, ValueError):
        baseline_objectives = 0
    snapshot["net_verified_objectives"] = (
        snapshot["verified_objective_count"] - baseline_objectives
    )
    try:
        previous_score = int(previous.get("score", -1))
    except (TypeError, ValueError):
        previous_score = -1
    try:
        previous_objectives = int(previous.get("verified_objective_count", 0))
    except (TypeError, ValueError):
        previous_objectives = 0
    last_progress_at = previous.get("last_progress_at", observed_at)
    if (
        snapshot["score"] > previous_score
        or snapshot["verified_objective_count"] > previous_objectives
    ):
        last_progress_at = observed_at
    snapshot["last_progress_at"] = float(last_progress_at)
    snapshot["updated_at"] = observed_at
    custom["end_readiness"] = snapshot
    return snapshot


__all__ = [
    "FleetRole",
    "PREPARED_FOOD_ITEMS",
    "allows_local_work",
    "bot_name",
    "fleet_role",
    "readiness_snapshot",
    "record_readiness",
    "role_focused_candidates",
]
