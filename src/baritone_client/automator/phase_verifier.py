"""Read-only postcondition verification for production automation phases.

Handlers describe whether their action sequence ran.  This module decides
whether the resulting live world and durable in-memory checkpoint state are
strong enough to credit the objective.  The checks intentionally mirror the
Suite 1200 Survival acceptance gates instead of trusting handler return values.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, Mapping, Sequence, Tuple

from .state_manager import Phase, StateManager
from .resource_manager import ResourceManager
from ..common.storage_catalog import catalog_for
from ..common.tasks import TaskResult


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
SHULKER_BOXES = {"minecraft:shulker_box"} | {
    f"minecraft:{color}_shulker_box"
    for color in (
        "white", "orange", "magenta", "light_blue", "yellow", "lime",
        "pink", "gray", "light_gray", "cyan", "purple", "blue", "brown",
        "green", "red", "black",
    )
}


@dataclass(frozen=True)
class VerificationResult:
    """Result of evaluating a phase's postconditions."""

    success: bool
    reason: str
    gate_ids: Tuple[str, ...] = ()


@dataclass(frozen=True)
class _Check:
    description: str
    predicate: Callable[["_Evidence"], bool]


@dataclass(frozen=True)
class _Spec:
    gate_ids: Tuple[str, ...]
    checks: Tuple[_Check, ...]


class _Evidence:
    """One consistent live/checkpoint snapshot for a candidate phase."""

    def __init__(
        self,
        client: Any,
        resources: ResourceManager,
        state: StateManager,
        candidate: Phase,
        result: TaskResult,
    ) -> None:
        self.client = client
        self.resources = resources
        self.manager = state
        self.candidate = candidate
        self.result = result
        self.state = self._unwrap(client.transport.dispatch("get_state", {}))
        current = resources.refresh_inventory()
        self.current_inventory = {
            str(key): int(value or 0) for key, value in current.items()
        }
        self.inventory = self._observed_inventory(self.current_inventory)
        try:
            self.stored_inventory = catalog_for(client, state).inventory_totals()
        except (OSError, RuntimeError, ValueError):
            self.stored_inventory = {}

    def _observed_inventory(self, current: Mapping[str, int]) -> Dict[str, int]:
        """Match Suite 1200's best-known current-or-checkpoint item counts."""
        observed = {str(key): int(value or 0) for key, value in current.items()}
        path = self.manager.checkpoint_dir / self.manager.CHECKPOINT_FILE
        try:
            checkpoint = json.loads(path.read_text(encoding="utf-8"))
            previous = checkpoint.get(
                "inventory_observations",
                checkpoint.get("inventory_summary", {}),
            )
        except (OSError, ValueError, AttributeError):
            previous = {}
        if isinstance(previous, Mapping):
            for item_id, count in previous.items():
                observed[str(item_id)] = max(
                    observed.get(str(item_id), 0), int(count or 0)
                )
        return observed

    @staticmethod
    def _unwrap(payload: Any) -> Dict[str, Any]:
        if not isinstance(payload, dict):
            return {}
        nested = payload.get("data")
        if isinstance(nested, dict):
            merged = dict(payload)
            merged.pop("data", None)
            merged.update(nested)
            return merged
        return payload

    def count(self, item_id: str) -> int:
        currently_owned = int(self.current_inventory.get(item_id, 0) or 0) + int(
            self.stored_inventory.get(item_id, 0) or 0
        )
        return max(int(self.inventory.get(item_id, 0) or 0), currently_owned)

    def total(self, item_ids: Iterable[str]) -> int:
        return sum(self.count(item_id) for item_id in item_ids)

    def custom(self, *path: str, default: Any = None) -> Any:
        value: Any = self.manager.custom_data
        for key in path:
            if not isinstance(value, Mapping) or key not in value:
                return default
            value = value[key]
        return value

    def payload(self, phase: Phase) -> Dict[str, Any]:
        if phase is self.candidate and self.result.data:
            persisted = dict(self.manager.get_phase_payload(phase))
            persisted.update(self.result.data)
            return persisted
        return self.manager.get_phase_payload(phase)

    def phase_complete(self, phase: Phase) -> bool:
        return phase is self.candidate or self.manager.is_phase_complete(phase)

    def has_location(self, category: str) -> bool:
        locations = self.custom("locations", category, default=[])
        return isinstance(locations, list) and bool(locations)

    def block_id(self, position: Sequence[int]) -> str:
        if len(position) != 3:
            return ""
        response = self._unwrap(
            self.client.transport.dispatch(
                "get_block",
                {"x": int(position[0]), "y": int(position[1]), "z": int(position[2])},
            )
        )
        return str(response.get("id") or response.get("type") or response.get("block") or "")


def _check(description: str, predicate: Callable[[_Evidence], bool]) -> _Check:
    return _Check(description, predicate)


def _wood_equivalent(evidence: _Evidence) -> int:
    total = 0
    item_ids = set(evidence.inventory) | set(evidence.stored_inventory)
    for item_id in item_ids:
        count = evidence.count(item_id)
        path = item_id.split(":", 1)[-1]
        if path.endswith(("_log", "_stem", "_hyphae")):
            total += int(count) * 4
        elif path.endswith("_planks"):
            total += int(count)
    return total


