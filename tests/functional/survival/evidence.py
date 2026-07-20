"""Read-only acceptance gates for a real Survival spawn-to-endgame run.

The legacy functional suites often create arenas with admin commands.  This
module deliberately does the opposite: it observes the live world plus the
production checkpoint and never changes either one.  It is therefore safe to
run while another controller owns Minecraft.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Mapping, Optional, Sequence, Tuple


CHECKPOINT_ENV = "MCBARATONE_CHECKPOINT"
DEFAULT_CHECKPOINT_NAME = "spawn_to_dragon_checkpoint.json"

# Keep this aligned with ``baritone_client.automator.state_manager.Phase``.
PRODUCTION_PHASE_ORDER: Tuple[str, ...] = (
    "BRIDGE_CHECK",
    "SPAWN_BOOTSTRAP",
    "INITIAL_GATHERING",
    "BASE_CONSTRUCTION",
    "BOOT_SEQUENCE",
    "FOOD_AND_IRON",
    "ENCHANTING_PIPELINE",
    "NETHER_AND_BLAZE",
    "VILLAGER_INFRA",
    "XP_ENGINE",
    "IRON_FARM",
    "TOOL_PERFECTION",
    "WORLD_UNLOCK",
    "MEGABASE_INIT",
    "COMPLETE",
)

WOOD_SUFFIXES = ("_log", "_stem", "_hyphae")
PLANK_SUFFIX = "_planks"
FOOD_ITEMS = {
    "minecraft:bread",
    "minecraft:baked_potato",
    "minecraft:cooked_beef",
    "minecraft:cooked_chicken",
    "minecraft:cooked_cod",
    "minecraft:cooked_mutton",
    "minecraft:cooked_porkchop",
    "minecraft:cooked_rabbit",
    "minecraft:cooked_salmon",
    "minecraft:golden_carrot",
}
STONE_TOOLS = {
    "minecraft:stone_axe",
    "minecraft:stone_hoe",
    "minecraft:stone_pickaxe",
    "minecraft:stone_shovel",
    "minecraft:stone_sword",
}
IRON_ARMOR = {
    "minecraft:iron_boots",
    "minecraft:iron_chestplate",
    "minecraft:iron_helmet",
    "minecraft:iron_leggings",
}
SHULKER_BOXES = {
    "minecraft:shulker_box",
    "minecraft:white_shulker_box",
    "minecraft:orange_shulker_box",
    "minecraft:magenta_shulker_box",
    "minecraft:light_blue_shulker_box",
    "minecraft:yellow_shulker_box",
    "minecraft:lime_shulker_box",
    "minecraft:pink_shulker_box",
    "minecraft:gray_shulker_box",
    "minecraft:light_gray_shulker_box",
    "minecraft:cyan_shulker_box",
    "minecraft:purple_shulker_box",
    "minecraft:blue_shulker_box",
    "minecraft:brown_shulker_box",
    "minecraft:green_shulker_box",
    "minecraft:red_shulker_box",
    "minecraft:black_shulker_box",
}


def _unwrap(payload: Any) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    data = payload.get("data")
    if isinstance(data, dict):
        merged = dict(payload)
        merged.pop("data", None)
        merged.update(data)
        return merged
    return payload


def _inventory_counts(payload: Any) -> Dict[str, int]:
    data = _unwrap(payload)
    result: Dict[str, int] = {}
    slots = list(data.get("inventory", []))
    for equipment_key in ("armor", "offhand"):
        equipment = data.get(equipment_key, [])
        if isinstance(equipment, dict):
            slots.extend(equipment.values())
        elif isinstance(equipment, list):
            slots.extend(equipment)
    for slot in slots:
        if not isinstance(slot, dict):
            continue
        item_id = slot.get("id")
        if not isinstance(item_id, str) or item_id == "minecraft:air":
            continue
        result[item_id] = result.get(item_id, 0) + int(slot.get("count", 1) or 0)
    return result


def _deep_get(mapping: Mapping[str, Any], path: Sequence[str], default: Any = None) -> Any:
    value: Any = mapping
    for key in path:
        if not isinstance(value, Mapping) or key not in value:
            return default
        value = value[key]
    return value


def find_checkpoint(explicit: Optional[os.PathLike[str] | str] = None) -> Optional[Path]:
    """Resolve a checkpoint without creating or modifying any files."""
    candidates = []
    if explicit:
        candidates.append(Path(explicit))
    if os.environ.get(CHECKPOINT_ENV):
        candidates.append(Path(os.environ[CHECKPOINT_ENV]))
    candidates.extend(
        [
            Path.cwd() / DEFAULT_CHECKPOINT_NAME,
            Path(__file__).resolve().parents[2] / DEFAULT_CHECKPOINT_NAME,
        ]
    )
    seen = set()
    for candidate in candidates:
        resolved = candidate.expanduser().resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        if resolved.is_file():
            return resolved
    return None


def load_checkpoint(explicit: Optional[os.PathLike[str] | str] = None) -> Tuple[Dict[str, Any], Optional[Path]]:
    path = find_checkpoint(explicit)
    if path is None:
        return {}, None
    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return {}, path
    return payload if isinstance(payload, dict) else {}, path


@dataclass
class ProgressionEvidence:
    """A consistent read-only snapshot used by all milestone gates."""

    ctx: Any
    state: Dict[str, Any]
    current_inventory: Dict[str, int]
    checkpoint: Dict[str, Any]
    checkpoint_path: Optional[Path]

    @classmethod
    def capture(
        cls,
        ctx: Any,
        checkpoint_path: Optional[os.PathLike[str] | str] = None,
    ) -> "ProgressionEvidence":
        checkpoint, resolved = load_checkpoint(checkpoint_path)
        return cls(
            ctx=ctx,
            state=_unwrap(ctx.get_state()),
            current_inventory=_inventory_counts(ctx.get_inventory()),
            checkpoint=checkpoint,
            checkpoint_path=resolved,
        )

    @property
    def checkpoint_inventory(self) -> Dict[str, int]:
        raw = self.checkpoint.get("inventory_summary", {})
        if not isinstance(raw, dict):
            return {}
        return {str(key): int(value or 0) for key, value in raw.items()}

    @property
    def observed_inventory(self) -> Dict[str, int]:
        """Best known counts from the current inventory or latest checkpoint."""
        result = dict(self.checkpoint_inventory)
        for item_id, count in self.current_inventory.items():
            result[item_id] = max(result.get(item_id, 0), count)
        return result

    def current_count(self, item_id: str) -> int:
        return self.current_inventory.get(item_id, 0)

    def observed_count(self, item_id: str) -> int:
        return self.observed_inventory.get(item_id, 0)

    def observed_total(self, item_ids: Iterable[str]) -> int:
        counts = self.observed_inventory
        return sum(counts.get(item_id, 0) for item_id in item_ids)

    def custom(self, *path: str, default: Any = None) -> Any:
        custom_data = self.checkpoint.get("custom_data", {})
        if not isinstance(custom_data, dict):
            return default
        return _deep_get(custom_data, path, default)

    def phase_payload(self, phase: str) -> Dict[str, Any]:
        payloads = self.checkpoint.get("phase_payloads", {})
        if not isinstance(payloads, dict):
            payloads = {}
        payload = payloads.get(phase)
        if not isinstance(payload, dict):
            payload = self.custom("phase_payloads", phase, default={})
        return payload if isinstance(payload, dict) else {}

    def phase_complete(self, phase: str) -> bool:
        progress = self.checkpoint.get("phase_progress", {})
        if isinstance(progress, dict) and float(progress.get(phase, 0.0) or 0.0) >= 1.0:
            return True

        current = self.checkpoint.get("phase")
        if current in PRODUCTION_PHASE_ORDER and phase in PRODUCTION_PHASE_ORDER:
            return PRODUCTION_PHASE_ORDER.index(str(current)) > PRODUCTION_PHASE_ORDER.index(phase)
        return False

    def has_location(self, category: str) -> bool:
        locations = self.custom("locations", category, default=[])
        return isinstance(locations, list) and bool(locations)

    def block_id(self, position: Sequence[int]) -> str:
        if len(position) != 3:
            return ""
        response = _unwrap(self.ctx.get_block(int(position[0]), int(position[1]), int(position[2])))
        block_id = response.get("id") or response.get("type") or response.get("block")
        return str(block_id or "")


CheckPredicate = Callable[[ProgressionEvidence], bool]


@dataclass(frozen=True)
class GateCheck:
    description: str
    predicate: CheckPredicate


@dataclass(frozen=True)
class ProgressionGate:
    id: str
    name: str
    description: str
    production_phase: Optional[str]
    checks: Tuple[GateCheck, ...]

    def evaluate(self, evidence: ProgressionEvidence) -> Tuple[bool, str]:
        missing = []
        for check in self.checks:
            try:
                passed = bool(check.predicate(evidence))
            except Exception as exc:
                missing.append(f"{check.description} (error: {exc})")
            else:
                if not passed:
                    missing.append(check.description)
        if missing:
            checkpoint = str(evidence.checkpoint_path) if evidence.checkpoint_path else "not found"
            return False, f"Missing evidence: {'; '.join(missing)}. Checkpoint: {checkpoint}"
        return True, f"{self.id} acceptance gate satisfied"


def _phase(phase: str) -> GateCheck:
    return GateCheck(
        f"production phase {phase} completed",
        lambda evidence, phase=phase: evidence.phase_complete(phase),
    )


def _any_observed(evidence: ProgressionEvidence, item_ids: Iterable[str], count: int = 1) -> bool:
    return evidence.observed_total(item_ids) >= count


def _wood_equivalent(evidence: ProgressionEvidence) -> int:
    total = 0
    for item_id, count in evidence.observed_inventory.items():
        path = item_id.split(":", 1)[-1]
        if path.endswith(WOOD_SUFFIXES):
            total += count * 4
        elif path.endswith(PLANK_SUFFIX):
            total += count
    return total


def _food_count(evidence: ProgressionEvidence) -> int:
    return evidence.observed_total(FOOD_ITEMS)


def _base_location(evidence: ProgressionEvidence) -> Any:
    return evidence.custom("base_location") or evidence.custom("structures", "starter_house", "origin")


def _house_record(evidence: ProgressionEvidence) -> Dict[str, Any]:
    record = evidence.custom("structures", "starter_house", default={})
    if not isinstance(record, dict) or not record:
        record = evidence.custom("house_7x7", default={})
    return record if isinstance(record, dict) else {}


def _house_shell_verified(evidence: ProgressionEvidence) -> bool:
    """Probe the persisted 7x7 plan without placing or breaking blocks."""
    house = _house_record(evidence)
    origin = house.get("origin")
    if not isinstance(origin, (list, tuple)) or len(origin) != 3:
        return False
    ox, oy, oz = (int(value) for value in origin)
    # ``origin`` is the floor's northwest corner.  Probe the actual 7x7
    # structure produced by build_good_house: floor at y, perimeter walls at
    # y+1..y+3, and roof at y+4.  The old samples were all inside the room and
    # therefore required air blocks to be solid.
    solid_samples = (
        (ox + 1, oy, oz + 1),
        (ox + 5, oy, oz + 5),
        (ox + 1, oy + 2, oz),
        (ox + 5, oy + 2, oz + 6),
        (ox, oy + 1, oz + 3),
        (ox + 3, oy + 4, oz + 3),
    )
    for position in solid_samples:
        block_id = evidence.block_id(position)
        if not block_id or block_id in {"minecraft:air", "minecraft:cave_air", "minecraft:void_air"}:
            return False

    utilities = (
        (house.get("door"), ("_door",)),
        (house.get("crafting_table"), ("minecraft:crafting_table",)),
        (house.get("supply_chest") or house.get("chest"), ("minecraft:chest", "minecraft:trapped_chest")),
    )
    for position, expected in utilities:
        if not isinstance(position, (list, tuple)) or len(position) != 3:
            return False
        block_id = evidence.block_id(position)
        if not any(block_id == value or block_id.endswith(value) for value in expected):
            return False
    return True


def _portal_location(evidence: ProgressionEvidence) -> Any:
    locations = evidence.custom("locations", "nether_portal", default=[])
    if locations:
        return locations[0]
    return evidence.custom("nether_portal")


def _portal_verified(evidence: ProgressionEvidence) -> bool:
    location = _portal_location(evidence)
    if not isinstance(location, Mapping):
        return False
    try:
        x, y, z = int(location["x"]), int(location["y"]), int(location["z"])
    except (KeyError, TypeError, ValueError):
        return False
    nearby = (
        (x, y, z),
        (x, y + 1, z),
        (x + 1, y, z),
        (x - 1, y, z),
    )
    return any(
        evidence.block_id(position) in {"minecraft:obsidian", "minecraft:nether_portal"}
        for position in nearby
    )


def _explicit_flag(evidence: ProgressionEvidence, *paths: Sequence[str]) -> bool:
    for path in paths:
        if evidence.custom(*path):
            return True
    return False


PROGRESSION_GATES: Tuple[ProgressionGate, ...] = (
    ProgressionGate(
        "T1200",
        "Healthy Survival Runtime",
        "Bridge state is readable and the player is alive in a known dimension.",
        "BRIDGE_CHECK",
        (
            GateCheck("health above zero", lambda e: float(e.state.get("health", 0) or 0) > 0),
            GateCheck("player is not dead", lambda e: not bool(e.state.get("is_dead", False))),
            GateCheck("known dimension reported", lambda e: "overworld" in str(e.state.get("dimension", "")).lower() or "nether" in str(e.state.get("dimension", "")).lower() or "end" in str(e.state.get("dimension", "")).lower()),
        ),
    ),
    ProgressionGate(
        "T1201",
        "Bootstrap Checkpoint",
        "Bridge and spawn bootstrap have durable completion evidence.",
        "SPAWN_BOOTSTRAP",
        (_phase("BRIDGE_CHECK"), _phase("SPAWN_BOOTSTRAP"), GateCheck("world seed persisted", lambda e: e.checkpoint.get("world_seed") is not None)),
    ),
    ProgressionGate(
        "T1202",
        "Initial Survival Resources",
        "Wood, stone, and a stone tool were acquired without supplied materials.",
        "INITIAL_GATHERING",
        (
            _phase("INITIAL_GATHERING"),
            GateCheck("at least 64 plank-equivalent wood observed", lambda e: _wood_equivalent(e) >= 64),
            GateCheck("at least 32 cobblestone observed", lambda e: e.observed_count("minecraft:cobblestone") >= 32),
            GateCheck("at least one stone tool observed", lambda e: _any_observed(e, STONE_TOOLS)),
        ),
    ),
    ProgressionGate(
        "T1203",
        "Verified Starter House",
        "A persisted starter-house plan matches blocks in the real world.",
        "BASE_CONSTRUCTION",
        (
            _phase("BASE_CONSTRUCTION"),
            GateCheck("base location persisted", lambda e: bool(_base_location(e))),
            GateCheck("house plan persisted", lambda e: bool(_house_record(e))),
            GateCheck("walls, roof, door, crafting table, and chest verified in-world", _house_shell_verified),
        ),
    ),
    ProgressionGate(
        "T1204",
        "Food and Iron Independence",
        "The bot has sustainable food plus essential iron equipment.",
        "FOOD_AND_IRON",
        (
            _phase("FOOD_AND_IRON"),
            GateCheck("at least 16 durable food items observed", lambda e: _food_count(e) >= 16),
            GateCheck("iron pickaxe observed", lambda e: e.observed_count("minecraft:iron_pickaxe") >= 1),
            GateCheck("shield observed", lambda e: e.observed_count("minecraft:shield") >= 1),
            GateCheck("at least three iron armor pieces observed", lambda e: _any_observed(e, IRON_ARMOR, 3)),
            GateCheck("farm or renewable food source persisted", lambda e: bool(e.custom("structures", "food_source")) or bool(e.custom("farm_location")) or e.has_location("farm")),
        ),
    ),
    ProgressionGate(
        "T1205",
        "Enchanting Pipeline",
        "Diamond mining and a level-30 enchanting station are durably proven.",
        "ENCHANTING_PIPELINE",
        (
            _phase("ENCHANTING_PIPELINE"),
            GateCheck("diamond pickaxe observed", lambda e: e.observed_count("minecraft:diamond_pickaxe") >= 1),
            GateCheck("enchanting station verified", lambda e: bool(e.custom("structures", "enchanting_station", "verified")) or bool(e.phase_payload("ENCHANTING_PIPELINE").get("station_verified"))),
            GateCheck("level-30 enchant capability verified", lambda e: bool(e.custom("capabilities", "level_30_enchanting")) or bool(e.phase_payload("ENCHANTING_PIPELINE").get("level_30_ready"))),
        ),
    ),
    ProgressionGate(
        "T1206",
        "Verified Nether Portal",
        "A persisted portal location resolves to obsidian or active portal blocks.",
        "NETHER_AND_BLAZE",
        (
            GateCheck("Nether portal location persisted", lambda e: bool(_portal_location(e))),
            GateCheck("portal blocks verified in-world", _portal_verified),
        ),
    ),
    ProgressionGate(
        "T1207",
        "Blaze Rod Supply",
        "Fortress navigation and blaze combat produced enough rods.",
        "NETHER_AND_BLAZE",
        (
            _phase("NETHER_AND_BLAZE"),
            GateCheck("at least six blaze rods observed", lambda e: e.observed_count("minecraft:blaze_rod") >= 6),
            GateCheck("Nether fortress location persisted", lambda e: e.has_location("nether_fortress") or bool(e.custom("nether_fortress"))),
        ),
    ),
    ProgressionGate(
        "T1208",
        "Eyes of Ender Ready",
        "Enderman hunting or bartering plus blaze conversion produced 12 Eyes.",
        "WORLD_UNLOCK",
        (GateCheck("at least 12 Eyes of Ender observed", lambda e: e.observed_count("minecraft:eye_of_ender") >= 12),),
    ),
    ProgressionGate(
        "T1209",
        "Stronghold Located",
        "Triangulation produced a durable stronghold location.",
        "WORLD_UNLOCK",
        (
            GateCheck("stronghold coordinates persisted", lambda e: e.has_location("stronghold") or bool(e.custom("stronghold_coords")) or bool(e.phase_payload("WORLD_UNLOCK").get("stronghold_coords"))),
        ),
    ),
    ProgressionGate(
        "T1210",
        "End Portal Entered",
        "The portal was found, activated, and entered without teleport commands.",
        "WORLD_UNLOCK",
        (
            GateCheck("End portal location persisted", lambda e: e.has_location("end_portal") or bool(e.custom("end_portal"))),
            GateCheck("End entry explicitly checkpointed", lambda e: _explicit_flag(e, ("milestones", "end_entered"), ("end_entered",)) or "the_end" in str(e.state.get("dimension", ""))),
        ),
    ),
    ProgressionGate(
        "T1211",
        "Ender Dragon Defeated",
        "Dragon defeat has explicit durable evidence rather than an assumed success.",
        "WORLD_UNLOCK",
        (
            GateCheck("dragon defeat explicitly checkpointed", lambda e: _explicit_flag(e, ("milestones", "dragon_defeated"), ("dragon_defeated",)) or bool(e.phase_payload("WORLD_UNLOCK").get("dragon_defeated"))),
        ),
    ),
    ProgressionGate(
        "T1212",
        "Post-End Resource Unlock",
        "Elytra and five shulker boxes prove repeatable End-city capability.",
        "WORLD_UNLOCK",
        (
            _phase("WORLD_UNLOCK"),
            GateCheck("Elytra observed", lambda e: e.observed_count("minecraft:elytra") >= 1),
            GateCheck("at least five shulker boxes observed", lambda e: _any_observed(e, SHULKER_BOXES, 5)),
        ),
    ),
    ProgressionGate(
        "T1213",
        "Terraforming Handoff Ready",
        "A durable megabase location and excavation plan are ready after endgame.",
        "MEGABASE_INIT",
        (
            _phase("MEGABASE_INIT"),
            GateCheck("megabase location persisted", lambda e: bool(e.custom("megabase_location")) or e.has_location("megabase")),
            GateCheck("terraform plan persisted", lambda e: bool(e.custom("terraform", "plan")) or bool(e.custom("terraform_plan"))),
            GateCheck("beacon observed or verified", lambda e: e.observed_count("minecraft:beacon") >= 1 or bool(e.custom("structures", "beacon", "verified"))),
        ),
    ),
)


def gate_by_id(test_id: str) -> ProgressionGate:
    for gate in PROGRESSION_GATES:
        if gate.id == test_id:
            return gate
    raise KeyError(test_id)


__all__ = [
    "CHECKPOINT_ENV",
    "PRODUCTION_PHASE_ORDER",
    "PROGRESSION_GATES",
    "GateCheck",
    "ProgressionEvidence",
    "ProgressionGate",
    "find_checkpoint",
    "gate_by_id",
    "load_checkpoint",
]
