"""State-driven scheduling and bounded local farming opportunities.

The objective graph remains the authority for progression dependencies.  This
module supplies *preference*, never completion: live observations can choose a
better runnable sibling or justify one small renewable-resource action, but a
phase still has to pass its normal handler and verifier before it is DONE.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from ..common.combat import get_nearby_entities
from ..common.defense import assess_threats
from ..common.husbandry import BREEDING_FOOD, breed_pair
from ..common.forestry import run_wood_cycle
from ..common.inventory import get_inventory
from ..common.navigation import find_nearby_block, goto
from .end_readiness import (
    FleetRole,
    allows_local_work,
    fleet_role,
    record_readiness,
    role_focused_candidates,
)
from . import food_opportunity
from .iron_scheduler import iron_cycle_ready, run_scheduled_iron_cycle
from .local_opportunity import LocalOpportunity, OpportunityKind
from .objective import Objective
from .state_manager import Phase


CROP_BLOCKS = (
    "minecraft:wheat",
    "minecraft:carrots",
    "minecraft:potatoes",
    "minecraft:beetroots",
)
CROP_ITEMS = (
    "minecraft:wheat",
    "minecraft:carrot",
    "minecraft:potato",
    "minecraft:beetroot",
)
PLANTABLE_ITEMS = (
    "minecraft:wheat_seeds",
    "minecraft:carrot",
    "minecraft:potato",
    "minecraft:beetroot_seeds",
)
LEATHER_ANIMALS = {
    "cow",
    "mooshroom",
    "horse",
    "donkey",
    "mule",
    "llama",
}


@dataclass(frozen=True)
class GameSignals:
    """One best-effort snapshot used for a single scheduling decision."""

    observed: bool = False
    entities_observed: bool = False
    dimension: str = ""
    biome: str = ""
    health: float = 0.0
    food: int = 0
    experience_level: int = 0
    world_time: int = 0
    position: Tuple[int, int, int] = (0, 64, 0)
    inventory: Mapping[str, int] = field(default_factory=dict)
    animals: Mapping[str, int] = field(default_factory=dict)
    adult_animals: Mapping[str, int] = field(default_factory=dict)
    adult_villagers: int = 0
    nearby_hostiles: int = 0
    crop_location: Optional[Tuple[int, int, int]] = None
    crop_block: str = ""
    known_farm_location: Optional[Tuple[int, int, int]] = None
    known_portal: bool = False

    def count(self, item_id: str) -> int:
        """Return an inventory count without trusting value types."""
        try:
            return max(0, int(self.inventory.get(item_id, 0) or 0))
        except (TypeError, ValueError):
            return 0

    def animal_pairs(self) -> Dict[str, int]:
        """Breedable adult pairs whose food is currently carried."""
        pairs: Dict[str, int] = {}
        for family, count in self.adult_animals.items():
            food = BREEDING_FOOD.get(family)
            if food and int(count) >= 2 and self.count(food) >= 2:
                pairs[family] = int(count) // 2
        return pairs

    @property
    def safe_for_local_work(self) -> bool:
        """Local side work is allowed only with a comfortable safety margin."""
        return (
            self.observed
            and self.entities_observed
            and "overworld" in self.dimension
            and self.health >= 16.0
            and self.food >= 14
            and self.nearby_hostiles == 0
            and self.world_time % 24000 < 12000
        )

@dataclass(frozen=True)
class PhaseScore:
    """Explainable signal contribution to one runnable phase."""

    value: float
    reasons: Tuple[str, ...] = ()


@dataclass(frozen=True)
class OpportunityResult:
    """Evidence recorded after a local opportunity attempt."""

    opportunity: LocalOpportunity
    success: bool
    detail: str
    before: int = 0
    after: int = 0


@dataclass(frozen=True)
class SchedulingDecision:
    """One scheduler turn: either local work or a runnable objective."""

    objective: Optional[Objective] = None
    opportunity_result: Optional[OpportunityResult] = None
    score: float = 0.0
    reasons: Tuple[str, ...] = ()
    role_hold: bool = False

    @property
    def local_work(self) -> bool:
        return self.opportunity_result is not None

    @property
    def summary(self) -> str:
        if self.opportunity_result is not None:
            result = self.opportunity_result
            status = "verified" if result.success else "deferred"
            return (
                f"ADAPTIVE WORK: {result.opportunity.kind.value} {status}: "
                f"{result.opportunity.reason} ({result.detail})"
            )
        if self.objective is None:
            if self.reasons:
                return f"END READINESS HOLD: {'; '.join(self.reasons)}"
            return "ADAPTIVE PHASE: no runnable objective"
        explanation = "; ".join(self.reasons) or "stable graph fallback"
        return (
            f"ADAPTIVE PHASE: selected {self.objective.phase.name} "
            f"(score={self.score:.1f}; {explanation})"
        )


def _unwrap(response: Any) -> Mapping[str, Any]:
    if not isinstance(response, Mapping):
        return {}
    data = response.get("data", response)
    return data if isinstance(data, Mapping) else {}


def _position(snapshot: Mapping[str, Any]) -> Tuple[int, int, int]:
    raw = snapshot.get("block_position", snapshot.get("position", {}))
    raw = raw if isinstance(raw, Mapping) else {}
    try:
        return (
            int(float(raw.get("x", 0) or 0)),
            int(float(raw.get("y", 64) or 64)),
            int(float(raw.get("z", 0) or 0)),
        )
    except (TypeError, ValueError):
        return (0, 64, 0)


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _coordinate(value: Any) -> Optional[Tuple[int, int, int]]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return None
    try:
        return tuple(int(float(part)) for part in value)  # type: ignore[return-value]
    except (TypeError, ValueError):
        return None


def _known_farm(state: Any) -> Optional[Tuple[int, int, int]]:
    custom = getattr(state, "custom_data", {}) or {}
    direct = _coordinate(custom.get("farm_location"))
    if direct is not None:
        return direct
    wheat_farm = custom.get("wheat_farm", {})
    if isinstance(wheat_farm, Mapping):
        origin = _coordinate(wheat_farm.get("origin"))
        if origin is not None:
            return origin
    structures = custom.get("structures", {})
    if isinstance(structures, Mapping):
        source = structures.get("food_source", {})
        if isinstance(source, Mapping):
            return _coordinate(source.get("location"))
    return None


def _known_portal(state: Any) -> bool:
    custom = getattr(state, "custom_data", {}) or {}
    if custom.get("nether_portal"):
        return True
    locations = custom.get("locations", {})
    return isinstance(locations, Mapping) and bool(locations.get("nether_portal"))


def _family(entity_type: object) -> str:
    path = str(entity_type or "").lower().split(":", 1)[-1]
    for family in BREEDING_FOOD:
        if path == family or path.endswith(f"_{family}"):
            return family
    return ""


def collect_game_signals(client: Any, resources: Any, state: Any) -> GameSignals:
    """Collect live scheduling inputs, falling back to fixed priorities on error."""
    try:
        snapshot = _unwrap(client.transport.dispatch("get_state", {}))
    except Exception:
        return GameSignals()
    if not snapshot or snapshot.get("error"):
        return GameSignals()

    try:
        inventory = resources.refresh_inventory()
    except Exception:
        inventory = getattr(resources, "cached_inventory", {}) or {}
    inventory = inventory if isinstance(inventory, Mapping) else {}

    try:
        entities = get_nearby_entities(client, radius=24, raise_on_error=True)
        entities_observed = True
    except Exception:
        entities = []
        entities_observed = False

    animals: Dict[str, int] = {}
    adult_animals: Dict[str, int] = {}
    villagers = 0
    for entity in entities:
        entity_type = str(entity.get("type", "")).lower()
        if entity_type == "minecraft:villager" and not bool(entity.get("is_baby")):
            villagers += 1
        family = _family(entity_type)
        if family:
            animals[family] = animals.get(family, 0) + 1
            if not bool(entity.get("is_baby")):
                adult_animals[family] = adult_animals.get(family, 0) + 1

    try:
        threats = assess_threats(entities, dict(snapshot))
    except Exception:
        threats = []

    dimension = str(snapshot.get("dimension", ""))
    crop_location = None
    if "overworld" in dimension:
        crop_location = find_nearby_block(client, list(CROP_BLOCKS), radius=24)
    crop_block = ""
    if crop_location is not None:
        try:
            crop_block = str(
                _unwrap(
                    client.transport.dispatch(
                        "get_block",
                        {
                            "x": crop_location[0],
                            "y": crop_location[1],
                            "z": crop_location[2],
                        },
                    )
                ).get("id", "")
            )
        except Exception:
            crop_block = ""

    return GameSignals(
        observed=True,
        entities_observed=entities_observed,
        dimension=dimension,
        biome=str(snapshot.get("biome", "")),
        health=_as_float(snapshot.get("health", 0) or 0),
        food=_as_int(snapshot.get("food_level", snapshot.get("food", 0)) or 0),
        experience_level=_as_int(snapshot.get("experience_level", 0) or 0),
        world_time=_as_int(snapshot.get("world_time", 0) or 0),
        position=_position(snapshot),
        inventory=dict(inventory),
        animals=animals,
        adult_animals=adult_animals,
        adult_villagers=villagers,
        nearby_hostiles=len(threats),
        crop_location=crop_location,
        crop_block=crop_block,
        known_farm_location=_known_farm(state),
        known_portal=_known_portal(state),
    )


def score_phase(phase: Phase, signals: GameSignals) -> PhaseScore:
    """Return live utility for a runnable phase without changing eligibility."""
    if not signals.observed:
        return PhaseScore(0.0, ())

    score = 0.0
    reasons = []
    inventory = signals.inventory

    def add(points: float, reason: str) -> None:
        nonlocal score
        score += points
        reasons.append(reason)

    if phase is Phase.NETHER_AND_BLAZE:
        if "the_nether" in signals.dimension or signals.dimension.endswith(":nether"):
            add(120, "already in the Nether")
        if signals.known_portal:
            add(22, "verified portal is known")
        if signals.count("minecraft:obsidian") >= 10:
            add(20, "portal obsidian is carried")
        if signals.count("minecraft:flint_and_steel") >= 1:
            add(12, "portal ignition is carried")
        rods = signals.count("minecraft:blaze_rod")
        if 0 < rods < 6:
            add(30, "blaze-rod objective is already in progress")

    elif phase is Phase.ENCHANTING_PIPELINE:
        leather_adults = sum(
            count
            for family, count in signals.adult_animals.items()
            if family in LEATHER_ANIMALS
        )
        if leather_adults:
            add(35, "leather animals are nearby")
        if any(family in LEATHER_ANIMALS for family in signals.animal_pairs()):
            add(45, "a renewable leather pair can be bred now")
        if signals.count("minecraft:leather") or signals.count("minecraft:paper"):
            add(12, "book materials are already carried")
        if signals.count("minecraft:diamond") >= 2:
            add(18, "enchanting-table diamonds are carried")
        if signals.experience_level >= 20:
            add(12, "experience is already accumulated")

    elif phase is Phase.VILLAGER_INFRA:
        if signals.adult_villagers >= 2:
            add(110, "two adult villagers are nearby")
            if signals.crop_location or signals.known_farm_location:
                add(20, "a crop supply is available for breeding")
            if signals.count("minecraft:bread") >= 6:
                add(25, "breeding bread is carried")
            elif signals.count("minecraft:wheat") >= 18:
                add(18, "breeding wheat is carried")

    elif phase is Phase.XP_ENGINE:
        if signals.experience_level >= 20:
            add(20, "high experience makes XP infrastructure timely")
        if signals.count("minecraft:lapis_lazuli"):
            add(8, "lapis is ready for the XP loop")

    elif phase is Phase.IRON_FARM:
        if signals.adult_villagers >= 3:
            add(70, "an iron-farm villager population is nearby")
        if signals.count("minecraft:iron_ingot") < 16:
            add(8, "iron reserves are low")

    elif phase is Phase.TOOL_PERFECTION:
        if signals.experience_level >= 30:
            add(35, "level 30 is available for tool work")
        if signals.count("minecraft:emerald") >= 16:
            add(20, "trading emeralds are carried")

    elif phase is Phase.WORLD_UNLOCK:
        eyes = signals.count("minecraft:ender_eye")
        pearls = signals.count("minecraft:ender_pearl")
        if eyes:
            add(min(48, eyes * 4), "Eyes of Ender are already carried")
        elif pearls:
            add(min(24, pearls * 2), "ender pearls are already carried")
        if "the_end" in signals.dimension or signals.dimension.endswith(":end"):
            add(120, "already in the End")

    elif phase is Phase.MEGABASE_INIT:
        if signals.count("minecraft:beacon"):
            add(45, "a beacon is available")
        if signals.count("minecraft:shulker_box"):
            add(20, "bulk storage is available")

    # A modest inventory-density signal prefers work whose ingredients are
    # already in hand without overpowering the explicit opportunity rules.
    if phase in {Phase.TERRAFORM, Phase.CITY_BUILD} and len(inventory) >= 20:
        add(5, "inventory is stocked for construction")

    return PhaseScore(score, tuple(reasons))


class AdaptiveScheduler:
    """Select explainable live objectives and recurring local farm work."""

    _COOLDOWNS = {
        OpportunityKind.ANIMAL_FARM: 300.0,
        OpportunityKind.CROP_FARM: 180.0,
        OpportunityKind.WOOD_FARM: 60.0,
        OpportunityKind.IRON_MINE: 120.0,
        OpportunityKind.FOOD_RECOVERY: 60.0,
        OpportunityKind.FOOD_PRODUCTION: 120.0,
    }

    def __init__(self, client: Any, resources: Any, state: Any):
        self.client = client
        self.resources = resources
        self.state = state

    def observe(self) -> GameSignals:
        return collect_game_signals(self.client, self.resources, self.state)

    def next_step(self, planner: Any) -> SchedulingDecision:
        """Observe once, run useful local work, or choose a runnable objective."""
        signals = self.observe()
        completed = tuple(planner.completed_phases())
        role = fleet_role(self.state)
        if signals.observed:
            record_readiness(
                self.state,
                signals.inventory,
                completed=completed,
                known_portal=signals.known_portal,
            )
        opportunity = None
        if allows_local_work(role):
            opportunity = self.select_local_opportunity(
                signals,
                completed,
                role=role,
            )
        if opportunity is not None:
            return SchedulingDecision(
                opportunity_result=self.run_local_opportunity(opportunity)
            )

        candidates, focus_reason, role_complete = role_focused_candidates(
            planner.runnable(),
            planner.objectives,
            completed,
            role,
        )
        objective = planner.select(
            candidates,
            utility=lambda candidate: self.objective_score(candidate, signals),
        )
        if objective is None:
            reasons = (focus_reason,) if focus_reason else ()
            return SchedulingDecision(reasons=reasons, role_hold=role_complete)
        self.record_decision(objective, signals)
        contribution = score_phase(objective.phase, signals)
        reasons = contribution.reasons or ("stable graph fallback",)
        if focus_reason:
            reasons = (focus_reason, *reasons)
        return SchedulingDecision(
            objective=objective,
            score=self.objective_score(objective, signals),
            reasons=reasons,
        )

    @staticmethod
    def objective_score(objective: Objective, signals: GameSignals) -> float:
        """Blend stable graph priority with a stronger live opportunity score."""
        return float(objective.priority * 10) + score_phase(objective.phase, signals).value

    def record_decision(self, objective: Objective, signals: GameSignals) -> None:
        contribution = score_phase(objective.phase, signals)
        runtime = self._runtime()
        runtime["last_decision"] = {
            "phase": objective.phase.name,
            "fleet_role": fleet_role(self.state).value,
            "score": self.objective_score(objective, signals),
            "signals": list(contribution.reasons) or ["stable graph fallback"],
            "timestamp": time.time(),
        }

    def select_local_opportunity(
        self,
        signals: GameSignals,
        completed: Sequence[Phase],
        *,
        now: Optional[float] = None,
        role: Optional[FleetRole] = None,
    ) -> Optional[LocalOpportunity]:
        """Choose one safe renewable-resource action, if any is useful now."""
        if Phase.SPAWN_BOOTSTRAP not in set(completed):
            return None
        current_time = time.time() if now is None else float(now)
        role = FleetRole.BALANCED if role is None else role
        if role is FleetRole.IRON_SUPPLY:
            if (recovery := food_opportunity.select_food_recovery_opportunity(signals.food, self._cooldown_ready(OpportunityKind.FOOD_RECOVERY, current_time))) is not None: return recovery
            if (
                iron_cycle_ready(
                    signals,
                    worker_bootstrapped=Phase.INITIAL_GATHERING in set(completed),
                )
                and self._cooldown_ready(OpportunityKind.IRON_MINE, current_time)
            ):
                return LocalOpportunity(
                    OpportunityKind.IRON_MINE,
                    200,
                    "the dedicated iron supplier can mine, smelt, and bank a bounded batch",
                )
            return None
        if role is FleetRole.VILLAGE_FOOD:
            recovery = food_opportunity.select_food_recovery_opportunity(
                signals.food,
                self._cooldown_ready(OpportunityKind.FOOD_RECOVERY, current_time),
            )
            if recovery is not None:
                return recovery
            if not signals.safe_for_local_work:
                return None
            return food_opportunity.select_village_food_production_opportunity(
                signals.food,
                completed,
                self._cooldown_ready(OpportunityKind.FOOD_PRODUCTION, current_time),
            )
        if not signals.safe_for_local_work:
            return None
        if role is FleetRole.WOOD_SUPPLY:
            if (
                Phase.BOOT_SEQUENCE in set(completed)
                and self._cooldown_ready(OpportunityKind.WOOD_FARM, current_time)
            ):
                return LocalOpportunity(
                    OpportunityKind.WOOD_FARM,
                    200,
                    "the dedicated wood supplier can harvest, replant, and bank a bounded batch",
                )
            return None
        candidates = []

        pairs = signals.animal_pairs()
        for family, pair_count in pairs.items():
            if signals.adult_animals.get(family, 0) >= 8:
                continue
            if self._cooldown_ready(OpportunityKind.ANIMAL_FARM, current_time):
                leather_bonus = 20 if family in LEATHER_ANIMALS else 0
                candidates.append(
                    LocalOpportunity(
                        OpportunityKind.ANIMAL_FARM,
                        100 + pair_count * 5 + leather_bonus,
                        f"{signals.adult_animals[family]} adult {family} and breeding food nearby",
                        animal_type=family,
                    )
                )

        crop_location = food_opportunity.reachable_farm_location(signals)
        crop_reserve = sum(signals.count(item) for item in CROP_ITEMS)
        plantable = sum(signals.count(item) for item in PLANTABLE_ITEMS)
        crop_useful = crop_reserve < 32 or signals.count("minecraft:wheat") < 18
        if (
            crop_location is not None
            and crop_useful
            and (signals.crop_location is not None or plantable > 0)
            and self._cooldown_ready(OpportunityKind.CROP_FARM, current_time)
        ):
            candidates.append(
                LocalOpportunity(
                    OpportunityKind.CROP_FARM,
                    80 + max(0, 32 - crop_reserve),
                    "a nearby crop patch can replenish renewable food",
                    location=crop_location,
                )
            )

        if not candidates:
            return None
        return max(candidates, key=lambda candidate: candidate.score)

    def run_local_opportunity(
        self,
        opportunity: LocalOpportunity,
        *,
        crop_timeout: float = 45.0,
    ) -> OpportunityResult:
        """Execute and record exactly one bounded local opportunity."""
        try:
            if opportunity.kind is OpportunityKind.ANIMAL_FARM:
                before_signals = self.observe()
                before = int(
                    before_signals.animals.get(
                        opportunity.animal_type,
                        before_signals.adult_animals.get(opportunity.animal_type, 0),
                    )
                )
                success = breed_pair(
                    self.client,
                    opportunity.animal_type,
                    radius=16,
                )
                after_signals = self.observe()
                after = int(
                    after_signals.animals.get(
                        opportunity.animal_type,
                        after_signals.adult_animals.get(opportunity.animal_type, 0),
                    )
                )
                result = OpportunityResult(
                    opportunity,
                    bool(success and after > before),
                    "new offspring observed" if success and after > before else "herd did not grow",
                    before,
                    after,
                )
            elif opportunity.kind is OpportunityKind.CROP_FARM:
                result = self._run_crop_opportunity(opportunity, crop_timeout)
            elif opportunity.kind is OpportunityKind.WOOD_FARM:
                before_total = int(
                    self._runtime().get("wood_logs_banked", 0) or 0
                )
                cycle = run_wood_cycle(self.client, self.state)
                after_total = max(before_total, int(cycle.total_logs_banked or 0))
                self._runtime()["wood_logs_banked"] = after_total
                result = OpportunityResult(
                    opportunity,
                    cycle.success,
                    cycle.detail,
                    before_total,
                    after_total,
                )
            elif opportunity.kind is OpportunityKind.FOOD_RECOVERY:
                result = OpportunityResult(opportunity, *food_opportunity.run_scheduled_food_recovery(self.client, self.state))
            elif opportunity.kind is OpportunityKind.FOOD_PRODUCTION:
                result = OpportunityResult(
                    opportunity,
                    *food_opportunity.run_village_food_production(
                        self.client, self.state, self._runtime()
                    ),
                )
                self._runtime()["food_banked"] = result.after
            else:
                success, detail, before_total, after_total = run_scheduled_iron_cycle(
                    self.client, self.state, self._runtime()
                )
                result = OpportunityResult(
                    opportunity,
                    success,
                    detail,
                    before_total,
                    after_total,
                )
        except Exception as exc:
            result = OpportunityResult(
                opportunity,
                False,
                f"{type(exc).__name__}: {exc}",
            )
        self._record_opportunity_result(result)
        return result

    def _run_crop_opportunity(
        self,
        opportunity: LocalOpportunity,
        timeout: float,
    ) -> OpportunityResult:
        location = opportunity.location
        if location is None or not goto(
            self.client,
            *location,
            timeout=90,
            tolerance=5.0,
        ):
            return OpportunityResult(opportunity, False, "crop patch was unreachable")

        before_inventory = get_inventory(self.client)
        before_produce = sum(int(before_inventory.get(item, 0) or 0) for item in CROP_ITEMS)
        before_plantable = sum(
            int(before_inventory.get(item, 0) or 0) for item in PLANTABLE_ITEMS
        )
        self.client.transport.dispatch(
            "farm",
            {
                "range": 8,
                "x": location[0],
                "y": location[1],
                "z": location[2],
                "replant": True,
            },
        )

        deadline = time.monotonic() + max(1.0, float(timeout))
        after_produce = before_produce
        after_plantable = before_plantable
        try:
            while time.monotonic() < deadline:
                time.sleep(min(2.0, max(0.05, float(timeout))))
                current = get_inventory(self.client)
                after_produce = sum(
                    int(current.get(item, 0) or 0) for item in CROP_ITEMS
                )
                after_plantable = sum(
                    int(current.get(item, 0) or 0) for item in PLANTABLE_ITEMS
                )
                if after_produce > before_produce:
                    return OpportunityResult(
                        opportunity,
                        True,
                        "crop produce increased",
                        before_produce,
                        after_produce,
                    )
                if after_plantable < before_plantable and find_nearby_block(
                    self.client,
                    list(CROP_BLOCKS),
                    radius=12,
                ):
                    return OpportunityResult(
                        opportunity,
                        True,
                        "planting was verified in the world",
                        before_plantable,
                        after_plantable,
                    )
        finally:
            try:
                self.client.transport.dispatch("cancel", {})
            except Exception:
                pass
        return OpportunityResult(
            opportunity,
            False,
            "no harvest or planting change was observed",
            before_produce,
            after_produce,
        )

    def _runtime(self) -> Dict[str, Any]:
        custom = getattr(self.state, "custom_data", None)
        if not isinstance(custom, dict):
            custom = {}
            self.state.custom_data = custom
        runtime = custom.setdefault("adaptive_scheduler", {})
        if not isinstance(runtime, dict):
            runtime = {}
            custom["adaptive_scheduler"] = runtime
        return runtime
    def _cooldown_ready(self, kind: OpportunityKind, now: float) -> bool: return food_opportunity.cooldown_ready(self._runtime(), kind, now, self._COOLDOWNS)

    def _record_opportunity_result(self, result: OpportunityResult) -> None:
        runtime = self._runtime()
        opportunities = runtime.setdefault("opportunities", {})
        if not isinstance(opportunities, dict):
            opportunities = {}
            runtime["opportunities"] = opportunities
        opportunities[result.opportunity.kind.value] = {
            "last_attempt": time.time(),
            "success": result.success,
            "detail": result.detail,
            "before": result.before,
            "after": result.after,
            "target": result.opportunity.animal_type
            or list(result.opportunity.location or ()),
        }


__all__ = [
    "AdaptiveScheduler",
    "GameSignals",
    "LocalOpportunity",
    "OpportunityKind",
    "OpportunityResult",
    "PhaseScore",
    "SchedulingDecision",
    "collect_game_signals",
    "score_phase",
]
