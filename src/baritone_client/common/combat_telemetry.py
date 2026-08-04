"""Best-effort structured telemetry for combat decisions and outcomes."""

from __future__ import annotations

import functools
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Optional
from uuid import uuid4

from ..observability import emit_event


DEFAULT_SNAPSHOT_INTERVAL_SECONDS = 1.0
MAX_THREATS = 8
MAX_TEXT_LENGTH = 120
_PLAYER_FIELDS = (
    "health",
    "food",
    "food_level",
    "armor_count",
    "armor_points",
    "is_blocking",
    "is_using_item",
    "attack_cooldown",
    "dimension",
    "block_position",
    "position",
)
_THREAT_FIELDS = (
    "id",
    "uuid",
    "type",
    "distance",
    "health",
    "position",
    "velocity",
    "target",
)


def _bounded(value: Any, depth: int = 0) -> Any:
    if depth >= 3:
        return "<max-depth>"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:MAX_TEXT_LENGTH]
    if isinstance(value, dict):
        return {
            str(key)[:MAX_TEXT_LENGTH]: _bounded(item, depth + 1)
            for key, item in list(value.items())[:12]
        }
    if isinstance(value, (list, tuple, set)):
        return [_bounded(item, depth + 1) for item in list(value)[:MAX_THREATS]]
    return str(value)[:MAX_TEXT_LENGTH]


def _selected(payload: Any, fields: tuple[str, ...]) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    return {
        field: _bounded(payload[field])
        for field in fields
        if field in payload
    }


def _player(payload: Any) -> Dict[str, Any]:
    return _selected(payload, _PLAYER_FIELDS)


def _threat(payload: Any) -> Dict[str, Any]:
    return _selected(payload, _THREAT_FIELDS)


def _threats(payloads: Optional[Iterable[Any]]) -> list[Dict[str, Any]]:
    return [_threat(item) for item in list(payloads or [])[:MAX_THREATS]]


def _health(player: Any) -> Optional[float]:
    value = player.get("health") if isinstance(player, dict) else None
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


@dataclass
class _CombatState:
    encounter_id: Optional[str] = None
    started_at: Optional[float] = None
    last_snapshot_at: float = 0.0
    last_health: Optional[float] = None
    source: Optional[str] = None
    threat_health: Dict[str, float] = field(default_factory=dict)
    target_ids: set[str] = field(default_factory=set)


def _threat_health(payloads: Iterable[Any]) -> Dict[str, float]:
    observed: Dict[str, float] = {}
    for item in payloads:
        if not isinstance(item, dict) or item.get("id") is None:
            continue
        health = _health(item)
        if health is not None:
            observed[str(item["id"])] = health
    return observed


