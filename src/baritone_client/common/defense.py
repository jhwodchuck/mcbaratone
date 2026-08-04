"""Pure threat assessment and self-defense policy.

This module deliberately contains no transport calls.  The combat module owns
Minecraft I/O while this module turns snapshots into deterministic decisions.
"""

from dataclasses import dataclass, field
from enum import Enum
import math
import time
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


class DefenseMode(str, Enum):
    """Persistent self-defense states."""

    CLEAR = "clear"
    ALERT = "alert"
    EVADE = "evade"
    ENGAGE = "engage"
    RECOVER = "recover"


class AttackStyle(str, Enum):
    """Coarse attack style used by the Milestone A policy."""

    MELEE = "melee"
    RANGED = "ranged"
    EXPLOSIVE = "explosive"
    BOSS = "boss"


@dataclass(frozen=True)
class ThreatProfile:
    """Static defaults for one hostile entity type."""

    severity: float
    style: AttackStyle
    always_evade: bool = False
    conditional: bool = False


@dataclass(frozen=True)
class ThreatAssessment:
    """Scored view of a nearby hostile entity."""

    entity: Dict
    entity_type: str
    distance: float
    closing_speed: float
    score: float
    style: AttackStyle
    always_evade: bool
    reasons: Tuple[str, ...] = ()


@dataclass(frozen=True)
class DefenseDecision:
    """One policy decision and the evidence used to make it."""

    mode: DefenseMode
    reason: str
    primary: Optional[ThreatAssessment] = None


@dataclass
class DefenseRuntime:
    """Small persistent state used to prevent flee/re-engage oscillation."""

    mode: DefenseMode = DefenseMode.CLEAR
    entered_at: float = field(default_factory=time.monotonic)
    recovery_until: float = 0.0
    last_threat_at: float = 0.0
    last_reason: str = "initial"
    # Consecutive failed run_away() attempts against the SAME entity id.
    # Confirmed live: an unarmored bot in mountainous terrain (windswept
    # forest -- ledges everywhere) got a zombie stuck in melee range for
    # dozens of consecutive cycles, "no terrain-safe escape endpoint found"
    # every time, and slowly died. armor_count<3 forces EVADE unconditionally
    # with no escalation, so pure evasion had a 0% success rate against this
    # specific threat/terrain combination for the entire encounter -- worse
    # than even an unfavorable fight. See evade_escalation_threshold below.
    evade_failures: int = 0
    evade_failure_entity: Optional[int] = None
    # A crowded cave can rotate the highest-scored entity every tick. Keep an
    # aggregate streak as well so switching between two attackers does not
    # reset a demonstrably failed escape strategy forever.
    consecutive_evade_failures: int = 0

    def record_evade_result(self, entity_id: Optional[int], escaped: bool) -> None:
        """Track a run_away() outcome against one specific threat."""
        self.consecutive_evade_failures = (
            0 if escaped else self.consecutive_evade_failures + 1
        )
        if escaped or entity_id != self.evade_failure_entity:
            self.evade_failures = 0 if escaped else 1
            self.evade_failure_entity = None if escaped else entity_id
        else:
            self.evade_failures += 1

    def should_escalate_to_combat(
        self, entity_id: Optional[int], *, threshold: int = 2
    ) -> bool:
        """True once evasion has demonstrably failed repeatedly for this threat."""
        return (
            self.consecutive_evade_failures >= threshold
            or (
                entity_id is not None
                and entity_id == self.evade_failure_entity
                and self.evade_failures >= threshold
            )
        )

    def transition(
        self,
        mode: DefenseMode,
        reason: str,
        *,
        now: Optional[float] = None,
    ) -> None:
        """Move to a state without resetting its timer on identical ticks."""
        now = time.monotonic() if now is None else now
        if mode != self.mode:
            self.mode = mode
            self.entered_at = now
        self.last_reason = reason
        if mode in (DefenseMode.ALERT, DefenseMode.EVADE, DefenseMode.ENGAGE):
            self.last_threat_at = now

    def hold_recovery(
        self,
        seconds: float = 6.0,
        *,
        now: Optional[float] = None,
    ) -> None:
        """Require a quiet recovery interval after an escape or fight."""
        now = time.monotonic() if now is None else now
        self.recovery_until = max(self.recovery_until, now + max(0.0, seconds))
        self.transition(DefenseMode.RECOVER, "post-encounter recovery", now=now)


