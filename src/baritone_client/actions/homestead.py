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
from ..common.surface_recovery import reach_dry_surface
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
# When enforce_anchor() cannot path back to the homestead (e.g. the bot
# respawned at world origin far below a cliff-side base Baritone cannot
# re-ascend), we flag the old anchor as unreachable so run_dry_anchor()
# abandons it and re-homes to a reachable spot instead of holding forever
# on "could not complete bounded return to the homestead".
ANCHOR_UNREACHABLE = "anchor_unreachable"
# Upper bound on re-home attempts for one checkpoint. Guard against an
# infinite reselect loop if every candidate anchor is also unreachable.
MAX_ANCHOR_REHOME_ATTEMPTS = 2
from .homestead_site import carry_site_state  # noqa: E402
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
        # Carry the reachability/re-home state through reloads so an anchor
        # flagged unreachable by enforce_anchor() (see ANCHOR_UNREACHABLE)
        # survives the load() rebuild instead of being discarded and re-pinning
        # the phase to an unpathable home forever.
        if raw.get(ANCHOR_UNREACHABLE):
            progress[ANCHOR_UNREACHABLE] = True
            progress["anchor_unreachable_at"] = self._coordinate(
                raw.get("anchor_unreachable_at")
            )
        rehome_attempts = raw.get("rehome_attempts")
        if rehome_attempts:
            progress["rehome_attempts"] = int(rehome_attempts)
        # Same carry-through requirement as the re-home state above: load()
        # rebuilds from a fixed key list, so unnamed keys are silently dropped
        # and the stall counter could never reach its threshold.
        carry_site_state(progress, raw)
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
        """Return the first unverified, non-degraded improvement in stable order.

        A step marked ``degraded`` was waived after the site proved it could
        never satisfy it (e.g. micro_farm on a barren mountain); it must not
        block onward progress toward the dragon, so it is treated as done.
        """
        for name in ORDERED_HOMESTEAD_STEPS:
            record = self.step(homestead, name)
            if record.get("degraded"):
                continue
            if not record.get("verified"):
                return name
        return None

    def invalidate_stale(self, homestead: dict[str, Any]) -> None:
        """Fail closed when checkpoint claims no longer match the live world.

        Degraded steps are excluded: their live check is expected to fail
        (that is why the site's inability to satisfy them waived them), so
        re-opening them would deadlock the run again.
        """
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
            "micro_farm": self._live_farm(anchor),
            "light_perimeter": lighting_live,
        }
        for name, live in checks.items():
            record = self.step(homestead, name)
            if record.get("degraded"):
                continue
            if record.get("verified") and not live:
                record["verified"] = False

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

    def ordered_steps(self) -> tuple[str, ...]:
        """The canonical step order, for collaborators that re-open steps."""
        return ORDERED_HOMESTEAD_STEPS

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
        # Name the condition that actually failed. This message read "wait for
        # daylight survival margin" no matter which of the three checks
        # tripped, which made it both undiagnosable from logs and invisible to
        # the executor's hunger-hold recovery (that matches on food/hunger
        # wording). Live 2026-07-31: Bot13 and Bot17 yielded here 357 times on
        # food<16 while the log claimed they were waiting for daylight, and no
        # food acquisition ever ran.
        reasons = []
        if int(state.get("world_time", 0)) % 24000 >= 12000:
            reasons.append("waiting for daylight")
        if health < 18:
            reasons.append(f"health {health:.0f} below 18")
        if food < 16:
            reasons.append(f"food {food} below 16")
        if reasons:
            raise PacingHoldRequired(
                "survival margin before returning home: " + ", ".join(reasons)
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
            # The route to the current anchor cannot be completed -- very commonly
            # because the bot respawned away from a cliff-side base and Baritone
            # cannot path up the face. Rather than flag-and-raise (which the caller
            # may swallow and never re-run dry_anchor, since dry_anchor is already
            # verified and next_step() moves past it), re-home here in place: adopt
            # the bot's current reachable position as the new dry anchor. This is
            # the choke point every homestead step funnels through, so re-homing
            # here covers all steps and breaks the "bounded return" deadlock.
            rehome_attempts = int(homestead.get("rehome_attempts", 0) or 0)
            if rehome_attempts >= MAX_ANCHOR_REHOME_ATTEMPTS:
                raise ProgressRecoveryRequired(
                    "re-home exhausted after "
                    f"{rehome_attempts} attempt(s); no reachable dry anchor found"
                )
            current = self.current_position()
            if not self._dry_ground(current):
                # Standing on non-dry ground (water/lava). Re-home should
                # actively move the bot to dry land rather than passively
                # refusing and waiting for it to swim out -- otherwise a bot
                # pinned in water near a submerged anchor burns recovery
                # budget until circumstance happens to rescue it (Jason
                # 2026-08-08: "I would hope the bot would rehome as needed").
                dry = reach_dry_surface(
                    self.client,
                    origin=tuple(int(v) for v in current),
                    expected_y=int(current[1]),
                    goto=goto,
                )
                if dry is None:
                    homestead[ANCHOR_UNREACHABLE] = True
                    homestead["anchor_unreachable_at"] = current
                    self._persist_anchor_unreachable(homestead, anchor)
                    raise ProgressRecoveryRequired(
                        "cannot re-home onto non-dry ground at current position"
                    )
                # The bot is now on dry shore; use it as the new anchor so a
                # later return home lands on solid ground, not open water.
                current = (int(dry[0]), int(dry[1]), int(dry[2]))
            # Re-home succeeded: the bot's current dry position becomes the
            # new anchor and is by construction reachable. Reset the counter
            # so it bounds *consecutive* failures, not lifetime re-homes --
            # otherwise two re-homes anywhere in the run permanently latch
            # every future goto failure into a fatal loop (live 2026-08-08:
            # BOOT_SEQUENCE spun on "re-home exhausted after 2 attempt(s)"
            # forever with rehome_attempts persisted at the 2 cap).
            homestead["rehome_attempts"] = 0
            homestead.pop(ANCHOR_UNREACHABLE, None)
            homestead.pop("anchor_unreachable_at", None)
            homestead["anchor"] = current
            self.state.custom_data["homestead_anchor"] = current
            stored = getattr(self.state, "custom_data", {}).get("homestead")
            if isinstance(stored, dict):
                stored["anchor"] = list(current)
                stored["rehome_attempts"] = 0
                stored.pop(ANCHOR_UNREACHABLE, None)
                stored.pop("anchor_unreachable_at", None)
            self.step(homestead, "dry_anchor").update(
                verified=True, evidence="rehomed_dry_anchor"
            )
            print(f"Re-homed homestead to reachable anchor {current} "
                  f"(budget reset)")
            return True
        arrived = self.current_position()
        if self._distance(arrived, anchor) > SAFE_RADIUS:
            # Route claims arrival but we are still outside the envelope --
            # treat as unreachable too so we re-home instead of deadlocking.
            homestead[ANCHOR_UNREACHABLE] = True
            homestead["anchor_unreachable_at"] = arrived
            self._persist_anchor_unreachable(homestead, anchor)
            raise ProgressRecoveryRequired(
                "return route ended outside the homestead envelope"
            )
        # Reached the anchor; clear any earlier unreachable mark (in-memory
        # and durable) so a later legitimate return home is not misread. Also
        # reset the re-home budget: a clean arrival proves the anchor is
        # reachable, so the counter should not carry over as a lifetime latch.
        homestead.pop(ANCHOR_UNREACHABLE, None)
        homestead.pop("anchor_unreachable_at", None)
        homestead["rehome_attempts"] = 0
        stored = getattr(self.state, "custom_data", {}).get("homestead")
        if isinstance(stored, dict):
            stored.pop(ANCHOR_UNREACHABLE, None)
            stored.pop("anchor_unreachable_at", None)
            stored["rehome_attempts"] = 0
        homestead["last_return_home"] = arrived
        return True

    def _persist_anchor_unreachable(
        self,
        homestead: dict[str, Any],
        anchor: Optional[list[int]],
    ) -> None:
        """Write the unreachable-anchor flag to the durable store directly.

        enforce_anchor() raises ProgressRecoveryRequired right after marking
        the anchor unreachable, which aborts the phase before the caller's
        record() persists the in-memory homestead. Without this, the flag is
        dropped on every phase re-entry (load() rebuilds from custom_data) and
        the re-home never fires. Writing through here guarantees it survives.
        """
        try:
            stored = getattr(self.state, "custom_data", {}).get("homestead")
            if not isinstance(stored, dict):
                stored = {}
                try:
                    self.state.custom_data["homestead"] = stored
                except Exception:
                    return
            stored[ANCHOR_UNREACHABLE] = True
            if anchor is not None:
                stored["anchor_unreachable_at"] = list(anchor)
        except Exception as exc:  # defensive; must not break navigation
            print(f"re-home: could not persist unreachable flag: {exc}")

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
        # Deliberately NOT requiring a carried snack on top of the food>=16
        # floor above. That extra condition was unsatisfiable in practice and
        # deadlocked the lead bot.
        #
        # It read `food < 20 and not self._has_edible()`. Hunger is almost
        # never exactly 20, so it effectively demanded carried food always --
        # while nothing in the loop acquires any in the 16..19 band, because
        # acquire_emergency_food returns immediately once hunger already meets
        # its target. Live 2026-07-31: Bot16, the furthest-along bot in the
        # fleet at 8/9 homestead steps, sat at food=19 and full health with an
        # empty larder, yielding "carry food or refill hunger before
        # construction" with zero recovery attempts, unable to satisfy a gate
        # nothing could satisfy for it.
        #
        # The food>=16 floor (which now eats carried food first) is the real
        # survival margin for a bounded, local build step. If a future step
        # genuinely needs a travel reserve, it should acquire one explicitly
        # rather than hold for one that never arrives.

    def run_dry_anchor(self, homestead: dict[str, Any]) -> bool:
        """Select or live-revalidate a dry local anchor."""
        record = self.step(homestead, "dry_anchor")
        anchor = self._coordinate(homestead.get("anchor"))
        # A previously-verified anchor is only reusable while it is BOTH dry
        # ground AND still reachable. Once enforce_anchor() flagged it
        # unreachable (see ANCHOR_UNREACHABLE), reusing it just deadlocks the
        # phase on an unpathable return -- fall through to re-home instead.
        if (
            anchor is not None
            and self._dry_ground(anchor)
            and not homestead.get(ANCHOR_UNREACHABLE)
        ):
            changed = not bool(record.get("verified"))
            record.update(verified=True, evidence="live_dry_anchor")
            homestead["anchor"] = anchor
            return changed

        state = self._state()
        if state.get("dimension") != "minecraft:overworld":
            raise SurvivalRecoveryRequired("dry anchor requires overworld")
        if int(state.get("world_time", 0)) % 24000 >= 12000:
            raise SurvivalRecoveryRequired("wait for daylight before selecting anchor")

        # Re-home when the old anchor is unreachable: adopt the bot's current
        # reachable position as the new dry anchor. This is how the bot
        # survives a spawn away from a cliff-side base instead of pinning to a
        # spot it can never return to. Bound the attempts to avoid reselecting
        # forever when every candidate is unreachable.
        unreachable = bool(homestead.get(ANCHOR_UNREACHABLE))
        rehome_attempts = int(homestead.get("rehome_attempts", 0) or 0)
        if unreachable:
            if rehome_attempts >= MAX_ANCHOR_REHOME_ATTEMPTS:
                raise ProgressRecoveryRequired(
                    "re-home exhausted after "
                    f"{rehome_attempts} attempt(s); no reachable dry anchor found"
                )
            current = self.current_position()
            if not self._dry_ground(current):
                # Prefer actively moving the bot to the nearest dry shore over
                # refusing in place (see enforce_anchor for the same logic).
                dry = reach_dry_surface(
                    self.client,
                    origin=tuple(int(v) for v in current),
                    expected_y=int(current[1]),
                    goto=goto,
                )
                if dry is None:
                    raise ProgressRecoveryRequired(
                        "cannot re-home onto non-dry ground at current position"
                    )
                current = (int(dry[0]), int(dry[1]), int(dry[2]))
            # Re-home succeeded: adopt the current dry position as the new
            # anchor and reset the budget so it bounds consecutive failures
            # only (see enforce_anchor re-home block for the same fix).
            homestead["rehome_attempts"] = 0
            homestead.pop(ANCHOR_UNREACHABLE, None)
            homestead.pop("anchor_unreachable_at", None)
            homestead["anchor"] = current
            self.state.custom_data["homestead_anchor"] = current
            record.update(verified=True, evidence="rehomed_dry_anchor")
            print(
                f"Re-homed homestead to reachable anchor {current} "
                f"(budget reset)"
            )
            return True

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
        anchor = homestead.get("anchor")
        if self._live_farm(anchor):
            changed = not bool(record.get("verified"))
            record.update(verified=True, evidence="live_crop")
            return changed
        record["verified"] = False
        if not self.plant_crops(self.client) or not self._live_farm(anchor):
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
        fresh = perimeter_ring(anchor)[:24]
        # Adopt the current lighting policy even when a denser ring is already
        # stored. perimeter_ring used to emit every perimeter block (32
        # positions, shoulder to shoulder); it now samples them. Without this,
        # a bot that recorded the old dense ring keeps chasing ~24 torches
        # forever -- Bot16 was still on a stored 24-position ring after the
        # spacing fix shipped, so the change reached no existing bot. Torches
        # already placed stay placed and simply re-verify against the smaller
        # set.
        used_fresh = not intended or len(intended) > len(fresh)
        if used_fresh:
            intended = fresh
        # perimeter_ring puts every position at anchor_y+1, which only works on
        # perfectly flat ground. On real terrain most of the ring floats in
        # mid-air, and a torch needs solid support -- so robust_place fails
        # forever and light_perimeter can never verify. Live 2026-07-31: Bot16
        # had reached the last of nine homestead steps with 4 torches in hand,
        # and five of its first six ring targets had air directly beneath
        # them. Re-seat each column onto its actual local surface. Idempotent:
        # a position already sitting on ground is returned unchanged.
        #
        # Rebase every column to anchor height before re-seating. The scan
        # window is relative to the height it is handed, so feeding it a
        # stored position re-derives that position from itself and any bad
        # height becomes permanent: a torch once seated on a cave roof or
        # cliff lip can never come back down to the floor, even after the
        # anchor moves. Live 2026-08-01: Bot18 carried a y=114 column against
        # a y=105 anchor -- seated while the anchor was still y=107, and 8
        # blocks above the floor is far outside the ~4.5 block reach, so
        # Minecraft rejected every placement and light_perimeter (its last
        # homestead step) retried forever. Anchor-relative rebasing is a
        # no-op for a correctly seated ring, since re-seating finds the same
        # ground again.
        intended = [(x, anchor[1] + 1, z) for x, _y, z in intended]
        seated = self._ground_adjusted_ring(intended)
        if not seated and not used_fresh:
            # Every stored column failed to seat, which means the ring belongs
            # somewhere else -- typically a site abandoned by a relocation,
            # whose X/Z are far enough away that those chunks read as air.
            # Re-derive around the current anchor instead of giving up.
            #
            # Without this the step can never recover: the fallback to `fresh`
            # above happens before seating, and this branch used to return
            # without writing anything back, so the unusable ring was reloaded
            # and re-failed every cycle forever. Live on the A1 server
            # 2026-08-10, at 8 of 9 steps done, the ring still pointed ~110
            # blocks away at (-245, 72, 12) against an anchor of
            # (-352, 52, 93).
            print("  Perimeter ring does not fit this site; re-deriving around the anchor")
            seated = self._ground_adjusted_ring(
                [(x, anchor[1] + 1, z) for x, _y, z in fresh]
            )
        intended = seated
        if not intended:
            record["verified"] = False
            return False
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
            # Re-open torch_supply whenever the torches are gone, not only
            # when the fuel is gone too. place_torch needs a stick as well as
            # fuel and will not craft one, so a bot holding fuel but no sticks
            # could never resupply and never finish the ring. Live 2026-07-31:
            # Bot16 reached 16 of 24 perimeter torches -- the last step of
            # nine -- then stalled with 0 torches, 0 sticks, 2 charcoal and 8
            # planks, because fuel>=1 kept torch_supply marked verified.
            # run_torch_supply crafts the sticks first, so simply letting it
            # run again resolves this.
            if count_item(self.client, "minecraft:torch") < 1:
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

    def _live_farm(self, anchor: Any = None) -> bool:
        """Is there a live crop plot belonging to *this* homestead?

        A plot only counts if it is inside the local envelope.
        `_live_infrastructure` already enforces SAFE_RADIUS; without the same
        rule here, a farm left behind at an abandoned site keeps satisfying
        micro_farm from arbitrarily far away.

        Worse than merely wrong, it oscillates. A distant farm sits near the
        server's simulation-distance boundary, so `get_block` reports a crop
        while that chunk happens to be loaded and air while it is not. Live on
        the A1 server 2026-08-10: the anchor had relocated 22 times and ended
        ~90 blocks from its original farm, against simulation-distance=6 (96
        blocks). BOOT_SEQUENCE then ping-ponged forever -- invalidate_stale
        un-verified micro_farm on an unloaded read, run_micro_farm re-verified
        it on a loaded one and reported incremental progress, every ~30s. The
        run never advanced, and because the step always "progressed" it never
        stalled long enough to relocate or to be waived either.
        """
        anchor_coord = self._coordinate(anchor)
        structures = self.state.custom_data.get("structures", {})
        record = structures.get("food_source", {}) if isinstance(structures, Mapping) else {}
        plots = record.get("plots", []) if isinstance(record, Mapping) else []
        for plot in plots:
            position = self._coordinate(plot)
            if position is None:
                continue
            if (
                anchor_coord is not None
                and self._distance(position, anchor_coord) > SAFE_RADIUS
            ):
                continue
            if self._block_at(position) in CROP_BLOCKS:
                return True
        return False

    # Exact names, not substrings: "grass_block" is solid ground while
    # "short_grass" is a plant, and a substring test on "grass" rejects both.
    _UNSUPPORTIVE_NAMES = frozenset(
        {
            "air",
            "cave_air",
            "void_air",
            "water",
            "lava",
            "short_grass",
            "tall_grass",
            "fern",
            "large_fern",
            "dead_bush",
            "seagrass",
            "tall_seagrass",
            "kelp",
            "kelp_plant",
            "vine",
            "snow",
            "torch",
            "wall_torch",
            "soul_torch",
        }
    )
    # Anything a torch cannot displace when placed into the target slot.
    _NON_REPLACEABLE_EXTRA = frozenset({"water", "lava"})

    @classmethod
    def _is_supportive(cls, block_id: str) -> bool:
        if not block_id:
            return False
        name = block_id.split(":")[-1]
        # Java disallows standing torches on leaves.  Treat every leaf
        # variant as non-supportive so a ring column continues down to the
        # actual terrain instead of retrying the same rejected placement.
        if name in cls._UNSUPPORTIVE_NAMES or name.endswith("_leaves"):
            return False
        return not (name.endswith("_sapling") or name.endswith("_sign"))

    @classmethod
    def _is_replaceable(cls, block_id: str) -> bool:
        if not block_id:
            return False
        name = block_id.split(":")[-1]
        if name in cls._NON_REPLACEABLE_EXTRA:
            return False
        return name in cls._UNSUPPORTIVE_NAMES

    def _ground_adjusted_ring(
        self,
        positions: Sequence[Sequence[int]],
        *,
        search: int = 6,
    ) -> list[tuple[int, int, int]]:
        """Re-seat each ring column onto the first solid block beneath it.

        Scans a bounded window up and down from the nominal height so a ring
        laid over a slope still resolves. Columns with no solid support in
        range are dropped rather than kept as unplaceable targets that would
        block the step forever.
        """
        adjusted: list[tuple[int, int, int]] = []
        for position in positions:
            x, y, z = (int(value) for value in position)
            placed = None
            for candidate_y in range(y + search, y - search - 1, -1):
                if not self._is_supportive(self._block_at((x, candidate_y - 1, z))):
                    continue
                if not self._is_replaceable(self._block_at((x, candidate_y, z))):
                    continue
                placed = (x, candidate_y, z)
                break
            if placed is not None and placed not in adjusted:
                adjusted.append(placed)
        return adjusted

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
