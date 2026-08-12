"""Reusable survival policy for perpetual, verified road construction."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import os
from pathlib import Path
import time
from typing import Any, Mapping

from ..common.build_safety import has_build_survival_margin
from ..common.combat import defend_or_flee, eat_until_hunger
from ..common.harness_ops import move_near, place_block_exact
from ..common.inventory import get_inventory
from ..common.resources import gather_stone
from .perpetual_build_plan import LaneAssignment, ROUTES, fleet_lanes

OVERWORLD = "minecraft:overworld"
NON_OP_PROFILE = "non_op_client"
CRITICAL_HEALTH = 10.0
CRITICAL_FOOD = 3
MANUAL_RECOVERY_HEALTH = 12.0
MANUAL_RECOVERY_FOOD = 18
MAX_CELL_FAILURES = 3
MAX_NONPRODUCTIVE_CELLS = 12
BUILD_MATERIALS = (
    "minecraft:cobblestone",
    "minecraft:cobbled_deepslate",
    "minecraft:stone_bricks",
    "minecraft:end_stone",
    "minecraft:oak_planks",
    "minecraft:birch_planks",
    "minecraft:spruce_planks",
    "minecraft:dark_oak_planks",
    "minecraft:jungle_planks",
    "minecraft:acacia_planks",
    "minecraft:mangrove_planks",
    "minecraft:cherry_planks",
    "minecraft:dirt",
    "minecraft:coarse_dirt",
)
NATURAL_SUPPORTS = {
    "minecraft:grass_block",
    "minecraft:dirt",
    "minecraft:coarse_dirt",
    "minecraft:rooted_dirt",
    "minecraft:podzol",
    "minecraft:mycelium",
    "minecraft:stone",
    "minecraft:deepslate",
    "minecraft:granite",
    "minecraft:diorite",
    "minecraft:andesite",
    "minecraft:tuff",
    "minecraft:calcite",
    "minecraft:sand",
    "minecraft:red_sand",
    "minecraft:sandstone",
    "minecraft:red_sandstone",
    "minecraft:gravel",
    "minecraft:clay",
    "minecraft:terracotta",
}


@dataclass
class BuildProgress:
    """Durable construction cursor and evidence counters for one bot."""

    slot: int = 0
    expected_y: int = 75
    blocks_placed: int = 0
    materials_consumed: dict[str, int] = field(default_factory=dict)
    skipped_cells: int = 0
    cell_failures: int = 0
    route_index: int = 0
    route_slot: int = 0
    route_frontiers: dict[str, int] = field(default_factory=dict)
    consecutive_skips: int = 0
    nonproductive_cells: int = 0
    reroutes: int = 0
    last_position: tuple[int, int, int] | None = None
    manual_recovery_reason: str | None = None
    manual_recovery_at: float | None = None

    @classmethod
    def load(cls, path: Path, *, expected_y: int) -> "BuildProgress":
        if not path.exists():
            return cls(expected_y=expected_y)
        value = json.loads(path.read_text(encoding="utf-8"))
        # Migrate the original one-route checkpoint without replaying hundreds
        # of already attempted cells.
        value.setdefault("route_index", 0)
        value.setdefault("route_slot", int(value.get("slot", 0)))
        value.setdefault("route_frontiers", {"0": int(value["route_slot"])})
        value.setdefault("consecutive_skips", 0)
        value.setdefault("nonproductive_cells", 0)
        value.setdefault("reroutes", 0)
        value.setdefault("manual_recovery_reason", None)
        value.setdefault("manual_recovery_at", None)
        position = value.get("last_position")
        if position is not None:
            value["last_position"] = tuple(int(axis) for axis in position)
        return cls(**value)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(asdict(self), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)


@dataclass(frozen=True)
class BuildTurn:
    """One bounded construction outcome for monitoring and tests."""

    state: str
    detail: str
    progressed: bool = False
    position: tuple[int, int, int] | None = None
    material: str | None = None


def _unwrap(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    nested = value.get("data", value)
    return nested if isinstance(nested, Mapping) else {}


def non_op_survival_hold_reason(
    state: Mapping[str, Any],
    bot_name: str,
) -> str | None:
    """Return why a bridge state cannot safely drive this exact bot."""
    if not state:
        return "bridge state is unavailable"
    profile = str(state.get("automation_profile", ""))
    if profile != NON_OP_PROFILE or state.get("server_authority") is not False:
        return "bridge did not attest non_op_client with server_authority=false"
    player_name = str(state.get("player_name", ""))
    if player_name.casefold() != str(bot_name).casefold():
        return f"bridge identity mismatch: expected {bot_name}, got {player_name!r}"
    mode = str(state.get("game_mode", "")).lower()
    if mode != "survival":
        return f"{bot_name} must remain in Survival, found {mode!r}"
    return None


def manual_recovery_is_verified(state: Mapping[str, Any], dimension: str) -> bool:
    """Return whether an operator has restored a safe playable state."""
    try:
        return (
            not bool(state.get("is_dead"))
            and float(state.get("health", 0) or 0) >= MANUAL_RECOVERY_HEALTH
            and int(state.get("food_level", state.get("food", 0)) or 0)
            >= MANUAL_RECOVERY_FOOD
            and str(state.get("dimension", "")) == dimension
        )
    except (TypeError, ValueError):
        return False


def block_id(client: Any, position: tuple[int, int, int]) -> str:
    """Read one exact world block identifier."""
    x, y, z = position
    value = _unwrap(client.transport.dispatch("get_block", {"x": x, "y": y, "z": z}))
    return str(value.get("id", value.get("block", "")))


def select_build_material(inventory: Mapping[str, int]) -> str | None:
    """Choose common renewable masonry without consuming mission valuables."""
    return next((item for item in BUILD_MATERIALS if inventory.get(item, 0) > 0), None)


def find_road_target(
    client: Any,
    x: int,
    z: int,
    *,
    expected_y: int,
    rise: int = 10,
    drop: int = 16,
) -> tuple[int, int, int] | None:
    """Find the road cell above natural dry ground, ignoring prior masonry."""
    for ground_y in range(expected_y + rise, expected_y - drop - 1, -1):
        support = block_id(client, (x, ground_y, z))
        if support not in NATURAL_SUPPORTS:
            continue
        target_y = ground_y + 1
        target = block_id(client, (x, target_y, z))
        if target in {"minecraft:water", "minecraft:lava"}:
            return None
        return (x, target_y, z)
    return None


class PerpetualBuilder:
    """Place real blocks forever, gathering renewable masonry when depleted."""

    def __init__(
        self,
        client: Any,
        assignment: LaneAssignment,
        progress: BuildProgress,
        *,
        checkpoint_path: Path,
        sleeper=time.sleep,
    ) -> None:
        self.client = client
        self.assignment = assignment
        self.progress = progress
        self.checkpoint_path = checkpoint_path
        self.sleeper = sleeper

    def manual_recovery_hold(self, detail: str) -> BuildTurn:
        """Persist a fail-closed hold that survives process restarts."""
        self.progress.manual_recovery_reason = str(detail)
        self.progress.manual_recovery_at = time.time()
        self.progress.save(self.checkpoint_path)
        return BuildTurn("manual_recovery_hold", str(detail))

    def _clear_manual_recovery_hold(self) -> None:
        self.progress.manual_recovery_reason = None
        self.progress.manual_recovery_at = None
        self.progress.save(self.checkpoint_path)

    def _advance(self, *, skipped: bool = False, productive: bool = False) -> bool:
        self.progress.slot += 1
        self.progress.route_slot += 1
        self.progress.cell_failures = 0
        self.progress.skipped_cells += int(skipped)
        self.progress.consecutive_skips = (
            self.progress.consecutive_skips + 1 if skipped else 0
        )
        self.progress.nonproductive_cells = (
            0 if productive else self.progress.nonproductive_cells + 1
        )
        self.progress.route_frontiers[str(self.progress.route_index)] = (
            self.progress.route_slot
        )
        rerouted = self.progress.nonproductive_cells >= MAX_NONPRODUCTIVE_CELLS
        if rerouted:
            self.progress.route_index = (self.progress.route_index + 1) % len(ROUTES)
            self.progress.route_slot = self.progress.route_frontiers.get(
                str(self.progress.route_index), 0
            )
            self.progress.consecutive_skips = 0
            self.progress.nonproductive_cells = 0
            self.progress.expected_y = self.assignment.expected_y
            self.progress.reroutes += 1
        self.progress.save(self.checkpoint_path)
        return rerouted

    def _fail_cell(self, detail: str) -> BuildTurn:
        self.progress.cell_failures += 1
        if self.progress.cell_failures >= MAX_CELL_FAILURES:
            rerouted = self._advance(skipped=True)
            if rerouted:
                return BuildTurn(
                    "rerouted",
                    f"{detail}; pivoted to route {self.progress.route_index}",
                )
            return BuildTurn("skipped", f"{detail}; advanced after bounded retries")
        self.progress.save(self.checkpoint_path)
        return BuildTurn("retrying", detail)

    def run_turn(self) -> BuildTurn:
        """Run one survival, supply, or exact placement turn."""
        state = _unwrap(self.client.transport.dispatch("get_state", {}))
        unsafe = non_op_survival_hold_reason(state, self.assignment.bot)
        if unsafe is not None:
            return self.manual_recovery_hold(unsafe)
        try:
            health = float(state.get("health", 0) or 0)
            food = int(state.get("food_level", state.get("food", 0)) or 0)
        except (TypeError, ValueError):
            return self.manual_recovery_hold("bridge survival telemetry is invalid")
        if bool(state.get("is_dead")) or health <= 0:
            self.client.transport.dispatch("cancel", {})
            return self.manual_recovery_hold(
                "builder is on the death screen; normal respawn and manual "
                "Survival return are required"
            )
        if str(state.get("dimension", "")) != OVERWORLD:
            self.client.transport.dispatch("cancel", {})
            return self.manual_recovery_hold(
                "builder is outside the Overworld; return through normal "
                "Survival travel before resuming"
            )
        if health < CRITICAL_HEALTH or food < CRITICAL_FOOD:
            self.client.transport.dispatch("cancel", {})
            return self.manual_recovery_hold(
                f"critical survival margin health={health:.1f}, food={food}; "
                "manual recovery is required"
            )
        if self.progress.manual_recovery_reason is not None:
            if not manual_recovery_is_verified(state, OVERWORLD):
                self.client.transport.dispatch("cancel", {})
                return BuildTurn(
                    "manual_recovery_hold",
                    self.progress.manual_recovery_reason,
                )
            self._clear_manual_recovery_hold()
        if defend_or_flee(self.client, allow_safe_recovery_movement=True):
            return BuildTurn("defending", "defense or evasion took priority")
        if not has_build_survival_margin(self.client):
            if not eat_until_hunger(self.client, minimum_food=18):
                self.client.transport.dispatch("cancel", {})
                return self.manual_recovery_hold(
                    "carried food could not restore the required Survival margin"
                )
            return BuildTurn("recovering", "restoring survival margin")

        inventory = get_inventory(self.client)
        material = select_build_material(inventory)
        if material is None:
            gathered = gather_stone(self.client, count=32, timeout=180)
            return BuildTurn(
                "gathering" if gathered else "supply_retry",
                (
                    "gathered renewable masonry"
                    if gathered
                    else "stone gathering will retry"
                ),
                progressed=gathered,
            )

        x, z = self.assignment.cell(
            self.progress.route_slot,
            self.progress.route_index,
        )
        target = find_road_target(
            self.client,
            x,
            z,
            expected_y=self.progress.expected_y,
        )
        if target is None:
            return self._fail_cell(f"no dry natural support at {x},{z}")
        existing = block_id(self.client, target)
        if existing in BUILD_MATERIALS:
            self.progress.expected_y = target[1]
            self.progress.last_position = target
            self._advance()
            return BuildTurn(
                "verified_existing", "road cell was already built", position=target
            )

        before = int(inventory.get(material, 0))
        if not move_near(self.client, *target, timeout=60.0):
            return self._fail_cell(f"survival-supervised approach failed at {target}")
        placed = place_block_exact(self.client, *target, material, allow_break=True)
        after_inventory = get_inventory(self.client)
        for _ in range(3):
            if int(after_inventory.get(material, 0)) < before:
                break
            self.sleeper(0.25)
            after_inventory = get_inventory(self.client)
        after = int(after_inventory.get(material, 0))
        verified = block_id(self.client, target) == material
        if not placed or not verified:
            return self._fail_cell(f"placement was not verified at {target}")
        if after >= before:
            return self._fail_cell(
                f"world changed at {target} but inventory delta was not observed"
            )

        self.progress.blocks_placed += 1
        self.progress.materials_consumed[material] = (
            self.progress.materials_consumed.get(material, 0) + before - after
        )
        self.progress.expected_y = target[1]
        self.progress.last_position = target
        self._advance(productive=True)
        return BuildTurn(
            "building",
            f"placed and consumed {material}",
            progressed=True,
            position=target,
            material=material,
        )


__all__ = [
    "BUILD_MATERIALS",
    "BuildProgress",
    "BuildTurn",
    "LaneAssignment",
    "PerpetualBuilder",
    "find_road_target",
    "fleet_lanes",
    "manual_recovery_is_verified",
    "non_op_survival_hold_reason",
    "select_build_material",
]
