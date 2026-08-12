"""Polling formation escort; intentionally contains no direct-follow route."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from enum import Enum
import math
from typing import Any, Callable, Mapping, Optional, Protocol, Sequence, Tuple

from .models import FormationSlot, PlayerSnapshot, Vec3


HOSTILE_TYPES = frozenset(
    {
        "blaze", "bogged", "breeze", "cave_spider", "creeper", "drowned",
        "elder_guardian", "enderman", "endermite", "evoker", "ghast",
        "guardian", "hoglin", "husk", "magma_cube", "phantom", "piglin",
        "piglin_brute", "pillager", "ravager", "shulker", "silverfish",
        "skeleton", "slime", "spider", "stray", "vex", "vindicator",
        "warden", "witch", "wither", "wither_skeleton", "zoglin", "zombie",
        "zombie_villager", "zombified_piglin",
    }
)


class EscortDecision(str, Enum):
    HOLD = "HOLD"
    STAY = "STAY"
    MOVE = "MOVE"
    DEFEND = "DEFEND"
    ABORT = "ABORT"


@dataclass(frozen=True)
class EscortTick:
    bot_name: str
    decision: EscortDecision
    reason: str
    target: Optional[Vec3] = None


@dataclass(frozen=True)
class FleetEscortTick:
    decision: EscortDecision
    members: Tuple[EscortTick, ...]
    reason: str


class FleetTracker(Protocol):
    def get_snapshot(self, name: str) -> Optional[PlayerSnapshot]: ...
    def get_entities(self, name: str) -> Sequence[Mapping[str, Any]]: ...
    def cancel(self, name: str) -> None: ...


FORMATION_OFFSETS = (
    Vec3(0.0, 0.0, -2.5),
    Vec3(2.5, 0.0, -1.5),
    Vec3(-2.5, 0.0, -1.5),
    Vec3(3.0, 0.0, 1.5),
    Vec3(-3.0, 0.0, 1.5),
    Vec3(0.0, 0.0, 3.0),
)


def formation_offsets(bot_names: Sequence[str]) -> dict[str, FormationSlot]:
    """Assign a deterministic protective ring, with Bot07 nearest the leader."""
    unique = sorted(set(str(name) for name in bot_names))
    ordered = (["Bot07"] if "Bot07" in unique else []) + [
        name for name in unique if name != "Bot07"
    ]
    return {
        name: FormationSlot(name, FORMATION_OFFSETS[index], "eye_carrier" if name == "Bot07" else "escort")
        for index, name in enumerate(ordered[: len(FORMATION_OFFSETS)])
    }


def rotate_offset(slot: FormationSlot, yaw: Optional[float]) -> Vec3:
    if yaw is None:
        return slot.offset
    angle = math.radians(float(yaw))
    cosine, sine = math.cos(angle), math.sin(angle)
    return Vec3(
        slot.offset.x * cosine + slot.offset.z * sine,
        slot.offset.y,
        -slot.offset.x * sine + slot.offset.z * cosine,
    )


def desired_position(leader: PlayerSnapshot, slot: FormationSlot) -> Optional[Vec3]:
    if leader.position is None:
        return None
    offset = rotate_offset(slot, leader.yaw)
    return Vec3(
        leader.position.x + offset.x,
        leader.position.y + offset.y,
        leader.position.z + offset.z,
    )


def is_safe_hostile_target(entity: Mapping[str, Any], protected_names: Sequence[str]) -> bool:
    entity_type = str(entity.get("type", "")).casefold().split(":")[-1]
    if entity_type in {"player", "minecraft:player"} or entity_type not in HOSTILE_TYPES:
        return False
    protected = {str(name).casefold() for name in protected_names}
    entity_name = str(entity.get("name", entity.get("display_name", ""))).casefold()
    return not entity_name or entity_name not in protected


class EscortWorker:
    """One bot, one bridge client, one short formation-maintenance step."""

    def __init__(
        self,
        bot_name: str,
        leader_name: str,
        slot: FormationSlot,
        tracker: FleetTracker,
        navigator: Callable[[str, Vec3, float], bool],
        defense: Callable[[str, Sequence[Mapping[str, Any]]], bool],
        clock: Callable[[], float],
        *,
        protected_names: Sequence[str] = (),
        stale_seconds: float = 5.0,
        movement_tolerance: float = 3.0,
        min_health: float = 12.0,
        min_food: int = 10,
        navigator_timeout: float = 8.0,
    ) -> None:
        self.bot_name = bot_name
        self.leader_name = leader_name
        self.slot = slot
        self.tracker = tracker
        self.navigator = navigator
        self.defense = defense
        self.clock = clock
        self.protected_names = tuple(protected_names) + (leader_name,)
        self.stale_seconds = stale_seconds
        self.movement_tolerance = movement_tolerance
        self.min_health = min_health
        self.min_food = min_food
        self.navigator_timeout = navigator_timeout

    def cancel(self) -> None:
        self.tracker.cancel(self.bot_name)

    def _fresh(self, value: Optional[PlayerSnapshot], now: float) -> bool:
        return bool(
            value
            and value.online
            and value.position is not None
            and value.observed_at_monotonic is not None
            and 0.0 <= now - value.observed_at_monotonic <= self.stale_seconds
        )

    def step(self) -> EscortTick:
        try:
            now = float(self.clock())
            bot = self.tracker.get_snapshot(self.bot_name)
            leader = self.tracker.get_snapshot(self.leader_name)
            if not self._fresh(leader, now):
                self.cancel()
                return EscortTick(self.bot_name, EscortDecision.HOLD, "leader telemetry missing or stale")
            if not self._fresh(bot, now):
                self.cancel()
                return EscortTick(self.bot_name, EscortDecision.HOLD, "bot telemetry missing or stale")
            assert bot is not None and leader is not None
            if (bot.health or 0.0) <= 0:
                self.cancel()
                return EscortTick(self.bot_name, EscortDecision.ABORT, "bot is dead")
            if str(bot.game_mode or "").casefold() != "survival":
                self.cancel()
                return EscortTick(self.bot_name, EscortDecision.HOLD, "bot is not in Survival")
            if (bot.health or 0.0) < self.min_health or (bot.food or 0) < self.min_food:
                self.cancel()
                return EscortTick(self.bot_name, EscortDecision.HOLD, "bot survival margin is low")
            if str(bot.dimension).casefold() != str(leader.dimension).casefold():
                self.cancel()
                return EscortTick(self.bot_name, EscortDecision.HOLD, "dimension mismatch")
            hostiles = tuple(
                entity
                for entity in self.tracker.get_entities(self.bot_name)
                if is_safe_hostile_target(entity, self.protected_names)
            )
            if hostiles and self.defense(self.bot_name, hostiles):
                return EscortTick(self.bot_name, EscortDecision.DEFEND, "hostile defense active")
            target = desired_position(leader, self.slot)
            if target is None or bot.position is None:
                self.cancel()
                return EscortTick(self.bot_name, EscortDecision.HOLD, "position unavailable")
            distance = math.dist(
                (bot.position.x, bot.position.y, bot.position.z),
                (target.x, target.y, target.z),
            )
            if distance <= self.movement_tolerance:
                return EscortTick(self.bot_name, EscortDecision.STAY, "in formation", target)
            if not self.navigator(self.bot_name, target, self.navigator_timeout):
                self.cancel()
                return EscortTick(self.bot_name, EscortDecision.HOLD, "short navigation failed", target)
            return EscortTick(self.bot_name, EscortDecision.MOVE, "moving to formation", target)
        except Exception as error:
            self.cancel()
            return EscortTick(self.bot_name, EscortDecision.HOLD, f"escort error: {error}")


class EscortFleet:
    def __init__(self, workers: Sequence[EscortWorker]) -> None:
        self.workers = tuple(workers)

    def cancel_all(self) -> None:
        for worker in self.workers:
            worker.cancel()

    def tick(self) -> FleetEscortTick:
        if not self.workers:
            return FleetEscortTick(EscortDecision.HOLD, (), "no escort workers")
        with ThreadPoolExecutor(max_workers=len(self.workers)) as pool:
            members = tuple(pool.map(lambda worker: worker.step(), self.workers))
        if any(item.decision is EscortDecision.ABORT for item in members):
            self.cancel_all()
            return FleetEscortTick(EscortDecision.ABORT, members, "a bot aborted")
        if any(item.decision is EscortDecision.HOLD for item in members):
            self.cancel_all()
            return FleetEscortTick(EscortDecision.HOLD, members, "a bot requested HOLD")
        if any(item.decision is EscortDecision.DEFEND for item in members):
            return FleetEscortTick(EscortDecision.DEFEND, members, "fleet defending")
        if all(item.decision is EscortDecision.STAY for item in members):
            return FleetEscortTick(EscortDecision.STAY, members, "fleet in formation")
        return FleetEscortTick(EscortDecision.MOVE, members, "fleet moving")


__all__ = [
    "EscortDecision",
    "EscortFleet",
    "EscortTick",
    "FleetEscortTick",
    "EscortWorker",
    "formation_offsets",
    "is_safe_hostile_target",
    "rotate_offset",
]