class CombatTelemetryRecorder:
    """Hold one client's current encounter and emit bounded event records."""

    def __init__(
        self,
        *,
        client_label: str,
        transport_label: str,
        snapshot_interval_seconds: float = DEFAULT_SNAPSHOT_INTERVAL_SECONDS,
    ) -> None:
        self.client_label = client_label
        self.transport_label = transport_label
        self.snapshot_interval_seconds = max(
            0.0, float(snapshot_interval_seconds)
        )
        self._state = _CombatState()
        self._lock = threading.RLock()

    @property
    def encounter_id(self) -> Optional[str]:
        return self._state.encounter_id

    def _emit(self, event: str, **fields: Any) -> None:
        payload = {
            "encounter_id": self._state.encounter_id,
            "encounter_active": event != "combat_end",
            "client_type": self.client_label,
            "transport_type": self.transport_label,
        }
        payload.update({key: _bounded(value) for key, value in fields.items()})
        try:
            emit_event(event, **payload)
        except Exception:
            pass

    def begin_encounter(
        self,
        *,
        source: str,
        player: Optional[Any] = None,
        threats: Optional[Iterable[Any]] = None,
        target_id: Any = None,
    ) -> bool:
        """Start an encounter if idle; return whether this call owns it."""
        with self._lock:
            if self._state.encounter_id is not None:
                return False
            now = time.monotonic()
            threat_list = list(threats or [])[:MAX_THREATS]
            self._state = _CombatState(
                encounter_id=uuid4().hex,
                started_at=now,
                last_snapshot_at=now,
                last_health=_health(player),
                source=source,
                threat_health=_threat_health(threat_list),
                target_ids={str(target_id)} if target_id is not None else set(),
            )
            self._emit(
                "combat_start",
                source=source,
                player=_player(player),
                threats=_threats(threat_list),
                target_id=target_id,
            )
            return True

    def record_decision(
        self,
        decision: str,
        *,
        reason: Optional[str] = None,
        player: Optional[Any] = None,
        threats: Optional[Iterable[Any]] = None,
        target: Optional[Any] = None,
    ) -> None:
        with self._lock:
            if self._state.encounter_id is None:
                return
            if isinstance(target, dict) and target.get("id") is not None:
                self._state.target_ids.add(str(target["id"]))
            self._emit(
                "combat_decision",
                decision=decision,
                reason=reason,
                player=_player(player),
                threats=_threats(threats),
                target=_threat(target),
            )

    def record_action(
        self,
        action: str,
        *,
        outcome: str = "attempted",
        target: Optional[Any] = None,
        **fields: Any,
    ) -> None:
        with self._lock:
            if self._state.encounter_id is None:
                return
            if isinstance(target, dict) and target.get("id") is not None:
                self._state.target_ids.add(str(target["id"]))
            self._emit(
                "combat_action",
                action=action,
                outcome=outcome,
                target=_threat(target),
                **fields,
            )

    def record_snapshot(
        self,
        *,
        player: Optional[Any] = None,
        threats: Optional[Iterable[Any]] = None,
        force: bool = False,
    ) -> None:
        """Throttle state samples, but never suppress observed damage."""
        with self._lock:
            if self._state.encounter_id is None:
                return
            now = time.monotonic()
            threat_list = list(threats or [])[:MAX_THREATS]
            health = _health(player)
            before = self._state.last_health
            if before is not None and health is not None and health < before:
                self._emit(
                    "combat_damage",
                    health_before=before,
                    health_after=health,
                    damage=before - health,
                    player=_player(player),
                    threats=_threats(threat_list),
                )
            if health is not None:
                self._state.last_health = health
            current_threat_health = _threat_health(threat_list)
            for entity_id, target_health in current_threat_health.items():
                prior = self._state.threat_health.get(entity_id)
                if (
                    entity_id in self._state.target_ids
                    and prior is not None
                    and target_health < prior
                ):
                    self._emit(
                        "combat_target_damage",
                        target_id=entity_id,
                        health_before=prior,
                        health_after=target_health,
                        damage=prior - target_health,
                    )
            self._state.threat_health.update(current_threat_health)
            elapsed = now - self._state.last_snapshot_at
            if force or elapsed >= self.snapshot_interval_seconds:
                self._emit(
                    "combat_snapshot",
                    player=_player(player),
                    threats=_threats(threat_list),
                    snapshot_interval_s=elapsed,
                )
                self._state.last_snapshot_at = now

    def end_encounter(
        self,
        *,
        outcome: str,
        reason: Optional[str] = None,
        player: Optional[Any] = None,
        threats: Optional[Iterable[Any]] = None,
    ) -> None:
        with self._lock:
            if self._state.encounter_id is None:
                return
            duration = (
                time.monotonic() - self._state.started_at
                if self._state.started_at is not None
                else None
            )
            self.record_snapshot(player=player, threats=threats, force=True)
            self._emit(
                "combat_end",
                outcome=outcome,
                reason=reason,
                duration_s=duration,
                player=_player(player),
                threats=_threats(threats),
            )
            self._state = _CombatState()


