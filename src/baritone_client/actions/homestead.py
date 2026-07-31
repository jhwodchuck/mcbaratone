"""Small, live-verified homestead improvements for the BOOT phase."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Optional

from ..automator.state_manager import Phase
from ..common.base import place_torch, setup_base
from ..common.homestead_lighting import perimeter_ring, partition_light_coordinates
from ..common.inventory import count_item, craft
from ..common.navigation import goto
from ..common.resources import gather_stone, gather_wood
from ..common.tasks import (
    PacingHoldRequired,
    ProgressRecoveryRequired,
    SurvivalRecoveryRequired,
)


ORDERED_HOMESTEAD_STEPS = (
    "dry_anchor",
    "wood_reserve",
    "plank_reserve",
    "stone_reserve",
    "infrastructure",
    "micro_farm",
    "charcoal_supply",
    "torch_supply",
    "light_perimeter",
)
SAFE_RADIUS = 24.0
LOG_ITEMS = (
    "minecraft:oak_log",
    "minecraft:birch_log",
    "minecraft:spruce_log",
    "minecraft:dark_oak_log",
    "minecraft:acacia_log",
    "minecraft:jungle_log",
    "minecraft:mangrove_log",
    "minecraft:cherry_log",
)
PLANK_ITEMS = (
    "minecraft:oak_planks",
    "minecraft:birch_planks",
    "minecraft:spruce_planks",
    "minecraft:dark_oak_planks",
    "minecraft:acacia_planks",
    "minecraft:jungle_planks",
    "minecraft:mangrove_planks",
    "minecraft:cherry_planks",
)
CROP_BLOCKS = {
    "minecraft:wheat",
    "minecraft:carrots",
    "minecraft:potatoes",
    "minecraft:beetroots",
}


class IncrementalHomestead:
    """Execute one bounded, checkpointed BOOT improvement per invocation."""

    def __init__(
        self,
        client: Any,
        state: Any,
        plant_crops: Callable[[Any], bool],
    ) -> None:
        self.client = client
        self.state = state
        self.plant_crops = plant_crops

    def load(self) -> dict[str, Any]:
        """Normalize durable progress while retaining evidence coordinates."""
        raw = getattr(self.state, "custom_data", {}).get("homestead", {})
        raw = raw if isinstance(raw, Mapping) else {}
        existing = raw.get("steps", {})
        if isinstance(existing, list):
            existing = {
                str(item.get("name")): item
                for item in existing
                if isinstance(item, Mapping)
            }
        existing = existing if isinstance(existing, Mapping) else {}
        progress: dict[str, Any] = {
            "anchor": self._coordinate(raw.get("anchor")),
            "last_return_home": self._coordinate(raw.get("last_return_home")),
            "ordered_steps": list(ORDERED_HOMESTEAD_STEPS),
            "steps": {},
        }
        for name in ORDERED_HOMESTEAD_STEPS:
            record = existing.get(name, {})
            record = record if isinstance(record, Mapping) else {}
            progress["steps"][name] = {
                "verified": bool(record.get("verified")),
                "intended": record.get("intended"),
                "verified_positions": record.get("verified_positions"),
                "evidence": record.get("evidence"),
            }
        if progress["anchor"] is None:
            progress["anchor"] = self._coordinate(
                getattr(self.state, "custom_data", {}).get("homestead_anchor")
            )
        return progress

    def next_step(self, homestead: dict[str, Any]) -> Optional[str]:
        """Return the first unverified improvement in stable order."""
        for name in ORDERED_HOMESTEAD_STEPS:
            if not self.step(homestead, name).get("verified"):
                return name
        return None

    def invalidate_stale(self, homestead: dict[str, Any]) -> None:
        """Fail closed when checkpoint claims no longer match the live world."""
        anchor = self._coordinate(homestead.get("anchor"))
        lighting_live = self._live_lighting(
            self.step(homestead, "light_perimeter")
        )
        checks = {
            "dry_anchor": anchor is not None and self._dry_ground(anchor),
            "infrastructure": self._live_infrastructure(
                self._infrastructure_record(anchor),
                anchor,
            ),
            "micro_farm": self._live_farm(),
            "light_perimeter": lighting_live,
        }
        for name, live in checks.items():
            if self.step(homestead, name).get("verified") and not live:
                self.step(homestead, name)["verified"] = False

    def step(self, homestead: dict[str, Any], name: str) -> dict[str, Any]:
        steps = homestead.setdefault("steps", {})
        if not isinstance(steps, dict):
            steps = {}
            homestead["steps"] = steps
        record = steps.setdefault(name, {})
        if not isinstance(record, dict):
            record = {}
            steps[name] = record
        return record

    def record(self, homestead: dict[str, Any]) -> None:
        """Persist one normalized snapshot after verified improvement."""
        homestead["updated_at"] = time.time()
        custom_data = self.state.custom_data
        custom_data["homestead"] = homestead
        custom_data.setdefault("structures", {})["homestead"] = homestead
        completed = sum(
            bool(self.step(homestead, name).get("verified"))
            for name in ORDERED_HOMESTEAD_STEPS
        )
        self.state.record_phase_payload(
            Phase.BOOT_SEQUENCE,
            {
                "homestead_steps": completed,
                "completed_actions": completed,
                "sequence_result": "incremental-homestead",
                "homestead": homestead,
                "timestamp": homestead["updated_at"],
            },
        )

    def enforce_anchor(self, homestead: dict[str, Any]) -> bool:
        """Return safely to the local envelope before doing more work."""
        anchor = self._coordinate(homestead.get("anchor"))
        if anchor is None:
            return False
        current = self.current_position()
        if self._distance(current, anchor) <= SAFE_RADIUS:
            return False
        state = self._state()
        health = float(state.get("health", 0) or 0)
        food = int(state.get("food_level", state.get("food", 0)) or 0)
        if state.get("dimension") != "minecraft:overworld" or health < 12 or food <= 10:
            raise SurvivalRecoveryRequired(
                "critical survival recovery required before returning home"
            )
        # Same eat-before-holding rule as require_construction_pacing: this
        # gate was holding bots that were carrying food. Live 2026-07-31:
        # Bot15 sat at food=11 with a chicken and a beef in its inventory,
        # held here 249 times.
        if food < 16:
            food = self._eat_carried_food(minimum_food=18, current_food=food)
        if (
            int(state.get("world_time", 0)) % 24000 >= 12000
            or health < 18
            or food < 16
        ):
            raise PacingHoldRequired(
                "wait for daylight survival margin before returning home"
            )
        if not goto(
            self.client,
            anchor[0],
            anchor[1],
            anchor[2],
            timeout=180,
            check_interval=1.0,
            tolerance=4.0,
        ):
            raise ProgressRecoveryRequired(
                "could not complete bounded return to the homestead"
            )
        arrived = self.current_position()
        if self._distance(arrived, anchor) > SAFE_RADIUS:
            raise ProgressRecoveryRequired(
                "return route ended outside the homestead envelope"
            )
        homestead["last_return_home"] = arrived
        return True

    def require_construction_pacing(self) -> None:
        """Only build during a strong, daylight survival window."""
        state = self._state()
        if state.get("dimension") != "minecraft:overworld":
            raise SurvivalRecoveryRequired("return to overworld before construction")
        if int(state.get("world_time", 0)) % 24000 >= 12000:
            raise PacingHoldRequired("wait for daylight before construction")
        if float(state.get("health", 0) or 0) < 18:
            if float(state.get("health", 0) or 0) < 12:
                raise SurvivalRecoveryRequired("critical health before construction")
            raise PacingHoldRequired("health below 18 before construction")
        food = int(state.get("food_level", state.get("food", 0)) or 0)
        # Eat before holding. These gates are about *having* a survival
        # margin, and a bot carrying food already has one -- it just has not
        # consumed it. Yielding instead of eating deadlocks: nothing else in
        # the loop eats at these levels, so the hold repeats forever.
        # Measured live 2026-07-31 across an 18-bot fleet: not one bot had
        # ever completed BOOT_SEQUENCE, and every one was cycling
        # "yielded to pacing_hold; recovery budget 0/6". Bot12 was held on
        # food=15 while carrying 6 mutton and 1 chicken.
        if food < 20:
            food = self._eat_carried_food(minimum_food=18, current_food=food)
        if food < 16:
            if food <= 10:
                raise SurvivalRecoveryRequired("critical hunger before construction")
            raise PacingHoldRequired("food below 16 before construction")
        if food < 20 and not self._has_edible():
            raise PacingHoldRequired(
                "carry food or refill hunger before construction"
            )

    def run_dry_anchor(self, homestead: dict[str, Any]) -> bool:
        """Select or live-revalidate a dry local anchor."""
        record = self.step(homestead, "dry_anchor")
        anchor = self._coordinate(homestead.get("anchor"))
        if anchor is not None and self._dry_ground(anchor):
            changed = not bool(record.get("verified"))
            record.update(verified=True, evidence="live_dry_anchor")
            homestead["anchor"] = anchor
            return changed

        state = self._state()
        if state.get("dimension") != "minecraft:overworld":
            raise SurvivalRecoveryRequired("dry anchor requires overworld")
        if int(state.get("world_time", 0)) % 24000 >= 12000:
            raise SurvivalRecoveryRequired("wait for daylight before selecting anchor")
        anchor = self._initial_homestead_anchor(homestead)
        if not self._dry_ground(anchor):
            record["verified"] = False
            raise ProgressRecoveryRequired("dry anchor requires non-liquid ground")
        homestead["anchor"] = anchor
        self.state.custom_data["homestead_anchor"] = anchor
        record.update(verified=True, evidence="live_dry_anchor")
        return True

    def _initial_homestead_anchor(self, homestead: dict[str, Any]) -> list[int]:
        """Return to the bootstrap home before adopting a first homestead."""
        bootstrap_home = self._bootstrap_home()
        if bootstrap_home is None:
            return self.current_position()
        homestead["anchor"] = bootstrap_home
        self.enforce_anchor(homestead)
        return self.current_position()

    def _bootstrap_home(self) -> Optional[list[int]]:
        """Read the durable SPAWN_BOOTSTRAP return-home coordinate."""
        payload = {}
        get_payload = getattr(self.state, "get_phase_payload", None)
        if callable(get_payload):
            candidate = get_payload(Phase.SPAWN_BOOTSTRAP)
            if isinstance(candidate, Mapping):
                payload = candidate
        if not payload:
            payloads = getattr(self.state, "custom_data", {}).get(
                "phase_payloads",
                {},
            )
            if isinstance(payloads, Mapping):
                candidate = payloads.get("SPAWN_BOOTSTRAP", {})
                if isinstance(candidate, Mapping):
                    payload = candidate
        return_home = payload.get("return_home", {})
        if not isinstance(return_home, Mapping):
            return None
        return self._coordinate(return_home.get("origin"))

    def run_wood_reserve(self, homestead: dict[str, Any]) -> bool:
        """Gather only the local wood needed for starter workstations."""
        if self.enforce_anchor(homestead):
            return True
        self.require_construction_pacing()
        record = self.step(homestead, "wood_reserve")
        before = self._wood_equivalents()
        if before >= 5:
            changed = not bool(record.get("verified"))
            record.update(verified=True, evidence={"log_equivalents": before})
            return changed
        gather_wood(
            self.client,
            count=5,
            timeout=180,
            latest_world_time=11500,
            max_distance_from_origin=SAFE_RADIUS,
            abort_on_threats=True,
            minimum_health=18.0,
        )
        after = self._wood_equivalents()
        record.update(
            verified=after >= 5,
            evidence={"log_equivalents": after},
        )
        return after > before

    def run_stone_reserve(self, homestead: dict[str, Any]) -> bool:
        """Gather one furnace worth of stone before placing infrastructure."""
        if self.enforce_anchor(homestead):
            return True
        self.require_construction_pacing()
        record = self.step(homestead, "stone_reserve")
        before = count_item(self.client, "minecraft:cobblestone")
        if before >= 8:
            changed = not bool(record.get("verified"))
            record.update(verified=True, evidence={"cobblestone": before})
            return changed
        gather_stone(self.client, count=8, timeout=180)
        after = count_item(self.client, "minecraft:cobblestone")
        record.update(
            verified=after >= 8,
            evidence={"cobblestone": after},
        )
        return after > before

    def run_plank_reserve(self, homestead: dict[str, Any]) -> bool:
        """Convert local logs into the exact workstation plank reserve."""
        if self.enforce_anchor(homestead):
            return True
        self.require_construction_pacing()
        record = self.step(homestead, "plank_reserve")
        before = sum(count_item(self.client, item) for item in PLANK_ITEMS)
        if before >= 16:
            changed = not bool(record.get("verified"))
            record.update(verified=True, evidence={"planks": before})
            return changed
        log_item = next(
            (item for item in LOG_ITEMS if count_item(self.client, item) > 0),
            None,
        )
        if log_item is None:
            self.step(homestead, "wood_reserve")["verified"] = False
            record.update(verified=False, evidence={"planks": before})
            return True
        plank_item = PLANK_ITEMS[LOG_ITEMS.index(log_item)]
        craft(self.client, plank_item, 16 - before)
        after = sum(count_item(self.client, item) for item in PLANK_ITEMS)
        record.update(verified=after >= 16, evidence={"planks": after})
        return after > before

    def run_infrastructure(self, homestead: dict[str, Any]) -> bool:
        """Re-probe or place a compact table, furnace, and chest near anchor."""
        if self.enforce_anchor(homestead):
            return True
        self.require_construction_pacing()
        record = self.step(homestead, "infrastructure")
        existing = self._infrastructure_record(homestead.get("anchor"))
        if self._live_infrastructure(existing, homestead.get("anchor")):
            changed = not bool(record.get("verified"))
            record.update(verified=True, evidence="live_infrastructure")
            return changed

        record["verified"] = False
        planks = sum(count_item(self.client, item) for item in PLANK_ITEMS)
        if planks < 12:
            self.step(homestead, "plank_reserve")["verified"] = False
            record["evidence"] = {"missing_planks": 12 - planks}
            return True
        cobblestone = count_item(self.client, "minecraft:cobblestone")
        furnace = count_item(self.client, "minecraft:furnace")
        if furnace < 1 and cobblestone < 8:
            self.step(homestead, "stone_reserve")["verified"] = False
            record["evidence"] = {"missing_cobblestone": 8 - cobblestone}
            return True
        success, location = setup_base(self.client)
        if not success or location is None:
            return False
        origin = [int(value) for value in location]
        if self._distance(origin, homestead["anchor"]) > SAFE_RADIUS:
            return False
        infrastructure = {
            "origin": origin,
            "crafting_table": [origin[0] + 1, origin[1], origin[2] + 1],
            "furnace": [origin[0] + 2, origin[1], origin[2] + 1],
            "supply_chest": [origin[0] + 1, origin[1], origin[2] + 2],
            "verified": True,
        }
        self.state.custom_data.setdefault("structures", {})[
            "bootstrap_base"
        ] = infrastructure
        if not self._live_infrastructure(infrastructure, homestead.get("anchor")):
            return False
        record.update(verified=True, evidence="live_infrastructure")
        return True

    def run_micro_farm(self, homestead: dict[str, Any]) -> bool:
        """Re-probe or add the smallest renewable crop plot."""
        if self.enforce_anchor(homestead):
            return True
        self.require_construction_pacing()
        record = self.step(homestead, "micro_farm")
        if self._live_farm():
            changed = not bool(record.get("verified"))
            record.update(verified=True, evidence="live_crop")
            return changed
        record["verified"] = False
        if not self.plant_crops(self.client) or not self._live_farm():
            return False
        record.update(verified=True, evidence="live_crop")
        return True

    def run_torch_supply(self, homestead: dict[str, Any]) -> bool:
        """Craft a small torch batch from carried, safely acquired fuel."""
        if self.enforce_anchor(homestead):
            return True
        self.require_construction_pacing()
        record = self.step(homestead, "torch_supply")
        before = count_item(self.client, "minecraft:torch")
        if before >= 4:
            changed = not bool(record.get("verified"))
            record.update(verified=True, evidence={"torches": before})
            return changed
        if count_item(self.client, "minecraft:stick") < 1:
            sticks_before = count_item(self.client, "minecraft:stick")
            craft(self.client, "minecraft:stick", 4)
            sticks_after = count_item(self.client, "minecraft:stick")
            if sticks_after > sticks_before:
                record.update(
                    verified=False,
                    evidence={"sticks": sticks_after, "torches": before},
                )
                return True
        fuel = count_item(self.client, "minecraft:coal") + count_item(
            self.client, "minecraft:charcoal"
        )
        if fuel < 1:
            record.update(verified=False, evidence={"torches": before, "fuel": 0})
            return False
        craft(self.client, "minecraft:torch", 4)
        after = count_item(self.client, "minecraft:torch")
        record.update(
            verified=after >= 4,
            evidence={"torches": after, "fuel": fuel},
        )
        return after > before

    def run_charcoal_supply(self, homestead: dict[str, Any]) -> bool:
        """Prepare one local torch-fuel unit without a cave expedition."""
        from ..common import harness_ops

        if self.enforce_anchor(homestead):
            return True
        self.require_construction_pacing()
        record = self.step(homestead, "charcoal_supply")
        coal = count_item(self.client, "minecraft:coal")
        charcoal_before = count_item(self.client, "minecraft:charcoal")
        if coal > 0 or charcoal_before > 0:
            changed = not bool(record.get("verified"))
            record.update(
                verified=True,
                evidence={"coal": coal, "charcoal": charcoal_before},
            )
            return changed

        log_item = next(
            (item for item in LOG_ITEMS if count_item(self.client, item) > 0),
            None,
        )
        plank_item = next(
            (item for item in PLANK_ITEMS if count_item(self.client, item) > 0),
            None,
        )
        if log_item is None or plank_item is None:
            before = self._wood_equivalents()
            if log_item is None:
                gather_wood(
                    self.client,
                    count=max(2, before + 1),
                    timeout=120,
                    latest_world_time=11500,
                    max_distance_from_origin=SAFE_RADIUS,
                    abort_on_threats=True,
                    minimum_health=18.0,
                )
                after = self._wood_equivalents()
                record.update(
                    verified=False,
                    evidence={"wood_equivalents": after},
                )
                return after > before
            if plank_item is None and log_item is not None:
                plank_item = PLANK_ITEMS[LOG_ITEMS.index(log_item)]
                before_planks = count_item(self.client, plank_item)
                craft(self.client, plank_item, 4)
                after_planks = count_item(self.client, plank_item)
                record.update(
                    verified=False,
                    evidence={"planks": after_planks},
                )
                return after_planks > before_planks
            return False

        infrastructure = self._infrastructure_record(homestead.get("anchor"))
        furnace = self._coordinate(infrastructure.get("furnace"))
        if furnace is None or self._block_at(furnace) not in {
            "minecraft:furnace",
            "minecraft:blast_furnace",
        }:
            record.update(verified=False, evidence="missing_live_furnace")
            return False
        try:
            smelted = harness_ops.smelt_in_furnace(
                self.client,
                tuple(furnace),
                log_item,
                plank_item,
                "minecraft:charcoal",
                output_count=1,
            )
        except Exception:
            smelted = False
        charcoal_after = count_item(self.client, "minecraft:charcoal")
        record.update(
            verified=bool(smelted and charcoal_after > charcoal_before),
            evidence={"charcoal": charcoal_after},
        )
        return charcoal_after > charcoal_before

    def run_light_perimeter(self, homestead: dict[str, Any]) -> bool:
        """Repair exactly one missing perimeter torch and verify it live."""
        if self.enforce_anchor(homestead):
            return True
        self.require_construction_pacing()
        record = self.step(homestead, "light_perimeter")
        anchor = self._coordinate(homestead.get("anchor"))
        if anchor is None:
            record["verified"] = False
            return False
        intended = self._coordinates(record.get("intended"))
        if not intended:
            intended = perimeter_ring(anchor)[:24]
        record["intended"] = [list(position) for position in intended]
        observed = [position for position in intended if self._is_torch(position)]
        missing, verified = partition_light_coordinates(intended, observed)
        record["verified_positions"] = [list(position) for position in verified]
        if not missing:
            changed = not bool(record.get("verified"))
            record.update(verified=True, evidence="live_perimeter")
            return changed

        record["verified"] = False
        target = missing[0]
        if not place_torch(self.client, *target):
            fuel = count_item(self.client, "minecraft:coal") + count_item(
                self.client, "minecraft:charcoal"
            )
            if count_item(self.client, "minecraft:torch") < 1 and fuel < 1:
                self.step(homestead, "torch_supply")["verified"] = False
            return False
        if not self._is_torch(target):
            return False
        observed = [position for position in intended if self._is_torch(position)]
        _, verified = partition_light_coordinates(intended, observed)
        record["verified_positions"] = [list(position) for position in verified]
        record["verified"] = len(verified) == len(intended)
        if record["verified"]:
            record["evidence"] = "live_perimeter"
        return True

    def current_position(self) -> list[int]:
        position = self._state().get("block_position", {})
        return [
            int(position.get("x", 0)),
            int(position.get("y", 64)),
            int(position.get("z", 0)),
        ]

    def _state(self) -> dict[str, Any]:
        return self.client.transport.dispatch("get_state", {})

    def _block_at(self, position: Sequence[int]) -> str:
        try:
            return str(
                self.client.transport.dispatch(
                    "get_block",
                    {"x": int(position[0]), "y": int(position[1]), "z": int(position[2])},
                ).get("id", "")
            )
        except Exception:
            return ""

    def _dry_ground(self, anchor: Sequence[int]) -> bool:
        ground = self._block_at((anchor[0], anchor[1] - 1, anchor[2]))
        return bool(ground) and "water" not in ground and "lava" not in ground

    def _has_edible(self) -> bool:
        # Raw meat and fish count. They restore less hunger than cooked, but a
        # bot carrying six raw mutton unambiguously has a survival margin --
        # and treating it as having none was half of the live deadlock (see
        # require_construction_pacing). Uses the same canonical list the
        # survival code eats from, so the two can never disagree about what
        # counts as food.
        from ..common.combat import EMERGENCY_FOOD_ITEMS

        return any(
            count_item(self.client, item) > 0 for item in EMERGENCY_FOOD_ITEMS
        )

    def _eat_carried_food(self, *, minimum_food: int, current_food: int) -> int:
        """Consume carried food, returning the resulting hunger level.

        Best-effort: a failure here must not break the pacing check, which
        will simply fall through to its normal hold.
        """
        try:
            from ..common.combat import eat_until_hunger

            eat_until_hunger(self.client, minimum_food=minimum_food)
            refreshed = self._state()
            return int(
                refreshed.get("food_level", refreshed.get("food", current_food))
                or current_food
            )
        except Exception as exc:  # bridge hiccup, nothing edible, etc.
            print(f"  Pacing: could not eat carried food ({exc})")
            return current_food

    def _wood_equivalents(self) -> int:
        logs = sum(count_item(self.client, item) for item in LOG_ITEMS)
        planks = sum(count_item(self.client, item) for item in PLANK_ITEMS)
        return logs + planks // 4

    def _infrastructure_record(self, anchor: Any = None) -> Mapping[str, Any]:
        structures = self.state.custom_data.get("structures", {})
        if not isinstance(structures, Mapping):
            return {}
        candidates = []
        anchor_coord = self._coordinate(anchor)
        for name in ("bootstrap_base", "starter_house", "house_7x7"):
            candidate = structures.get(name)
            if isinstance(candidate, Mapping) and candidate:
                origin = self._coordinate(candidate.get("origin"))
                distance = (
                    self._distance(origin, anchor_coord)
                    if origin is not None and anchor_coord is not None
                    else float("inf")
                )
                candidates.append((distance, candidate))
        if not candidates:
            return {}
        return min(candidates, key=lambda item: item[0])[1]

    def _live_infrastructure(
        self,
        record: Mapping[str, Any],
        anchor: Any,
    ) -> bool:
        origin = self._coordinate(record.get("origin"))
        anchor_coord = self._coordinate(anchor)
        if origin is None or anchor_coord is None:
            return False
        if self._distance(origin, anchor_coord) > SAFE_RADIUS:
            return False
        expected = {
            "crafting_table": {"minecraft:crafting_table"},
            "furnace": {"minecraft:furnace", "minecraft:blast_furnace"},
            "supply_chest": {"minecraft:chest", "minecraft:trapped_chest"},
        }
        for name, accepted in expected.items():
            position = self._coordinate(record.get(name))
            if position is None or self._block_at(position) not in accepted:
                return False
        return True

    def _live_farm(self) -> bool:
        structures = self.state.custom_data.get("structures", {})
        record = structures.get("food_source", {}) if isinstance(structures, Mapping) else {}
        plots = record.get("plots", []) if isinstance(record, Mapping) else []
        for plot in plots:
            position = self._coordinate(plot)
            if position is not None and self._block_at(position) in CROP_BLOCKS:
                return True
        return False

    def _is_torch(self, position: Sequence[int]) -> bool:
        return self._block_at(position) in {
            "minecraft:torch",
            "minecraft:wall_torch",
        }

    def _live_lighting(self, record: Mapping[str, Any]) -> bool:
        intended = self._coordinates(record.get("intended"))
        return bool(intended) and all(self._is_torch(position) for position in intended)

    @staticmethod
    def _coordinate(value: Any) -> Optional[list[int]]:
        if not isinstance(value, (list, tuple)) or len(value) < 3:
            return None
        try:
            return [int(value[0]), int(value[1]), int(value[2])]
        except (TypeError, ValueError):
            return None

    @classmethod
    def _coordinates(cls, values: Any) -> list[tuple[int, int, int]]:
        if not isinstance(values, list):
            return []
        result = []
        for value in values:
            coordinate = cls._coordinate(value)
            if coordinate is not None:
                result.append(tuple(coordinate))
        return result

    @staticmethod
    def _distance(left: Sequence[int], right: Sequence[int]) -> float:
        return (
            (int(left[0]) - int(right[0])) ** 2
            + (int(left[2]) - int(right[2])) ** 2
        ) ** 0.5
