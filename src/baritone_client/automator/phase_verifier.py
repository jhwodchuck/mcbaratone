"""Read-only postcondition verification for production automation phases.

Handlers describe whether their action sequence ran.  This module decides
whether the resulting live world and durable in-memory checkpoint state are
strong enough to credit the objective.  The checks intentionally mirror the
Suite 1200 Survival acceptance gates instead of trusting handler return values.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from math import dist
from typing import Any, Callable, Dict, Iterable, Mapping, Sequence, Tuple

from .state_manager import Phase, StateManager
from .resource_manager import ResourceManager
from .phase_verifier_support import FOOD_ITEMS, IRON_ARMOR, SHULKER_BOXES, STONE_TOOLS
from .phase_verifier_support import position as _position
from ..common.iron_farm import verify_farm_structure_witnesses
from ..common.storage_catalog import catalog_for
from ..common.tasks import TaskResult


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
        self._entity_cache: Dict[int, Tuple[Dict[str, Any], ...]] = {}

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

    def entities(self, radius: int = 32) -> Tuple[Dict[str, Any], ...]:
        """Capture the registered get_entities route's real schema lazily."""
        bounded = max(1, min(64, int(radius)))
        if bounded not in self._entity_cache:
            response = self._unwrap(
                self.client.transport.dispatch("get_entities", {"radius": bounded})
            )
            values = response.get("entities", [])
            self._entity_cache[bounded] = tuple(
                dict(value) for value in values if isinstance(value, Mapping)
            ) if isinstance(values, list) else ()
        return self._entity_cache[bounded]


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


def _homestead_record(evidence: _Evidence) -> Dict[str, Any]:
    value = evidence.custom("homestead", default={})
    return value if isinstance(value, dict) else {}


def _homestead_steps(evidence: _Evidence) -> Mapping[str, Mapping[str, Any]]:
    homestead = _homestead_record(evidence)
    entries = homestead.get("steps")
    if isinstance(entries, Mapping):
        return entries
    if not isinstance(entries, list):
        return {}
    result = {}
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        name = entry.get("name")
        if isinstance(name, str):
            result[name] = entry
    return result


def _homestead_anchor_verified(evidence: _Evidence) -> bool:
    anchor = _homestead_record(evidence).get("anchor")
    if not isinstance(anchor, (list, tuple)) or len(anchor) != 3:
        return False
    ground = evidence.block_id((int(anchor[0]), int(anchor[1]) - 1, int(anchor[2])))
    return (
        evidence.state.get("dimension") == "minecraft:overworld"
        and bool(ground)
        and "water" not in ground
        and "lava" not in ground
    )


def _homestead_lighting_verified(evidence: _Evidence) -> bool:
    record = _homestead_steps(evidence).get("light_perimeter", {})
    intended = record.get("intended", [])
    if not record.get("verified") or not isinstance(intended, list) or not intended:
        return False
    for position in intended:
        if not isinstance(position, (list, tuple)) or len(position) != 3:
            return False
        if evidence.block_id(position) not in {
            "minecraft:torch",
            "minecraft:wall_torch",
        }:
            return False
    return True


def _legacy_infrastructure_record(evidence: _Evidence) -> Mapping[str, Any]:
    structures = evidence.custom("structures", default={})
    if not isinstance(structures, Mapping):
        return {}
    bootstrap = structures.get("bootstrap_base")
    if isinstance(bootstrap, Mapping):
        return bootstrap
    house = evidence.custom("structures", "starter_house", default={})
    return house if isinstance(house, Mapping) else {}


def _legacy_infrastructure_verified(evidence: _Evidence) -> bool:
    bootstrap = _legacy_infrastructure_record(evidence)
    expected = (
        (bootstrap.get("crafting_table"), {"minecraft:crafting_table"}),
        (
            bootstrap.get("furnace"),
            {"minecraft:furnace", "minecraft:blast_furnace"},
        ),
        (
            bootstrap.get("supply_chest") or bootstrap.get("chest"),
            {"minecraft:chest", "minecraft:trapped_chest"},
        ),
    )
    for position, block_ids in expected:
        if not isinstance(position, (list, tuple)) or len(position) != 3:
            break
        if evidence.block_id(position) not in block_ids:
            break
    else:
        return True

    # BOOT_SEQUENCE precedes BASE_CONSTRUCTION. A partial starter-house record
    # from an interrupted later attempt must not hide a complete bootstrap
    # table/furnace/chest, but a complete live house remains valid fallback
    # evidence for older checkpoints without usable bootstrap coordinates.
    return bool(_house_record(evidence)) and _house_verified(evidence)


