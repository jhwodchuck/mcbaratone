"""Typed, JSON-safe contracts for the supervised family End expedition."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import re
from typing import Any, Mapping, Optional, Sequence, Tuple


DEFAULT_BOTS = ("Bot07", "Bot15", "Bot16", "Bot17", "Bot18", "Bot19")


class ExpeditionPhase(str, Enum):
    PREFLIGHT = "PREFLIGHT"
    WAITING_FOR_LEADER = "WAITING_FOR_LEADER"
    RENDEZVOUS = "RENDEZVOUS"
    ESCORTING = "ESCORTING"
    PORTAL_HOLD = "PORTAL_HOLD"
    PORTAL_ACTIVE = "PORTAL_ACTIVE"
    ENTERING_END = "ENTERING_END"
    DRAGON_SUPPORT = "DRAGON_SUPPORT"
    COMPLETE = "COMPLETE"
    HOLD = "HOLD"
    ABORTED = "ABORTED"


class OperatorAction(str, Enum):
    START = "START"
    HOLD = "HOLD"
    RESUME = "RESUME"
    ACTIVATE_PORTAL = "ACTIVATE_PORTAL"
    ENTER_PORTAL = "ENTER_PORTAL"
    START_FIGHT = "START_FIGHT"
    ABORT = "ABORT"


@dataclass(frozen=True)
class Vec3:
    x: float
    y: float
    z: float

    def to_dict(self) -> dict[str, float]:
        return {"x": self.x, "y": self.y, "z": self.z}

    @classmethod
    def from_dict(cls, value: Any) -> Optional["Vec3"]:
        if not isinstance(value, Mapping):
            return None
        try:
            return cls(float(value["x"]), float(value["y"]), float(value["z"]))
        except (KeyError, TypeError, ValueError):
            return None


@dataclass(frozen=True)
class PlayerSnapshot:
    name: str
    online: bool
    position: Optional[Vec3] = None
    rotation: Optional[Tuple[float, float]] = None
    dimension: Optional[str] = None
    health: Optional[float] = None
    food: Optional[int] = None
    game_mode: Optional[str] = None
    entities: Tuple[Mapping[str, Any], ...] = field(default_factory=tuple)
    observed_at_monotonic: Optional[float] = None
    observed_at_wall: Optional[float] = None

    @property
    def yaw(self) -> Optional[float]:
        return self.rotation[0] if self.rotation else None

    @property
    def in_end(self) -> bool:
        return str(self.dimension or "").casefold() in {
            "minecraft:the_end",
            "the_end",
            "end",
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "online": self.online,
            "position": self.position.to_dict() if self.position else None,
            "rotation": list(self.rotation) if self.rotation else None,
            "dimension": self.dimension,
            "health": self.health,
            "food": self.food,
            "game_mode": self.game_mode,
            "entities": [dict(entity) for entity in self.entities],
            "observed_at_monotonic": self.observed_at_monotonic,
            "observed_at_wall": self.observed_at_wall,
        }

    @classmethod
    def from_dict(cls, value: Any) -> Optional["PlayerSnapshot"]:
        if not isinstance(value, Mapping) or not isinstance(value.get("name"), str):
            return None
        rotation = value.get("rotation")
        parsed_rotation = None
        if isinstance(rotation, Sequence) and not isinstance(rotation, (str, bytes)):
            try:
                parsed_rotation = (float(rotation[0]), float(rotation[1]))
            except (IndexError, TypeError, ValueError):
                parsed_rotation = None
        entities = value.get("entities", ())
        return cls(
            name=value["name"],
            online=bool(value.get("online", False)),
            position=Vec3.from_dict(value.get("position")),
            rotation=parsed_rotation,
            dimension=_optional_text(value.get("dimension")),
            health=_optional_float(value.get("health")),
            food=_optional_int(value.get("food")),
            game_mode=_optional_text(value.get("game_mode")),
            entities=tuple(item for item in entities if isinstance(item, Mapping)),
            observed_at_monotonic=_optional_float(value.get("observed_at_monotonic")),
            observed_at_wall=_optional_float(value.get("observed_at_wall")),
        )


@dataclass(frozen=True)
class BotReadiness:
    bot_name: str
    online: bool
    alive: bool
    survival: bool
    armor_pieces: int
    health: float
    food: int
    has_weapon: bool
    has_shield: bool
    eye_count: int = 0
    reason: str = ""
    has_bow: bool = False
    arrow_count: int = 0

    def ready_for(self, config: "ExpeditionConfig") -> bool:
        return (
            self.online
            and self.alive
            and self.survival
            and self.armor_pieces >= config.required_armor_pieces
            and self.health >= config.required_health
            and self.food >= config.required_food
            and self.has_weapon
        )

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, value: Any) -> Optional["BotReadiness"]:
        if not isinstance(value, Mapping) or not isinstance(value.get("bot_name"), str):
            return None
        try:
            return cls(
                bot_name=value["bot_name"],
                online=bool(value.get("online")),
                alive=bool(value.get("alive")),
                survival=bool(value.get("survival")),
                armor_pieces=int(value.get("armor_pieces", 0)),
                health=float(value.get("health", 0)),
                food=int(value.get("food", 0)),
                has_weapon=bool(value.get("has_weapon")),
                has_shield=bool(value.get("has_shield")),
                has_bow=bool(value.get("has_bow")),
                arrow_count=int(value.get("arrow_count", 0)),
                eye_count=int(value.get("eye_count", 0)),
                reason=str(value.get("reason", "")),
            )
        except (TypeError, ValueError):
            return None


@dataclass(frozen=True)
class ReadinessReport:
    bots: Tuple[BotReadiness, ...]
    observed_at_monotonic: float

    def by_name(self, name: str) -> Optional[BotReadiness]:
        return next((bot for bot in self.bots if bot.bot_name == name), None)

    def combat_ready(self, config: "ExpeditionConfig") -> bool:
        """Require the full combat fleet without re-requiring spent Eyes."""
        expected = set(config.bot_names)
        actual = {bot.bot_name for bot in self.bots if bot.ready_for(config)}
        archers = sum(
            1
            for bot in self.bots
            if bot.ready_for(config)
            and bot.has_bow
            and bot.arrow_count >= config.required_arrows_per_archer
        )
        return (
            expected == actual
            and archers >= config.required_archers
        )

    def all_ready(self, config: "ExpeditionConfig") -> bool:
        """Require combat readiness plus the pre-activation Eye supply."""
        carrier = self.by_name(config.eye_carrier)
        return (
            self.combat_ready(config)
            and carrier is not None
            and carrier.eye_count >= config.required_eyes
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "bots": [bot.to_dict() for bot in self.bots],
            "observed_at_monotonic": self.observed_at_monotonic,
        }

    @classmethod
    def from_dict(cls, value: Any) -> Optional["ReadinessReport"]:
        if not isinstance(value, Mapping):
            return None
        bots = tuple(
            bot
            for bot in (BotReadiness.from_dict(item) for item in value.get("bots", ()))
            if bot is not None
        )
        timestamp = _optional_float(value.get("observed_at_monotonic"))
        return cls(bots, timestamp or 0.0)


@dataclass(frozen=True)
class FormationSlot:
    bot_name: str
    offset: Vec3
    role: str = "escort"

    def to_dict(self) -> dict[str, Any]:
        return {"bot_name": self.bot_name, "offset": self.offset.to_dict(), "role": self.role}


@dataclass(frozen=True)
class ExpeditionConfig:
    bot_names: Tuple[str, ...] = DEFAULT_BOTS
    eye_carrier: str = "Bot07"
    required_armor_pieces: int = 4
    required_health: float = 12.0
    required_food: int = 10
    required_eyes: int = 12
    required_archers: int = 2
    required_arrows_per_archer: int = 8
    stale_threshold_seconds: float = 5.0
    movement_tolerance: float = 3.0
    navigation_timeout_seconds: float = 12.0
    navigation_progress_distance: float = 1.5

    def to_dict(self) -> dict[str, Any]:
        return {
            "bot_names": list(self.bot_names),
            "eye_carrier": self.eye_carrier,
            "required_armor_pieces": self.required_armor_pieces,
            "required_health": self.required_health,
            "required_food": self.required_food,
            "required_eyes": self.required_eyes,
            "required_archers": self.required_archers,
            "required_arrows_per_archer": self.required_arrows_per_archer,
            "stale_threshold_seconds": self.stale_threshold_seconds,
            "movement_tolerance": self.movement_tolerance,
            "navigation_timeout_seconds": self.navigation_timeout_seconds,
            "navigation_progress_distance": self.navigation_progress_distance,
        }

    @classmethod
    def from_dict(cls, value: Any) -> "ExpeditionConfig":
        if not isinstance(value, Mapping):
            return cls()
        return cls(
            bot_names=tuple(str(item) for item in value.get("bot_names", DEFAULT_BOTS)),
            eye_carrier=str(value.get("eye_carrier", "Bot07")),
            required_armor_pieces=int(value.get("required_armor_pieces", 4)),
            required_health=float(value.get("required_health", 12)),
            required_food=int(value.get("required_food", 10)),
            required_eyes=int(value.get("required_eyes", 12)),
            required_archers=int(value.get("required_archers", 2)),
            required_arrows_per_archer=int(value.get("required_arrows_per_archer", 8)),
            stale_threshold_seconds=float(value.get("stale_threshold_seconds", 5)),
            movement_tolerance=float(value.get("movement_tolerance", 3)),
            navigation_timeout_seconds=float(value.get("navigation_timeout_seconds", 12)),
            navigation_progress_distance=float(value.get("navigation_progress_distance", 1.5)),
        )


@dataclass(frozen=True)
class ExpeditionCheckpoint:
    phase: ExpeditionPhase = ExpeditionPhase.PREFLIGHT
    prior_phase: Optional[ExpeditionPhase] = None
    reason: str = ""
    readiness: Optional[ReadinessReport] = None
    leader_snapshot: Optional[PlayerSnapshot] = None
    entered_bots: Tuple[str, ...] = field(default_factory=tuple)
    dragon_seen: bool = False
    dragon_completion_observations: int = 0
    last_action: Optional[OperatorAction] = None
    config: ExpeditionConfig = field(default_factory=ExpeditionConfig)

    @property
    def resume_phase(self) -> ExpeditionPhase:
        if self.prior_phase in {None, ExpeditionPhase.HOLD}:
            return ExpeditionPhase.WAITING_FOR_LEADER
        return self.prior_phase

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase.value,
            "prior_phase": self.prior_phase.value if self.prior_phase else None,
            "reason": self.reason,
            "readiness": self.readiness.to_dict() if self.readiness else None,
            "leader_snapshot": self.leader_snapshot.to_dict() if self.leader_snapshot else None,
            "entered_bots": list(self.entered_bots),
            "dragon_seen": self.dragon_seen,
            "dragon_completion_observations": self.dragon_completion_observations,
            "last_action": self.last_action.value if self.last_action else None,
            "config": self.config.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: Any) -> "ExpeditionCheckpoint":
        if not isinstance(value, Mapping):
            return cls()
        return cls(
            phase=_enum(ExpeditionPhase, value.get("phase"), ExpeditionPhase.PREFLIGHT),
            prior_phase=_optional_enum(ExpeditionPhase, value.get("prior_phase")),
            reason=str(value.get("reason", "")),
            readiness=ReadinessReport.from_dict(value.get("readiness")),
            leader_snapshot=PlayerSnapshot.from_dict(value.get("leader_snapshot")),
            entered_bots=tuple(str(item) for item in value.get("entered_bots", ())),
            dragon_seen=bool(value.get("dragon_seen", False)),
            dragon_completion_observations=int(value.get("dragon_completion_observations", 0)),
            last_action=_optional_enum(OperatorAction, value.get("last_action")),
            config=ExpeditionConfig.from_dict(value.get("config")),
        )


_SAFE_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


def validate_player_name(value: Any) -> str:
    """Preserve exact player names while rejecting command/selector syntax."""
    if not isinstance(value, str) or not _SAFE_NAME.fullmatch(value) or value.startswith("@"):
        raise ValueError("unsafe or unsupported player name")
    return value


def _optional_text(value: Any) -> Optional[str]:
    return str(value) if value is not None else None


def _optional_float(value: Any) -> Optional[float]:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _optional_int(value: Any) -> Optional[int]:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _optional_enum(enum_type: Any, value: Any) -> Any:
    if value is None:
        return None
    try:
        return enum_type(value)
    except (TypeError, ValueError):
        return None


def _enum(enum_type: Any, value: Any, default: Any) -> Any:
    return _optional_enum(enum_type, value) or default


__all__ = [
    "DEFAULT_BOTS",
    "BotReadiness",
    "ExpeditionCheckpoint",
    "ExpeditionConfig",
    "ExpeditionPhase",
    "FormationSlot",
    "OperatorAction",
    "PlayerSnapshot",
    "ReadinessReport",
    "Vec3",
    "validate_player_name",
]
