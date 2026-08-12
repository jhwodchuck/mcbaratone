"""Dedicated, reusable worker for verified post-dragon End assignments."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional

from ..automator.adaptive_scheduler import AdaptiveScheduler
from ..automator.end_readiness import FleetRole
from ..automator.local_opportunity import OpportunityKind
from ..automator.specialty_scheduler import select_specialty_opportunity
from ..automator.state_manager import Phase
from ..common.combat import defend_or_flee, eat_until_hunger
from ..common.inventory import (
    equip_best_armor,
    equip_best_weapon,
    reset_inventory_cache,
)

END_DIMENSION = "minecraft:the_end"
NON_OP_PROFILE = "non_op_client"
CRITICAL_HEALTH = 8.0
RECOVERY_HEALTH = 18.0
RECOVERY_FOOD = 18
MANUAL_HOLD_KEY = "manual_recovery_hold"


def _unwrap(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    data = value.get("data", value)
    return data if isinstance(data, Mapping) else {}


def _coordinate(value: Any) -> Optional[tuple[int, int, int]]:
    if isinstance(value, Mapping):
        value = value.get("location") or value.get("position")
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return None
    try:
        return tuple(int(float(axis)) for axis in value)  # type: ignore[return-value]
    except (TypeError, ValueError):
        return None


def assigned_end_city(state: Any) -> tuple[int, int, int]:
    """Return the bot's verified End-city assignment or raise fail-closed."""
    custom = getattr(state, "custom_data", {}) or {}
    raw = custom.get("end_city") if isinstance(custom, Mapping) else None
    verified = isinstance(raw, Mapping) and bool(raw.get("verified"))
    location = _coordinate(raw)
    if not verified or location is None:
        raise ValueError("post-dragon worker requires a verified end_city checkpoint")
    return location


@dataclass(frozen=True)
class PostDragonTurn:
    """One bounded worker result suitable for status files and supervision."""

    state: str
    detail: str
    progressed: bool = False
    before: int = 0
    after: int = 0


def non_op_survival_hold_reason(
    snapshot: Mapping[str, Any],
    bot_name: str,
) -> str | None:
    """Return why this bridge cannot safely control the configured bot."""
    if not snapshot:
        return "bridge state is unavailable"
    profile = str(snapshot.get("automation_profile", ""))
    if profile != NON_OP_PROFILE or snapshot.get("server_authority") is not False:
        return "bridge did not attest non_op_client with server_authority=false"
    player_name = str(snapshot.get("player_name", ""))
    if player_name.casefold() != str(bot_name).casefold():
        return f"bridge identity mismatch: expected {bot_name}, got {player_name!r}"
    mode = str(snapshot.get("game_mode", "")).lower()
    if mode != "survival":
        return f"{bot_name} must remain in Survival, found {mode!r}"
    return None


def _survival_metrics(snapshot: Mapping[str, Any]) -> tuple[float, int] | None:
    try:
        return (
            float(snapshot.get("health", 0) or 0),
            int(snapshot.get("food_level", snapshot.get("food", 0)) or 0),
        )
    except (TypeError, ValueError):
        return None


def recover_dead_bot(
    client: Any,
    *,
    bot_name: str,
    sleeper: Callable[[float], None] = time.sleep,
) -> PostDragonTurn | None:
    """Respawn normally and require manual Survival travel afterward."""
    snapshot = _unwrap(client.transport.dispatch("get_state", {}))
    unsafe = non_op_survival_hold_reason(snapshot, bot_name)
    if unsafe is not None:
        return PostDragonTurn("manual_recovery_hold", unsafe)
    metrics = _survival_metrics(snapshot)
    if metrics is None:
        return PostDragonTurn(
            "manual_recovery_hold", "bridge survival telemetry is invalid"
        )
    health, _food = metrics
    dead = bool(snapshot.get("is_dead")) or health <= 0
    if not dead:
        return None
    client.transport.dispatch("cancel", {})
    client.transport.dispatch("respawn", {})
    reset_inventory_cache()
    for _ in range(20):
        sleeper(0.5)
        live = _unwrap(client.transport.dispatch("get_state", {}))
        unsafe = non_op_survival_hold_reason(live, bot_name)
        if unsafe is not None:
            return PostDragonTurn("manual_recovery_hold", unsafe)
        metrics = _survival_metrics(live)
        if metrics is None:
            return PostDragonTurn(
                "manual_recovery_hold",
                "bridge survival telemetry is invalid after respawn",
            )
        health, _food = metrics
        if not bool(live.get("is_dead")) and health > 0:
            return PostDragonTurn(
                "manual_recovery_hold",
                "respawned normally; manual Survival return to the verified "
                "End assignment is required",
            )
    return PostDragonTurn(
        "manual_recovery_hold",
        "bot did not leave the death screen after normal respawn",
    )