def get_combat_telemetry(
    client: Any,
    *,
    snapshot_interval_seconds: float = DEFAULT_SNAPSHOT_INTERVAL_SECONDS,
) -> CombatTelemetryRecorder:
    """Return recorder state attached to one client (or its transport)."""
    attribute = "_mcbaratone_combat_telemetry"
    recorder = getattr(client, attribute, None)
    transport = getattr(client, "transport", client)
    if not isinstance(recorder, CombatTelemetryRecorder):
        recorder = getattr(transport, attribute, None)
    if isinstance(recorder, CombatTelemetryRecorder):
        return recorder
    recorder = CombatTelemetryRecorder(
        client_label=type(client).__name__,
        transport_label=type(transport).__name__,
        snapshot_interval_seconds=snapshot_interval_seconds,
    )
    try:
        setattr(client, attribute, recorder)
    except Exception:
        try:
            setattr(transport, attribute, recorder)
        except Exception:
            pass
    return recorder


def trace_safe_combat(function):
    """Decorate ``safe_combat`` without changing its return contract."""

    @functools.wraps(function)
    def traced(client, target_id, *args, **kwargs):
        recorder = get_combat_telemetry(client)
        owns_encounter = recorder.begin_encounter(
            source="safe_combat", target_id=target_id
        )
        recorder.record_decision("engage", reason="safe_combat", target={"id": target_id})
        try:
            result = function(client, target_id, *args, **kwargs)
        except Exception as exc:
            if owns_encounter:
                recorder.end_encounter(
                    outcome="error", reason=type(exc).__name__
                )
            raise
        if owns_encounter:
            recorder.end_encounter(
                outcome="target_cleared" if result else "disengaged"
            )
        return result

    return traced


def trace_escape(function):
    """Decorate ``run_away`` and keep nested defense encounters open."""

    @functools.wraps(function)
    def traced(client, threat, *args, **kwargs):
        recorder = get_combat_telemetry(client)
        owns_encounter = recorder.begin_encounter(
            source="run_away", threats=[threat], target_id=threat.get("id")
        )
        recorder.record_decision(
            "evade", reason="run_away", threats=[threat], target=threat
        )
        try:
            result = function(client, threat, *args, **kwargs)
        except Exception as exc:
            if owns_encounter:
                recorder.end_encounter(
                    outcome="error", reason=type(exc).__name__, threats=[threat]
                )
            raise
        if owns_encounter:
            recorder.end_encounter(
                outcome="escaped" if result else "escape_failed",
                threats=[threat],
            )
        return result

    return traced


def record_combat_snapshot(client: Any, snapshot: Dict[str, Any]) -> None:
    """Record an atomic snapshot when an encounter is active."""
    get_combat_telemetry(client).record_snapshot(
        player=snapshot.get("player"), threats=snapshot.get("entities")
    )


def record_defense_decision(
    client: Any,
    decision: Any,
    state: Dict[str, Any],
    threats: Iterable[Any],
) -> None:
    """Join defensive ticks into an encounter until the area is clear."""
    recorder = get_combat_telemetry(client)
    threat_list = list(threats)
    mode = getattr(getattr(decision, "mode", None), "value", "unknown")
    if threat_list:
        recorder.begin_encounter(
            source="defend_or_flee", player=state, threats=threat_list
        )
    if recorder.encounter_id is None:
        return
    recorder.record_snapshot(player=state, threats=threat_list)
    recorder.record_decision(
        mode,
        reason=getattr(decision, "reason", None),
        player=state,
        threats=threat_list,
        target=getattr(getattr(decision, "primary", None), "entity", None),
    )
    if mode == "clear":
        recorder.end_encounter(
            outcome="area_clear", player=state, threats=threat_list
        )


def record_combat_action(client: Any, action: str, **fields: Any) -> None:
    """Best-effort convenience hook for runtime combat actions."""
    get_combat_telemetry(client).record_action(action, **fields)


def record_melee_attack(client: Any, target: Any, result: Any) -> None:
    """Record one bridge melee result without expanding legacy combat code."""
    declined = isinstance(result, dict) and result.get("attacked") is False
    record_combat_action(
        client,
        "melee_attack",
        outcome="declined" if declined else "dispatched",
        target=target,
        bridge_result=result,
    )