@dataclass(frozen=True)
class EscapeCandidate:
    """Potential flee destination before terrain inspection."""

    x: int
    y: int
    z: int
    direction_score: float


_PROFILES = {
    "creeper": ThreatProfile(100, AttackStyle.EXPLOSIVE, always_evade=True),
    "warden": ThreatProfile(120, AttackStyle.BOSS, always_evade=True),
    "wither": ThreatProfile(120, AttackStyle.BOSS, always_evade=True),
    "ender_dragon": ThreatProfile(120, AttackStyle.BOSS, always_evade=True),
    "skeleton": ThreatProfile(78, AttackStyle.RANGED, always_evade=True),
    "stray": ThreatProfile(82, AttackStyle.RANGED, always_evade=True),
    "bogged": ThreatProfile(84, AttackStyle.RANGED, always_evade=True),
    "pillager": ThreatProfile(82, AttackStyle.RANGED, always_evade=True),
    "witch": ThreatProfile(95, AttackStyle.RANGED, always_evade=True),
    "blaze": ThreatProfile(92, AttackStyle.RANGED, always_evade=True),
    "ghast": ThreatProfile(90, AttackStyle.RANGED, always_evade=True),
    "guardian": ThreatProfile(88, AttackStyle.RANGED, always_evade=True),
    "elder_guardian": ThreatProfile(105, AttackStyle.RANGED, always_evade=True),
    "shulker": ThreatProfile(86, AttackStyle.RANGED, always_evade=True),
    "breeze": ThreatProfile(82, AttackStyle.RANGED, always_evade=True),
    "drowned": ThreatProfile(68, AttackStyle.MELEE),
    "zombie": ThreatProfile(55, AttackStyle.MELEE),
    "zombie_villager": ThreatProfile(57, AttackStyle.MELEE),
    "husk": ThreatProfile(62, AttackStyle.MELEE),
    "cave_spider": ThreatProfile(75, AttackStyle.MELEE),
    "spider": ThreatProfile(52, AttackStyle.MELEE, conditional=True),
    "enderman": ThreatProfile(80, AttackStyle.MELEE, conditional=True),
    "slime": ThreatProfile(48, AttackStyle.MELEE),
    "magma_cube": ThreatProfile(65, AttackStyle.MELEE),
    "vindicator": ThreatProfile(85, AttackStyle.MELEE),
    "evoker": ThreatProfile(100, AttackStyle.RANGED, always_evade=True),
    "ravager": ThreatProfile(105, AttackStyle.MELEE, always_evade=True),
    "hoglin": ThreatProfile(78, AttackStyle.MELEE),
    "zoglin": ThreatProfile(88, AttackStyle.MELEE, always_evade=True),
    "piglin_brute": ThreatProfile(92, AttackStyle.MELEE, always_evade=True),
    "piglin": ThreatProfile(65, AttackStyle.RANGED, conditional=True),
    "zombified_piglin": ThreatProfile(72, AttackStyle.MELEE, conditional=True),
    "polar_bear": ThreatProfile(72, AttackStyle.MELEE, conditional=True),
    "bee": ThreatProfile(45, AttackStyle.MELEE, conditional=True),
    "wolf": ThreatProfile(48, AttackStyle.MELEE, conditional=True),
    "phantom": ThreatProfile(76, AttackStyle.MELEE, always_evade=True),
    "silverfish": ThreatProfile(42, AttackStyle.MELEE),
    "endermite": ThreatProfile(42, AttackStyle.MELEE),
    "vex": ThreatProfile(82, AttackStyle.MELEE, always_evade=True),
}