def stabilize_critical_bot(
    client: Any,
    *,
    bot_name: str,
) -> PostDragonTurn | None:
    """Cancel critical activity without injecting movement or resources."""
    snapshot = _unwrap(client.transport.dispatch("get_state", {}))
    unsafe = non_op_survival_hold_reason(snapshot, bot_name)
    if unsafe is not None:
        return PostDragonTurn("manual_recovery_hold", unsafe)
    metrics = _survival_metrics(snapshot)
    if metrics is None:
        return PostDragonTurn(
            "manual_recovery_hold", "bridge survival telemetry is invalid"
        )
    if bool(snapshot.get("is_dead")):
        return None
    health, _food = metrics
    if health >= CRITICAL_HEALTH:
        return None
    client.transport.dispatch("cancel", {})
    return PostDragonTurn(
        "manual_recovery_hold",
        f"critical End health={health:.1f}; manual recovery is required",
    )


class PostDragonWorker:
    """Run only recovery and End-city supply work, never campaign phases."""

    def __init__(
        self,
        client: Any,
        resources: Any,
        state: Any,
        *,
        bot_name: str,
        sleeper: Callable[[float], None] = time.sleep,
        scheduler: AdaptiveScheduler | None = None,
    ) -> None:
        self.client = client
        self.resources = resources
        self.state = state
        self.bot_name = str(bot_name)
        self.sleeper = sleeper
        self.scheduler = scheduler or AdaptiveScheduler(client, resources, state)
        self.city = assigned_end_city(state)

    _COOLDOWNS = {
        OpportunityKind.END_SUPPLY: 30.0,
        OpportunityKind.END_CITY_ROUTE: 30.0,
        OpportunityKind.END_FRONTIER: 60.0,
        OpportunityKind.FOOD_RECOVERY: 30.0,
    }

    def _cooldown_ready(self, kind: OpportunityKind, now: float) -> bool:
        runtime = self._runtime_state()
        attempts = runtime.setdefault("last_attempt", {})
        if not isinstance(attempts, dict):
            attempts = {}
            runtime["last_attempt"] = attempts
        cooldown = self._COOLDOWNS.get(kind, 30.0)
        return now - float(attempts.get(kind.value, 0) or 0) >= cooldown

    def _record_attempt(self, kind: OpportunityKind) -> None:
        runtime = self._runtime_state()
        attempts = runtime.setdefault("last_attempt", {})
        attempts[kind.value] = time.time()

    def _snapshot(self) -> Mapping[str, Any]:
        return _unwrap(self.client.transport.dispatch("get_state", {}))

    @staticmethod
    def _in_end(snapshot: Mapping[str, Any]) -> bool:
        return str(snapshot.get("dimension", "")).casefold() == END_DIMENSION

    def _save(self) -> None:
        inventory = self.resources.refresh_inventory()
        self.state.save_checkpoint(inventory)

    def _runtime_state(self) -> dict[str, Any]:
        runtime = self.state.custom_data.setdefault("post_dragon_worker", {})
        if not isinstance(runtime, dict):
            runtime = {}
            self.state.custom_data["post_dragon_worker"] = runtime
        return runtime

    def _hold(self, detail: str, *, refresh_inventory: bool = False) -> PostDragonTurn:
        """Persist a manual-recovery hold without acting through a bad bridge."""
        self._runtime_state()[MANUAL_HOLD_KEY] = {
            "reason": str(detail),
            "timestamp": time.time(),
        }
        inventory = self.resources.refresh_inventory() if refresh_inventory else {}
        self.state.save_checkpoint(inventory)
        return PostDragonTurn("manual_recovery_hold", str(detail))

    def _held_reason(self) -> str | None:
        value = self._runtime_state().get(MANUAL_HOLD_KEY)
        if not isinstance(value, Mapping):
            return None
        reason = str(value.get("reason", "")).strip()
        return reason or "manual recovery remains required"

    def _clear_hold(self) -> None:
        self._runtime_state().pop(MANUAL_HOLD_KEY, None)
        self._save()

    def _equip(self) -> None:
        try:
            self.client.transport.dispatch("close_screen", {})
        except Exception:
            pass
        equip_best_armor(self.client)
        equip_best_weapon(self.client)

    def _respawn(self) -> PostDragonTurn:
        result = recover_dead_bot(
            self.client,
            bot_name=self.bot_name,
            sleeper=self.sleeper,
        )
        if result is None:
            return self._hold("death state changed; verify the bot manually")
        return self._hold(result.detail)

    def _restore_assignment(self) -> PostDragonTurn:
        self.client.transport.dispatch("cancel", {})
        return self._hold(
            "bot is outside the End; return through normal Survival travel "
            "before resuming"
        )

    def run_turn(self) -> PostDragonTurn:
        """Run one recovery, defense, or bounded End supply turn."""
        snapshot = self._snapshot()
        unsafe = non_op_survival_hold_reason(snapshot, self.bot_name)
        if unsafe is not None:
            return self._hold(unsafe)
        metrics = _survival_metrics(snapshot)
        if metrics is None:
            return self._hold("bridge survival telemetry is invalid")
        health, food = metrics
        if bool(snapshot.get("is_dead")) or health <= 0:
            return self._respawn()
        if not self._in_end(snapshot):
            return self._restore_assignment()

        if health < CRITICAL_HEALTH:
            self.client.transport.dispatch("cancel", {})
            return self._hold(
                f"critical End health={health:.1f}; manual recovery is required"
            )
        held_reason = self._held_reason()
        if held_reason is not None:
            if health < RECOVERY_HEALTH or food < RECOVERY_FOOD:
                self.client.transport.dispatch("cancel", {})
                return PostDragonTurn("manual_recovery_hold", held_reason)
            self._clear_hold()

        self._equip()
        if defend_or_flee(self.client, allow_safe_recovery_movement=True):
            return PostDragonTurn("defending", "defense or evasion took priority")

        snapshot = self._snapshot()
        unsafe = non_op_survival_hold_reason(snapshot, self.bot_name)
        if unsafe is not None:
            return self._hold(unsafe)
        metrics = _survival_metrics(snapshot)
        if metrics is None:
            return self._hold("bridge survival telemetry is invalid")
        health, food = metrics
        if bool(snapshot.get("is_dead")) or health <= 0:
            return self._respawn()
        if not self._in_end(snapshot):
            return self._restore_assignment()
        # Match the stronger admission used when shulkers are present.  A
        # looser gate here left a 17-health/17-food bot alive but permanently
        # ineligible for its End hunt, so it waited without ever healing.
        if health < 18.0 or food < 18:
            self.client.transport.dispatch("cancel", {})
            if not eat_until_hunger(self.client, minimum_food=18):
                return self._hold(
                    "carried food could not restore the required End Survival margin",
                    refresh_inventory=True,
                )
            self.sleeper(5.0)
            return PostDragonTurn(
                "recovering",
                f"holding for survival margin from health={health:.1f}, food={food}",
            )

        signals = self.scheduler.observe()
        opportunity = select_specialty_opportunity(
            client=self.client,
            state=self.state,
            role=FleetRole.END_RUNNER,
            signals=signals,
            completed=(Phase.SPAWN_BOOTSTRAP,),
            current_time=time.time(),
            cooldown_ready=self._cooldown_ready,
            allow_recovery=True,
        )
        if opportunity is None:
            return PostDragonTurn("waiting", "no safe post-dragon opportunity is ready")

        result = self.scheduler.run_local_opportunity(opportunity)
        self._record_attempt(opportunity.kind)
        self._save()
        return PostDragonTurn(
            "working" if result.success else "searching",
            f"{opportunity.kind.value}: {result.detail}",
            progressed=result.success,
            before=result.before,
            after=result.after,
        )


__all__ = [
    "PostDragonTurn",
    "PostDragonWorker",
    "assigned_end_city",
    "non_op_survival_hold_reason",
    "recover_dead_bot",
    "stabilize_critical_bot",
]