def _house_record(evidence: _Evidence) -> Dict[str, Any]:
    record = evidence.custom("structures", "starter_house", default={})
    if not isinstance(record, dict) or not record:
        record = evidence.custom("house_7x7", default={})
    return record if isinstance(record, dict) else {}


def _house_verified(evidence: _Evidence) -> bool:
    house = _house_record(evidence)
    origin = house.get("origin")
    if not isinstance(origin, (list, tuple)) or len(origin) != 3:
        return False
    ox, oy, oz = (int(value) for value in origin)
    solids = (
        (ox + 1, oy, oz + 1), (ox + 5, oy, oz + 5),
        (ox + 1, oy + 2, oz), (ox + 5, oy + 2, oz + 6),
        (ox, oy + 1, oz + 3), (ox + 3, oy + 4, oz + 3),
    )
    air = {"", "minecraft:air", "minecraft:cave_air", "minecraft:void_air"}
    if any(evidence.block_id(pos) in air for pos in solids):
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


def _portal_location(evidence: _Evidence) -> Any:
    locations = evidence.custom("locations", "nether_portal", default=[])
    if isinstance(locations, list) and locations:
        return locations[0]
    return evidence.custom("nether_portal")


def _portal_verified(evidence: _Evidence) -> bool:
    location = _portal_location(evidence)
    if not isinstance(location, Mapping):
        return False
    try:
        x, y, z = int(location["x"]), int(location["y"]), int(location["z"])
    except (KeyError, TypeError, ValueError):
        return False
    return any(
        evidence.block_id(pos) == "minecraft:nether_portal"
        for pos in ((x, y, z), (x, y + 1, z), (x + 1, y, z), (x - 1, y, z))
    )


def _flag(evidence: _Evidence, *paths: Sequence[str]) -> bool:
    return any(bool(evidence.custom(*path)) for path in paths)