def normalize_entity_type(entity_type: object) -> str:
    """Return a Minecraft entity registry path without its namespace."""
    value = str(entity_type or "").lower()
    return value.split(":", 1)[-1]


def _profile_for(entity_type: str) -> Optional[ThreatProfile]:
    # Exact matching avoids classifying a pig as a piglin or a harmless item
    # because one happens to contain a hostile substring.
    return _PROFILES.get(normalize_entity_type(entity_type))


def _explicit_aggression(entity: Dict, player_state: Dict) -> Optional[bool]:
    for key in ("is_attacking", "is_aggressive", "angry_at_player", "hostile"):
        if key in entity:
            return bool(entity.get(key))
    target_id = entity.get("target_id")
    player_id = player_state.get("entity_id", player_state.get("player_id"))
    if target_id is not None and player_id is not None:
        return target_id == player_id
    return None


def _conditional_is_active(
    entity_type: str,
    entity: Dict,
    player_state: Dict,
    distance: float,
) -> bool:
    explicit = _explicit_aggression(entity, player_state)
    if entity_type == "spider":
        # Mob targets can briefly clear between melee hits.  A daytime spider
        # already inside contact range is therefore unsafe even when the
        # sampled aggression flag is false.
        world_time = int(player_state.get("world_time", 13000)) % 24000
        return explicit is True or world_time >= 12000 or distance <= 5.0
    if explicit is not None:
        return explicit
    if entity_type == "enderman":
        # Without an anger signal, only treat immediate contact range as an
        # active threat.  Merely seeing an Enderman must not start a fight.
        return distance <= 3.5
    return distance <= 3.5


def _closing_speed(entity: Dict, player_state: Dict) -> float:
    position = entity.get("position") or {}
    player = player_state.get(
        "block_position",
        player_state.get("position", {}),
    ) or {}
    velocity = entity.get("velocity") or {}
    try:
        dx = float(position.get("x", 0)) - float(player.get("x", 0))
        dz = float(position.get("z", 0)) - float(player.get("z", 0))
        distance = math.hypot(dx, dz)
        if distance < 0.01:
            return 0.0
        radial_velocity = (
            float(velocity.get("x", 0)) * dx
            + float(velocity.get("z", 0)) * dz
        ) / distance
        return max(0.0, -radial_velocity)
    except (TypeError, ValueError):
        return 0.0


def assess_threats(
    entities: Iterable[Dict],
    player_state: Optional[Dict] = None,
) -> List[ThreatAssessment]:
    """Classify and rank hostiles by lethality, proximity, and movement."""
    state = player_state or {}
    assessments: List[ThreatAssessment] = []
    for entity in entities:
        entity_type = normalize_entity_type(entity.get("type"))
        profile = _profile_for(entity_type)
        if profile is None:
            continue
        try:
            distance = max(0.0, float(entity.get("distance", 999)))
        except (TypeError, ValueError):
            distance = 999.0
        if profile.conditional and not _conditional_is_active(
            entity_type, entity, state, distance
        ):
            continue
        closing = _closing_speed(entity, state)
        proximity = max(0.0, 16.0 - distance) * 2.5
        closing_risk = min(20.0, closing * 16.0)
        score = profile.severity + proximity + closing_risk
        reasons = [profile.style.value, f"{distance:.1f}m"]
        if closing > 0.1:
            reasons.append(f"closing {closing:.2f}m/tick")
        if profile.always_evade:
            reasons.append("avoid-only")
        assessments.append(
            ThreatAssessment(
                entity=entity,
                entity_type=entity_type,
                distance=distance,
                closing_speed=closing,
                score=score,
                style=profile.style,
                always_evade=profile.always_evade,
                reasons=tuple(reasons),
            )
        )
    return sorted(assessments, key=lambda item: (-item.score, item.distance))