def _legacy_food_source_verified(evidence: _Evidence) -> bool:
    crops = {
        "minecraft:wheat",
        "minecraft:carrots",
        "minecraft:potatoes",
        "minecraft:beetroots",
    }
    record = evidence.custom("structures", "food_source", default={})
    plots = record.get("plots", []) if isinstance(record, Mapping) else []
    for plot in plots:
        if (
            isinstance(plot, (list, tuple))
            and len(plot) >= 3
            and evidence.block_id(plot[:3]) in crops
        ):
            return True
    location = evidence.custom("farm_location")
    if not isinstance(location, (list, tuple)) or len(location) != 3:
        return False
    x, y, z = (int(value) for value in location)
    return any(
        evidence.block_id((x + dx, y + dy, z + dz)) in crops
        for dx in range(-1, 2)
        for dz in range(-1, 2)
        for dy in (0, 1)
    )


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


def _villager_breeder_verified(evidence: _Evidence) -> bool:
    payload = evidence.payload(Phase.VILLAGER_INFRA)
    breeder = payload.get("breeder") if isinstance(payload, Mapping) else None
    breeding = payload.get("breeding") if isinstance(payload, Mapping) else None
    if not isinstance(breeder, Mapping) or not isinstance(breeding, Mapping):
        return False
    if not breeder.get("verified") or not breeding.get("offspring_observed"):
        return False
    bed_blocks = breeder.get("bed_blocks")
    required_beds = int(breeder.get("required_beds", 0) or 0)
    if not isinstance(bed_blocks, list) or required_beds < 3:
        return False
    live_bed_blocks = sum(
        1
        for position in bed_blocks
        if isinstance(position, (list, tuple))
        and len(position) == 3
        and evidence.block_id(position).endswith("_bed")
    )
    if live_bed_blocks < required_beds * 2:
        return False
    offspring_uuid = str(breeding.get("offspring_uuid") or "")
    return bool(offspring_uuid) and any(
        str(entity.get("uuid") or "") == offspring_uuid
        and entity.get("type") == "minecraft:villager"
        and bool(entity.get("is_baby"))
        for entity in evidence.entities(32)
    )


def _xp_engine_verified(evidence: _Evidence) -> bool:
    payload = evidence.payload(Phase.XP_ENGINE)
    farm = payload.get("farm") if isinstance(payload, Mapping) else None
    grind = payload.get("grind") if isinstance(payload, Mapping) else None
    if not isinstance(farm, Mapping) or not isinstance(grind, Mapping):
        return False
    location = farm.get("location")
    if not isinstance(location, (list, tuple)) or len(location) != 3:
        return False
    try:
        achieved = int(grind.get("achieved_level", 0) or 0)
        encounters = int(grind.get("encounters", 0) or 0)
        xp_gained = int(grind.get("xp_gained", 0) or 0)
        live_level = int(evidence.state.get("experience_level", 0) or 0)
    except (TypeError, ValueError):
        return False
    return (
        farm.get("verified") is True
        and evidence.block_id(location) == "minecraft:spawner"
        and achieved >= 30
        and encounters > 0
        and xp_gained > 0
        and live_level >= 30
    )


def _farm_entities(
    evidence: _Evidence,
    entity_type: str,
    radius: float,
) -> Tuple[Dict[str, Any], ...]:
    anchor = _position(evidence.payload(Phase.IRON_FARM).get("farm_location"))
    if anchor is None:
        return ()
    matches = []
    for entity in evidence.entities(max(16, int(radius) + 4)):
        if str(entity.get("type", "")) != entity_type:
            continue
        location = _position(entity.get("position"))
        if location is not None and dist(anchor, location) <= radius:
            matches.append(entity)
    return tuple(matches)


def _iron_structure_verified(evidence: _Evidence) -> bool:
    payload = evidence.payload(Phase.IRON_FARM)
    return verify_farm_structure_witnesses(
        evidence.block_id,
        payload.get("farm_location", ()),
        payload.get("structure_witnesses"),
    )


def _adult_farm_villagers(evidence: _Evidence) -> int:
    return sum(
        1
        for entity in _farm_entities(evidence, "minecraft:villager", 8)
        if not bool(entity.get("is_baby", False))
    )


def _live_librarians(evidence: _Evidence) -> int:
    return sum(
        1
        for entity in evidence.entities(32)
        if str(entity.get("type", "")) == "minecraft:villager"
        and str(entity.get("profession", "")) == "minecraft:librarian"
        and not bool(entity.get("is_baby", False))
    )