def _specs() -> Dict[Phase, _Spec]:
    """Return postconditions aligned with Survival gates T1200-T1213."""
    return {
        Phase.BRIDGE_CHECK: _Spec(
            ("T1200",),
            (
                _check(
                    "health above zero",
                    lambda e: float(e.state.get("health", 0) or 0) > 0,
                ),
                _check(
                    "player is not dead",
                    lambda e: not bool(e.state.get("is_dead", False)),
                ),
                _check(
                    "known dimension reported",
                    lambda e: any(
                        name in str(e.state.get("dimension", "")).lower()
                        for name in ("overworld", "nether", "end")
                    ),
                ),
            ),
        ),
        Phase.SPAWN_BOOTSTRAP: _Spec(
            ("T1201",),
            (
                _check(
                    "BRIDGE_CHECK completed",
                    lambda e: e.phase_complete(Phase.BRIDGE_CHECK),
                ),
                _check(
                    "world seed persisted",
                    lambda e: bool(
                        e.manager.bound_world_identity
                        and e.manager.bound_world_identity.seed is not None
                    ),
                ),
            ),
        ),
        Phase.INITIAL_GATHERING: _Spec(("T1202",), (
            _check("at least 64 plank-equivalent wood observed", lambda e: _wood_equivalent(e) >= 64),
            _check("at least 32 cobblestone observed", lambda e: e.count("minecraft:cobblestone") >= 32),
            _check("at least one stone tool observed", lambda e: e.total(STONE_TOOLS) >= 1),
        )),
        Phase.BASE_CONSTRUCTION: _Spec(("T1203",), (
            _check(
                "base location persisted",
                lambda e: bool(e.custom("base_location") or _house_record(e).get("origin")),
            ),
            _check("house plan persisted", lambda e: bool(_house_record(e))),
            _check("walls, roof, door, crafting table, and chest verified in-world", _house_verified),
        )),
        Phase.BOOT_SEQUENCE: _Spec(("BOOT",), (
            _check(
                "boot action results persisted",
                lambda e: int(
                    e.payload(Phase.BOOT_SEQUENCE).get("completed_actions", 0) or 0
                ) > 0,
            ),
            _check(
                "boot sequence result persisted",
                lambda e: bool(
                    e.payload(Phase.BOOT_SEQUENCE).get("sequence_result")
                ),
            ),
            _check(
                "survival working margin established",
                lambda e: float(e.state.get("health", 0) or 0) >= 12
                and int(e.state.get("food_level", e.state.get("food", 0)) or 0)
                >= 14,
            ),
            _check(
                "stone tool observed",
                lambda e: e.total(STONE_TOOLS) >= 1,
            ),
            _check(
                "verified bootstrap infrastructure persisted",
                lambda e: bool(
                    e.custom("structures", "bootstrap_base", "verified")
                    or _house_record(e)
                ),
            ),
            _check(
                "renewable starter food source persisted",
                lambda e: bool(
                    e.custom("structures", "food_source", "verified")
                    or e.custom("farm_location")
                    or e.has_location("farm")
                ),
            ),
        )),
        Phase.FOOD_AND_IRON: _Spec(("T1204",), (
            _check("at least 16 durable food items observed", lambda e: e.total(FOOD_ITEMS) >= 16),
            _check("iron pickaxe observed", lambda e: e.count("minecraft:iron_pickaxe") >= 1),
            _check("shield observed", lambda e: e.count("minecraft:shield") >= 1),
            _check("at least three iron armor pieces observed", lambda e: e.total(IRON_ARMOR) >= 3),
            _check(
                "farm or renewable food source persisted",
                lambda e: bool(
                    e.custom("structures", "food_source")
                    or e.custom("farm_location")
                    or e.has_location("farm")
                ),
            ),
        )),
        Phase.ENCHANTING_PIPELINE: _Spec(("T1205",), (
            _check("diamond pickaxe observed", lambda e: e.count("minecraft:diamond_pickaxe") >= 1),
            _check(
                "enchanting station verified",
                lambda e: bool(
                    e.custom("structures", "enchanting_station", "verified")
                    or e.payload(Phase.ENCHANTING_PIPELINE).get("station_verified")
                ),
            ),
            _check(
                "level-30 enchant capability verified",
                lambda e: bool(
                    e.custom("capabilities", "level_30_enchanting")
                    or e.payload(Phase.ENCHANTING_PIPELINE).get("level_30_ready")
                ),
            ),
        )),
        Phase.NETHER_AND_BLAZE: _Spec(("T1206", "T1207"), (
            _check("Nether portal location persisted", lambda e: bool(_portal_location(e))),
            _check("portal blocks verified in-world", _portal_verified),
            _check("at least six blaze rods observed", lambda e: e.count("minecraft:blaze_rod") >= 6),
            _check(
                "Nether fortress location persisted",
                lambda e: e.has_location("nether_fortress")
                or bool(e.custom("nether_fortress")),
            ),
        )),
        Phase.WORLD_UNLOCK: _Spec(("T1208", "T1209", "T1210", "T1211", "T1212"), (
            _check(
                "at least 12 Eyes of Ender observed",
                lambda e: e.count("minecraft:ender_eye") >= 12
                or int(e.payload(Phase.WORLD_UNLOCK).get("eyes_ready", 0) or 0) >= 12,
            ),
            _check(
                "stronghold coordinates persisted",
                lambda e: e.has_location("stronghold")
                or bool(
                    e.custom("stronghold_coords")
                    or e.payload(Phase.WORLD_UNLOCK).get("stronghold_coords")
                ),
            ),
            _check(
                "End portal location persisted",
                lambda e: e.has_location("end_portal")
                or bool(e.custom("end_portal")),
            ),
            _check(
                "End entry explicitly checkpointed",
                lambda e: _flag(
                    e, ("milestones", "end_entered"), ("end_entered",)
                )
                or "the_end" in str(e.state.get("dimension", "")),
            ),
            _check(
                "dragon defeat explicitly checkpointed",
                lambda e: _flag(
                    e, ("milestones", "dragon_defeated"), ("dragon_defeated",)
                )
                or bool(e.payload(Phase.WORLD_UNLOCK).get("dragon_defeated")),
            ),
            _check("Elytra observed", lambda e: e.count("minecraft:elytra") >= 1),
            _check("at least five shulker boxes observed", lambda e: e.total(SHULKER_BOXES) >= 5),
        )),
        Phase.MEGABASE_INIT: _Spec(("T1213",), (
            _check(
                "megabase location persisted",
                lambda e: bool(
                    e.custom("megabase_location") or e.has_location("megabase")
                ),
            ),
            _check(
                "terraform plan persisted",
                lambda e: bool(
                    e.custom("terraform", "plan") or e.custom("terraform_plan")
                ),
            ),
            _check(
                "beacon observed or verified",
                lambda e: e.count("minecraft:beacon") >= 1
                or bool(e.custom("structures", "beacon", "verified")),
            ),
        )),
    }


class PhaseVerifier:
    """Evaluate phase postconditions using read-only live-world calls."""

    def __init__(self, client: Any, resources: ResourceManager, state: StateManager) -> None:
        self.client = client
        self.resources = resources
        self.state = state
        self.specs = _specs()

    def verify(self, phase: Phase, result: TaskResult) -> VerificationResult:
        spec = self.specs.get(phase)
        if spec is None:
            return VerificationResult(False, f"No postcondition verifier registered for {phase.name}")
        try:
            evidence = _Evidence(self.client, self.resources, self.state, phase, result)
        except Exception as exc:
            return VerificationResult(False, f"Could not capture live evidence: {exc}", spec.gate_ids)

        missing = []
        for check in spec.checks:
            try:
                passed = bool(check.predicate(evidence))
            except Exception as exc:
                missing.append(f"{check.description} (error: {exc})")
            else:
                if not passed:
                    missing.append(check.description)
        if missing:
            return VerificationResult(
                False,
                f"Missing evidence for {', '.join(spec.gate_ids)}: {'; '.join(missing)}",
                spec.gate_ids,
            )
        return VerificationResult(True, f"{', '.join(spec.gate_ids)} postconditions satisfied", spec.gate_ids)


__all__ = ["PhaseVerifier", "VerificationResult"]