def choose_defense_action(
    threats: Sequence[ThreatAssessment],
    *,
    health: float,
    armor_count: int,
    has_weapon: bool,
    runtime: DefenseRuntime,
    now: Optional[float] = None,
) -> DefenseDecision:
    """Choose a conservative action while retaining post-fight hysteresis."""
    now = time.monotonic() if now is None else now
    if health < 12.0:
        if threats and threats[0].distance <= 12.0:
            return DefenseDecision(
                DefenseMode.EVADE,
                "critical health with nearby threat",
                threats[0],
            )
        return DefenseDecision(DefenseMode.RECOVER, "critical health")

    if not threats:
        if runtime.mode in (
            DefenseMode.EVADE,
            DefenseMode.ENGAGE,
            DefenseMode.RECOVER,
        ):
            if now < runtime.recovery_until:
                return DefenseDecision(DefenseMode.RECOVER, "recovery hysteresis")
        return DefenseDecision(DefenseMode.CLEAR, "no active threats")

    urgent = [threat for threat in threats if threat.distance <= 10.0]
    if urgent and len(threats) > 1:
        return DefenseDecision(
            DefenseMode.EVADE,
            "multiple threats inside defense radius",
            threats[0],
        )
    primary = urgent[0] if urgent else threats[0]
    if not urgent and primary.closing_speed <= 0.15:
        return DefenseDecision(
            DefenseMode.ALERT,
            "threat outside action radius",
            primary,
        )
    if primary.always_evade:
        return DefenseDecision(DefenseMode.EVADE, f"avoid {primary.style.value} threat", primary)
    if armor_count < 3:
        return DefenseDecision(DefenseMode.EVADE, f"only {armor_count}/4 armor pieces", primary)
    if health < 16.0:
        return DefenseDecision(DefenseMode.EVADE, f"health only {health:.1f}", primary)
    if not has_weapon:
        return DefenseDecision(DefenseMode.EVADE, "no usable weapon", primary)
    return DefenseDecision(DefenseMode.ENGAGE, "equipped for bounded melee", primary)


def plan_escape_candidates(
    player_position: Dict,
    threats: Sequence[ThreatAssessment],
    *,
    distance: float = 24.0,
) -> List[EscapeCandidate]:
    """Generate routes away from the combined pressure of all threats."""
    def coordinate(name: str, default: float) -> float:
        value = player_position.get(name, default)
        try:
            return float(default if value is None else value)
        except (TypeError, ValueError):
            return default

    px = coordinate("x", 0.0)
    py = int(coordinate("y", 64.0))
    pz = coordinate("z", 0.0)
    away_x = 0.0
    away_z = 0.0
    for threat in threats:
        position = threat.entity.get("position") or {}
        try:
            tx = float(px if position.get("x") is None else position.get("x"))
            tz = float(pz if position.get("z") is None else position.get("z"))
        except (TypeError, ValueError):
            tx, tz = px, pz
        dx = px - tx
        dz = pz - tz
        length = max(0.01, math.hypot(dx, dz))
        weight = max(1.0, threat.score) / max(2.0, threat.distance)
        away_x += dx / length * weight
        away_z += dz / length * weight

    length = math.hypot(away_x, away_z)
    if length < 0.01:
        away_x, away_z, length = 1.0, 0.0, 1.0
    base_angle = math.atan2(away_z / length, away_x / length)
    candidates: List[EscapeCandidate] = []
    offsets = (0.0, math.pi / 4, -math.pi / 4, math.pi / 2, -math.pi / 2)
    y_offsets = (0, 1, -1)
    for index, offset in enumerate(offsets):
        angle = base_angle + offset
        for dy in y_offsets:
            candidates.append(
                EscapeCandidate(
                    x=int(round(px + math.cos(angle) * distance)),
                    y=py + dy,
                    z=int(round(pz + math.sin(angle) * distance)),
                    direction_score=1.0 - index * 0.1 - abs(dy) * 0.05,
                )
            )
    return candidates


__all__ = [
    "AttackStyle",
    "DefenseDecision",
    "DefenseMode",
    "DefenseRuntime",
    "EscapeCandidate",
    "ThreatAssessment",
    "assess_threats",
    "choose_defense_action",
    "normalize_entity_type",
    "plan_escape_candidates",
]