def _industrial_specs() -> Dict[Phase, _Spec]:
    """Postconditions for objectives beyond the numbered Survival suite."""
    return {
        Phase.IRON_FARM: _Spec(("IRON_FARM",), (
            _check(
                "versioned iron-farm evidence recorded",
                lambda e: e.payload(Phase.IRON_FARM).get("verification_version") == 1,
            ),
            _check("three adult villagers visible at farm", lambda e: _adult_farm_villagers(e) >= 3),
            _check(
                "captured zombie visible at farm",
                lambda e: bool(_farm_entities(e, "minecraft:zombie", 8)),
            ),
            _check("farm structure witnesses verified in-world", _iron_structure_verified),
            _check(
                "farm-adjacent iron golem visible",
                lambda e: bool(_farm_entities(e, "minecraft:iron_golem", 16)),
            ),
            _check(
                "handler reported no implementation blocker",
                lambda e: not e.payload(Phase.IRON_FARM).get("implementation_blocker"),
            ),
        )),
        Phase.TOOL_PERFECTION: _Spec(("LIBRARIAN_BOOKS",), (
            _check(
                "versioned trading evidence recorded",
                lambda e: e.payload(Phase.TOOL_PERFECTION).get("verification_version") == 2,
            ),
            _check("adult librarian visible", lambda e: _live_librarians(e) >= 1),
            _check(
                "all required tool enchantments verified from item components",
                lambda e: {
                    "mending", "efficiency", "unbreaking", "fortune"
                }.issubset(set(e.payload(Phase.TOOL_PERFECTION).get("verified_enchantments", []))),
            ),
            _check(
                "handler reported no implementation blocker",
                lambda e: not e.payload(Phase.TOOL_PERFECTION).get("implementation_blocker"),
            ),
            _check(
                "perfected tool metadata verified",
                lambda e: bool(e.payload(Phase.TOOL_PERFECTION).get("tool_perfected")),
            ),
        )),
    }

def _postgame_specs() -> Dict[Phase, _Spec]:
    """Return bounded postgame gates kept separate from legacy survival gates."""
    return {
        Phase.TERRAFORM: _Spec(("T1214",), (
            _check(
                "terraform operations completed successfully",
                lambda e: bool(e.payload(Phase.TERRAFORM).get("verified_operations")),
            ),
            _check(
                "terraform payload reached its bounded target",
                lambda e: bool(e.payload(Phase.TERRAFORM).get("progress_complete"))
                and int(e.payload(Phase.TERRAFORM).get("chunks_completed", 0)) > 0
                and int(e.payload(Phase.TERRAFORM).get("chunks_completed", 0))
                == int(e.payload(Phase.TERRAFORM).get("chunks_total", -1)),
            ),
            _check(
                # Was a cursor check (next_index == progress_entries_total).
                # terraform_ring/terraform_area now record-and-continue --
                # the cursor always reaches the end of a sweep whether or not
                # every chunk actually succeeded, so that comparison became
                # unconditionally true and stopped verifying anything. A
                # ledger with zero failed and zero unverified entries is the
                # replacement: it is the one thing record-and-continue can no
                # longer guarantee for free.
                #
                # Reads the payload's own chunks_failed/chunks_unverified
                # (scoped by terraform_area to this call's own chunks), NOT
                # terraform_progress.counts -- that mirror sums the WHOLE
                # ledger across every retry this phase has ever made, so a
                # stale failure left over from an abandoned earlier attempt
                # (a different plan, a different radius) would fail this
                # gate forever even after a fully clean re-run.
                "terraform progress has no failed or unverified chunks",
                lambda e: int(e.payload(Phase.TERRAFORM).get("chunks_failed", -1)) == 0
                and int(e.payload(Phase.TERRAFORM).get("chunks_unverified", -1)) == 0,
            ),
            _check(
                "terraform plan persisted",
                lambda e: bool(e.payload(Phase.TERRAFORM).get("terraform_plan")),
            ),
        )),
        Phase.CITY_BUILD: _Spec(("T1215",), (
            _check(
                "city operations completed successfully",
                lambda e: bool(e.payload(Phase.CITY_BUILD).get("verified_operations")),
            ),
            _check(
                "city payload reached its bounded target",
                lambda e: bool(e.payload(Phase.CITY_BUILD).get("progress_complete"))
                and int(e.payload(Phase.CITY_BUILD).get("districts_completed", 0)) > 0
                and int(e.payload(Phase.CITY_BUILD).get("rings_completed", 0)) > 0,
            ),
            _check(
                "city progress persisted consistently",
                lambda e: int(e.custom("city_progress", "districts_completed", default=-1))
                == int(e.payload(Phase.CITY_BUILD).get("districts_completed", -2))
                and int(e.custom("city_progress", "ring", default=-1))
                == int(e.payload(Phase.CITY_BUILD).get("rings_completed", -2)),
            ),
        )),
    }


def _specs() -> Dict[Phase, _Spec]:
    """Return postconditions aligned with Survival and postgame gates."""
    specs = {
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
                "homestead dry-anchor established",
                _homestead_anchor_verified,
            ),
            _check(
                "infrastructure verified and persisted",
                _legacy_infrastructure_verified,
            ),
            _check(
                "farm verified and persisted",
                _legacy_food_source_verified,
            ),
            _check(
                "light perimeter verified",
                _homestead_lighting_verified,
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
        Phase.VILLAGER_INFRA: _Spec(("VILLAGER_INFRA",), (
            _check(
                "bed capacity and newly observed villager offspring verified in-world",
                _villager_breeder_verified,
            ),
        )),
        Phase.XP_ENGINE: _Spec(("XP_ENGINE",), (
            _check(
                "live spawner source and level-30 XP postcondition verified",
                _xp_engine_verified,
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
        **_industrial_specs(),
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
    specs.update(_postgame_specs())
    return specs


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
