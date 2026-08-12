"""Project-native orchestration and readiness adapters for a family End run."""

from __future__ import annotations

from dataclasses import replace
import json
import math
import os
from pathlib import Path
import threading
import time
from typing import Any, Callable, Mapping, Optional, Sequence

from ...common.combat import defend_or_flee, look_at_entity
from ...common.end import enter_end_portal
from ...common.inventory import equip_best_weapon, reset_inventory_cache, select_item
from ...common.navigation import goto
from ...common.resources import ensure_supplies
from ..bridge_guard import NonOpClientGuard
from ..camp_armor import provision_camp_armor
from .end_sequence import (
    DragonRole,
    DragonSupportCoordinator,
    DragonSupportOutcome,
    PortalSequence,
    inspect_portal,
)
from .escort import (
    EscortDecision,
    EscortFleet,
    EscortWorker,
    formation_offsets,
    is_safe_hostile_target,
)
from .leader_tracker import RconLeaderTracker
from .models import (
    BotReadiness,
    ExpeditionCheckpoint,
    ExpeditionConfig,
    ExpeditionPhase,
    OperatorAction,
    PlayerSnapshot,
    ReadinessReport,
    Vec3,
)
from .provisioning import provision_expedition_food


def _unwrap(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    nested = value.get("data")
    return nested if isinstance(nested, Mapping) else value


class CheckpointStore:
    """Atomic JSON persistence for HOLD/resume and operator status."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> ExpeditionCheckpoint:
        try:
            return ExpeditionCheckpoint.from_dict(json.loads(self.path.read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError):
            return ExpeditionCheckpoint()

    def save(self, checkpoint: ExpeditionCheckpoint) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(checkpoint.to_dict(), indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, self.path)


def _items(payload: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    values = []
    for key in ("inventory", "armor", "offhand"):
        raw = payload.get(key, ())
        if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
            values.extend(item for item in raw if isinstance(item, Mapping))
    return tuple(values)


def _item_id(item: Mapping[str, Any]) -> str:
    return str(item.get("id", item.get("item", item.get("name", "")))).casefold()


def _item_count(item: Mapping[str, Any]) -> int:
    try:
        return int(item.get("count", 1))
    except (TypeError, ValueError):
        return 0


def _equipped_armor(payload: Mapping[str, Any], state: Mapping[str, Any]) -> int:
    armor = payload.get("armor", ())
    if isinstance(armor, Sequence) and not isinstance(armor, (str, bytes)):
        return sum(
            1
            for item in armor
            if isinstance(item, Mapping)
            and _item_count(item) > 0
            and _item_id(item) not in {"", "minecraft:air", "air"}
        )
    try:
        return int(state.get("armor_count", 0))
    except (TypeError, ValueError):
        return 0


def evaluate_bot_readiness(client: Any, bot_name: str) -> BotReadiness:
    """Read actual bridge state and equipped inventory; assumptions fail closed."""
    try:
        guard = NonOpClientGuard(client, bot_name)
        state = guard.require_safe()
        inventory = _unwrap(client.transport.dispatch("get_inventory", {}))
        carried = _items(inventory)
        ids = tuple(_item_id(item) for item in carried if _item_count(item) > 0)
        armor_pieces = _equipped_armor(inventory, state)
        health = float(state.get("health", 0) or 0)
        food = int(state.get("food_level", state.get("food", 0)) or 0)
        weapon = any(item.endswith(("_sword", "_axe", ":bow", ":crossbow")) for item in ids)
        shield = "minecraft:shield" in ids
        bow = "minecraft:bow" in ids
        arrows = sum(
            _item_count(item)
            for item in carried
            if _item_id(item) == "minecraft:arrow"
        )
        eyes = sum(
            _item_count(item)
            for item in carried
            if _item_id(item) in {"minecraft:ender_eye", "minecraft:eye_of_ender"}
        )
        return BotReadiness(
            bot_name=bot_name,
            online=True,
            alive=not bool(state.get("is_dead")) and health > 0,
            survival=str(state.get("game_mode", "")).casefold() == "survival",
            armor_pieces=armor_pieces,
            health=health,
            food=food,
            has_weapon=weapon,
            has_shield=shield,
            eye_count=eyes,
            has_bow=bow,
            arrow_count=arrows,
        )
    except Exception as error:
        return BotReadiness(
            bot_name=bot_name,
            online=False,
            alive=False,
            survival=False,
            armor_pieces=0,
            health=0.0,
            food=0,
            has_weapon=False,
            has_shield=False,
            reason=str(error),
        )


def collect_readiness(
    bot_clients: Mapping[str, Any],
    config: ExpeditionConfig,
    clock: Callable[[], float] = time.monotonic,
) -> ReadinessReport:
    return ReadinessReport(
        tuple(evaluate_bot_readiness(bot_clients[name], name) for name in config.bot_names),
        float(clock()),
    )


def prepare_armor(
    bot_clients: Mapping[str, Any],
    manifest: Mapping[str, Any],
    config: ExpeditionConfig,
) -> ReadinessReport:
    """Provision from camp resources, then verify equipped armor again."""
    expedition = manifest.get("family_expedition", {})
    if not isinstance(expedition, Mapping):
        expedition = {}
    storage_values = expedition.get(
        "provisioning_storage",
        manifest.get("provisioning_storage", (manifest.get("storage_input"),)),
    )
    storage = tuple(
        tuple(int(axis) for axis in position)
        for position in storage_values
        if isinstance(position, Sequence)
    )
    minimum_torches = int(manifest.get("surface_expedition_safety", {}).get("minimum_torches", 0))
    for name in config.bot_names:
        readiness = evaluate_bot_readiness(bot_clients[name], name)
        if readiness.armor_pieces >= config.required_armor_pieces:
            continue
        bot = manifest.get("bots", {}).get(name, {})
        crafting_table = expedition.get("crafting_table", bot["crafting_table"])
        furnace = expedition.get("furnace", bot["furnace"])
        success, _detail = provision_camp_armor(
            bot_clients[name],
            storage_sources=storage,
            furnace=tuple(int(axis) for axis in furnace),
            crafting_table=tuple(int(axis) for axis in crafting_table),
            target_pieces=config.required_armor_pieces,
            minimum_torches=minimum_torches,
        )
        verified = evaluate_bot_readiness(bot_clients[name], name)
        if (
            not success
            or not verified.alive
            or not verified.survival
            or verified.armor_pieces < config.required_armor_pieces
        ):
            break
    return collect_readiness(bot_clients, config)


def prepare_food_reserves(
    bot_clients: Mapping[str, Any],
    manifest: Mapping[str, Any],
    config: ExpeditionConfig,
) -> ReadinessReport:
    """Feed every live expedition bot and verify a carried reserve afterward."""
    expedition = manifest.get("family_expedition", {})
    if not isinstance(expedition, Mapping):
        expedition = {}
    storage_values = expedition.get(
        "provisioning_storage",
        manifest.get("provisioning_storage", (manifest.get("storage_input"),)),
    )
    storage = tuple(
        tuple(int(axis) for axis in position)
        for position in storage_values
        if isinstance(position, Sequence)
    )
    minimum_reserve = int(
        manifest.get("surface_expedition_safety", {}).get(
            "minimum_food_reserve", 8
        )
    )
    crafting_table_value = expedition.get("crafting_table")
    crafting_table = (
        tuple(int(axis) for axis in crafting_table_value)
        if isinstance(crafting_table_value, Sequence)
        else None
    )
    provision_expedition_food(
        bot_clients,
        config.bot_names,
        storage_sources=storage,
        crafting_table=crafting_table,
        minimum_reserve=minimum_reserve,
        required_food=config.required_food,
    )
    return collect_readiness(bot_clients, config)


def recover_dead_bots(
    bot_clients: Mapping[str, Any],
    config: ExpeditionConfig,
    *,
    protected_names: Sequence[str] = (),
    attempts: int = 8,
    clear_polls: int = 2,
    poll_interval: float = 1.0,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, bool]:
    """Respawn dead expedition members only after consecutive clear scans."""
    outcomes: dict[str, bool] = {}
    for name in config.bot_names:
        client = bot_clients[name]
        try:
            guard = NonOpClientGuard(client, name)
            state = guard.state()
            if (
                str(state.get("automation_profile", "")) != NonOpClientGuard.PROFILE
                or state.get("server_authority") is not False
                or str(state.get("player_name", "")).casefold() != name.casefold()
            ):
                outcomes[name] = False
                continue
            guard.require_survival(state)
            dead = bool(state.get("is_dead")) or float(state.get("health", 0) or 0) <= 0
            if not dead:
                outcomes[name] = True
                continue
            # A Baritone goal survives the death screen. Cancel it before the
            # respawn so the bot cannot immediately walk back into the route
            # that killed it.
            client.transport.dispatch("cancel", {})
            consecutive_clear = 0
            for _attempt in range(max(1, int(attempts))):
                try:
                    entities = _unwrap(
                        client.transport.dispatch("get_entities", {"radius": 16})
                    ).get("entities", ())
                    hostiles = tuple(
                        entity
                        for entity in entities
                        if isinstance(entity, Mapping)
                        and is_safe_hostile_target(entity, protected_names)
                    )
                except Exception:
                    hostiles = ({"type": "telemetry_unavailable"},)
                consecutive_clear = consecutive_clear + 1 if not hostiles else 0
                if consecutive_clear >= max(1, int(clear_polls)):
                    client.transport.dispatch("respawn", {})
                    client.transport.dispatch("cancel", {})
                    reset_inventory_cache()
                    for _ in range(10):
                        sleeper(max(0.0, poll_interval))
                        live = guard.state()
                        if (
                            not bool(live.get("is_dead"))
                            and float(live.get("health", 0) or 0) > 0
                        ):
                            outcomes[name] = True
                            break
                    break
                sleeper(max(0.0, poll_interval))
            outcomes.setdefault(name, False)
        except Exception:
            outcomes[name] = False
    return outcomes


def prepare_expedition(
    bot_clients: Mapping[str, Any],
    manifest: Mapping[str, Any],
    config: ExpeditionConfig,
    *,
    protected_names: Sequence[str] = (),
) -> ReadinessReport:
    """Recover, provision armor, ensure one weapon each, and equip loadouts."""
    recover_dead_bots(
        bot_clients,
        config,
        protected_names=protected_names,
    )
    prepare_armor(bot_clients, manifest, config)
    prepare_food_reserves(bot_clients, manifest, config)
    for name in config.bot_names:
        readiness = evaluate_bot_readiness(bot_clients[name], name)
        if not readiness.alive or not readiness.survival:
            continue
        if not readiness.has_weapon:
            try:
                ensure_supplies(
                    bot_clients[name],
                    {"minecraft:iron_sword": 1},
                    poll_interval=1.0,
                    timeout=90,
                )
            except Exception:
                pass
        try:
            equip_best_weapon(bot_clients[name])
        except Exception:
            pass
    return collect_readiness(bot_clients, config)


def _position(state: Mapping[str, Any]) -> Optional[Vec3]:
    value = state.get("position", state.get("block_position"))
    return Vec3.from_dict(value)


class FleetTrackerAdapter:
    """Share only the leader snapshot; every bot retains its own bridge client."""

    def __init__(
        self,
        leader_tracker: RconLeaderTracker,
        bot_clients: Mapping[str, Any],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.leader_tracker = leader_tracker
        self.bot_clients = dict(bot_clients)
        self.clock = clock
        self._leader: Optional[PlayerSnapshot] = None
        self._lock = threading.Lock()

    def refresh_leader(self) -> PlayerSnapshot:
        with self._lock:
            try:
                self._leader = self.leader_tracker.snapshot()
            except Exception:
                self._leader = PlayerSnapshot(
                    name=self.leader_tracker.leader_name,
                    online=False,
                    observed_at_monotonic=float(self.clock()),
                    observed_at_wall=time.time(),
                )
            return self._leader

    def get_snapshot(self, name: str) -> Optional[PlayerSnapshot]:
        if name == self.leader_tracker.leader_name:
            return self._leader
        client = self.bot_clients.get(name)
        if client is None:
            return None
        try:
            state = _unwrap(client.transport.dispatch("get_state", {}))
            return PlayerSnapshot(
                name=name,
                online=True,
                position=_position(state),
                rotation=(float(state.get("yaw", 0)), float(state.get("pitch", 0))),
                dimension=str(state.get("dimension", "")),
                health=float(state.get("health", 0) or 0),
                food=int(state.get("food_level", state.get("food", 0)) or 0),
                game_mode=str(state.get("game_mode", "")),
                observed_at_monotonic=float(self.clock()),
                observed_at_wall=time.time(),
            )
        except Exception:
            return None

    def get_entities(self, name: str) -> Sequence[Mapping[str, Any]]:
        client = self.bot_clients[name]
        payload = _unwrap(client.transport.dispatch("get_entities", {"radius": 16}))
        return tuple(item for item in payload.get("entities", ()) if isinstance(item, Mapping))

    def cancel(self, name: str) -> None:
        client = self.bot_clients.get(name)
        if client is not None:
            try:
                client.transport.dispatch("cancel", {})
            except Exception:
                pass


def build_escort_fleet(
    tracker: FleetTrackerAdapter,
    config: ExpeditionConfig,
    protected_names: Sequence[str],
) -> EscortFleet:
    if len(config.bot_names) != 6 or len(set(config.bot_names)) != 6:
        raise ValueError("A live family expedition requires exactly six unique bot names")
    slots = formation_offsets(config.bot_names)

    def navigate(name: str, target: Vec3, timeout: float) -> bool:
        client = tracker.bot_clients[name]
        before = tracker.get_snapshot(name)
        reached = goto(
            client,
            round(target.x),
            round(target.y),
            round(target.z),
            timeout=max(1, round(timeout)),
            check_interval=0.5,
            tolerance=config.movement_tolerance,
        )
        if reached:
            return True
        after = tracker.get_snapshot(name)
        if (
            before is None
            or after is None
            or before.position is None
            or after.position is None
            or before.dimension != after.dimension
            or (after.health or 0) < config.required_health
            or (after.food or 0) < config.required_food
            or str(after.game_mode or "").casefold() != "survival"
            or bool(getattr(client, "_last_navigation_survival_abort", False))
        ):
            return False
        moved = math.dist(
            (before.position.x, before.position.y, before.position.z),
            (after.position.x, after.position.y, after.position.z),
        )
        before_distance = math.dist(
            (before.position.x, before.position.y, before.position.z),
            (target.x, target.y, target.z),
        )
        after_distance = math.dist(
            (after.position.x, after.position.y, after.position.z),
            (target.x, target.y, target.z),
        )
        return (
            moved >= config.navigation_progress_distance
            and before_distance - after_distance >= config.navigation_progress_distance
        )

    def defend(name: str, _hostiles: Sequence[Mapping[str, Any]]) -> bool:
        return bool(defend_or_flee(tracker.bot_clients[name]))

    return EscortFleet(
        tuple(
            EscortWorker(
                name,
                tracker.leader_tracker.leader_name,
                slots[name],
                tracker,
                navigate,
                defend,
                tracker.clock,
                protected_names=protected_names,
                stale_seconds=config.stale_threshold_seconds,
                movement_tolerance=config.movement_tolerance,
                min_health=config.required_health,
                min_food=config.required_food,
                navigator_timeout=config.navigation_timeout_seconds,
            )
            for name in config.bot_names
        )
    )


def default_dragon_callback(
    _name: str,
    client: Any,
    role: DragonRole,
    target: Optional[Mapping[str, Any]],
) -> bool:
    """One bounded role tick; targeting is assigned centrally."""
    if role in {DragonRole.CHILD_GUARD, DragonRole.RESERVE}:
        defend_or_flee(client)
        return True
    if target is None:
        return True
    position = target.get("position", {})
    if not isinstance(position, Mapping):
        return False
    if role is DragonRole.CRYSTAL_ARCHER:
        if not select_item(client, "minecraft:bow", allow_swap=True):
            return False
        if not look_at_entity(client, dict(target)):
            return False
        response = _unwrap(
            client.transport.dispatch(
                "use_item",
                {"hand": "MAIN_HAND", "duration_ms": 1100},
            )
        )
        return bool(response.get("holding") or response.get("used"))
    try:
        dragon_x = float(position["x"])
        dragon_y = float(position["y"])
        dragon_z = float(position["z"])
    except (KeyError, TypeError, ValueError):
        return False
    if abs(dragon_x) > 12 or abs(dragon_z) > 12 or dragon_y > 85:
        return True
    state = _unwrap(client.transport.dispatch("get_state", {}))
    bot_position = _position(state)
    if bot_position is None:
        return False
    center_distance = math.dist(
        (bot_position.x, bot_position.y, bot_position.z),
        (0.0, 64.0, 0.0),
    )
    if center_distance > 8.0:
        before = bot_position
        reached = goto(client, 0, 64, 0, timeout=8, tolerance=6.0)
        if not reached:
            after = _position(_unwrap(client.transport.dispatch("get_state", {})))
            if (
                after is None
                or bool(getattr(client, "_last_navigation_survival_abort", False))
                or math.dist((before.x, before.y, before.z), (after.x, after.y, after.z))
                < 1.5
            ):
                return False
        return True
    if not look_at_entity(client, dict(target)) or not equip_best_weapon(client):
        return False
    target_id = target.get("id")
    if target_id is None:
        return False
    response = _unwrap(
        client.transport.dispatch(
            "attack_entity",
            {"entity_id": int(target_id), "min_cooldown": 0.9},
        )
    )
    return response.get("attacked") is True or response.get("reason") == "cooldown"


class ExpeditionCoordinator:
    """Fail-closed state machine controlled by explicit operator actions."""

    def __init__(
        self,
        config: ExpeditionConfig,
        store: CheckpointStore,
        leader_tracker: RconLeaderTracker,
        bot_clients: Mapping[str, Any],
        escort: EscortFleet,
        fleet_tracker: FleetTrackerAdapter,
        portal: PortalSequence,
        dragon: DragonSupportCoordinator,
    ) -> None:
        self.config = config
        self.store = store
        self.leader_tracker = leader_tracker
        self.bot_clients = dict(bot_clients)
        self.escort = escort
        self.fleet_tracker = fleet_tracker
        self.portal = portal
        self.dragon = dragon
        loaded = store.load()
        configured_names = set(config.bot_names)
        entered_bots = tuple(name for name in loaded.entered_bots if name in configured_names)
        self.checkpoint = replace(loaded, config=config, entered_bots=entered_bots)
        if (
            loaded.phase is ExpeditionPhase.DRAGON_SUPPORT
            and loaded.last_action is OperatorAction.START_FIGHT
        ):
            self.checkpoint = replace(
                self.checkpoint,
                reason="fight awaiting fresh authorization after restart",
                last_action=None,
            )
            self.store.save(self.checkpoint)
        self.portal.entered_bots.update(entered_bots)
        restore_progress = getattr(self.dragon, "restore_progress", None)
        if callable(restore_progress):
            restore_progress(
                loaded.dragon_seen,
                loaded.dragon_completion_observations,
            )

    def _set(self, phase: ExpeditionPhase, reason: str = "", **changes: Any) -> ExpeditionCheckpoint:
        if hasattr(self.dragon, "dragon_seen"):
            changes.setdefault("dragon_seen", bool(self.dragon.dragon_seen))
        if hasattr(self.dragon, "completion_observations"):
            changes.setdefault(
                "dragon_completion_observations",
                int(self.dragon.completion_observations),
            )
        self.checkpoint = replace(self.checkpoint, phase=phase, reason=reason, **changes)
        self.store.save(self.checkpoint)
        return self.checkpoint

    def preflight(self) -> ReadinessReport:
        report = collect_readiness(self.bot_clients, self.config)
        self._set(ExpeditionPhase.PREFLIGHT, "readiness measured", readiness=report)
        return report

    def hold(self, reason: str) -> ExpeditionCheckpoint:
        if self.checkpoint.phase not in {ExpeditionPhase.COMPLETE, ExpeditionPhase.ABORTED}:
            self.escort.cancel_all()
            prior_phase = (
                self.checkpoint.prior_phase
                if self.checkpoint.phase is ExpeditionPhase.HOLD
                else self.checkpoint.phase
            )
            return self._set(
                ExpeditionPhase.HOLD,
                reason,
                prior_phase=prior_phase,
                last_action=OperatorAction.HOLD,
            )
        return self.checkpoint

    def apply_action(self, action: OperatorAction) -> ExpeditionCheckpoint:
        if action is OperatorAction.ABORT:
            self.escort.cancel_all()
            return self._set(ExpeditionPhase.ABORTED, "operator abort", last_action=action)
        if action is OperatorAction.HOLD:
            return self.hold("operator hold")
        if action is OperatorAction.RESUME:
            if self.checkpoint.phase is ExpeditionPhase.HOLD:
                return self._set(self.checkpoint.resume_phase, "operator resume", prior_phase=None, last_action=action)
            return self.checkpoint
        if action is OperatorAction.START:
            if self.checkpoint.phase is not ExpeditionPhase.PREFLIGHT:
                return self.checkpoint
            report = collect_readiness(self.bot_clients, self.config)
            if not report.all_ready(self.config):
                return self._set(ExpeditionPhase.HOLD, "preflight failed", prior_phase=ExpeditionPhase.PREFLIGHT, readiness=report, last_action=action)
            return self._set(ExpeditionPhase.WAITING_FOR_LEADER, "ready; waiting for leader", readiness=report, last_action=action)
        if action is OperatorAction.ACTIVATE_PORTAL:
            return self._activate_portal(action)
        if action is OperatorAction.ENTER_PORTAL:
            return self._enter_portal(action)
        if action is OperatorAction.START_FIGHT and self.checkpoint.phase is ExpeditionPhase.DRAGON_SUPPORT:
            return self._set(ExpeditionPhase.DRAGON_SUPPORT, "dragon support authorized", last_action=action)
        return self.checkpoint

    def _activate_portal(self, action: OperatorAction) -> ExpeditionCheckpoint:
        if self.checkpoint.phase is not ExpeditionPhase.PORTAL_HOLD:
            return self.checkpoint
        carrier = self.config.eye_carrier
        report = collect_readiness(self.bot_clients, self.config)
        readiness = report.by_name(carrier)
        inspection = inspect_portal(self.bot_clients[carrier])
        readiness_ok = (
            report.combat_ready(self.config)
            if inspection.active
            else report.all_ready(self.config)
        )
        if not readiness_ok:
            return self.hold("fleet readiness failed before portal activation")
        if readiness is None:
            return self.hold("eye carrier readiness unavailable")
        self._set(ExpeditionPhase.PORTAL_HOLD, "activation authorized", readiness=report, last_action=action)
        self.portal.request_activation()
        if not self.portal.activate(carrier, self.bot_clients[carrier], readiness, self.config, inspection):
            return self.hold("portal activation failed verification")
        return self._set(ExpeditionPhase.PORTAL_ACTIVE, "portal active")

    def _enter_portal(self, action: OperatorAction) -> ExpeditionCheckpoint:
        if self.checkpoint.phase is not ExpeditionPhase.PORTAL_ACTIVE:
            return self.checkpoint
        leader = self.fleet_tracker.refresh_leader()
        fresh = self.leader_tracker.is_fresh(leader)
        inspection = inspect_portal(self.bot_clients[self.config.eye_carrier])
        self.portal.request_entry()
        if not self.portal.entry_allowed(leader, leader_fresh=fresh, portal_active=inspection.active):
            return self.hold("leader must enter The End first")
        self._set(ExpeditionPhase.ENTERING_END, "sequential entry authorized", leader_snapshot=leader, last_action=action)
        entered = self.portal.enter_bots(self.bot_clients, inspection.center)
        if set(entered) != set(self.config.bot_names):
            # Resume to the non-mutating active-portal gate. A fresh ENTER command
            # is required before retrying any remaining bot entry.
            self._set(ExpeditionPhase.PORTAL_ACTIVE, "partial bot entry", entered_bots=entered)
            return self.hold("not every bot entered The End")
        return self._set(ExpeditionPhase.DRAGON_SUPPORT, "all bots in The End; fight awaiting authorization", entered_bots=entered)

    def tick(self) -> ExpeditionCheckpoint:
        phase = self.checkpoint.phase
        if phase is ExpeditionPhase.WAITING_FOR_LEADER:
            leader = self.fleet_tracker.refresh_leader()
            if self.leader_tracker.is_fresh(leader):
                return self._set(ExpeditionPhase.RENDEZVOUS, "leader found", leader_snapshot=leader)
            return self._set(phase, "waiting for fresh leader telemetry", leader_snapshot=leader)
        if phase in {ExpeditionPhase.RENDEZVOUS, ExpeditionPhase.ESCORTING}:
            leader = self.fleet_tracker.refresh_leader()
            if not self.leader_tracker.is_fresh(leader):
                return self.hold("leader telemetry became stale")
            result = self.escort.tick()
            if result.decision is EscortDecision.ABORT:
                return self._set(ExpeditionPhase.ABORTED, result.reason)
            if result.decision is EscortDecision.HOLD:
                return self.hold(result.reason)
            if phase is ExpeditionPhase.RENDEZVOUS and result.decision is EscortDecision.STAY:
                return self._set(ExpeditionPhase.ESCORTING, "fleet rendezvous complete", leader_snapshot=leader)
            if phase is ExpeditionPhase.ESCORTING:
                inspection = inspect_portal(self.bot_clients[self.config.eye_carrier])
                if inspection.frame_count >= 12:
                    self.escort.cancel_all()
                    return self._set(ExpeditionPhase.PORTAL_HOLD, "portal room reached; awaiting activation", leader_snapshot=leader)
            return self._set(phase, result.reason, leader_snapshot=leader)
        if phase is ExpeditionPhase.DRAGON_SUPPORT and self.checkpoint.last_action is OperatorAction.START_FIGHT:
            leader = self.fleet_tracker.refresh_leader()
            outcome = self.dragon.run_tick(
                self.bot_clients,
                self.bot_clients[self.config.eye_carrier],
                leader,
                leader_fresh=self.leader_tracker.is_fresh(leader),
            )
            if outcome is DragonSupportOutcome.HOLD:
                return self._set(
                    phase,
                    "dragon support retrying",
                    leader_snapshot=leader,
                )
            if outcome is DragonSupportOutcome.COMPLETE:
                return self._set(ExpeditionPhase.COMPLETE, "dragon defeated", leader_snapshot=leader)
            return self._set(phase, "dragon support running", leader_snapshot=leader)
        return self.checkpoint


def build_coordinator(
    config: ExpeditionConfig,
    store: CheckpointStore,
    leader_tracker: RconLeaderTracker,
    bot_clients: Mapping[str, Any],
    protected_names: Sequence[str],
) -> ExpeditionCoordinator:
    tracker = FleetTrackerAdapter(leader_tracker, bot_clients)
    escort = build_escort_fleet(tracker, config, protected_names)
    portal = PortalSequence(config.eye_carrier)
    callbacks = {name: default_dragon_callback for name in config.bot_names}
    dragon = DragonSupportCoordinator(
        callbacks,
        tuple(protected_names) + (leader_tracker.leader_name,),
        required_archers=config.required_archers,
        required_arrows_per_archer=config.required_arrows_per_archer,
        hold_on_callback_failure=False,
    )
    return ExpeditionCoordinator(config, store, leader_tracker, bot_clients, escort, tracker, portal, dragon)


__all__ = [
    "CheckpointStore",
    "ExpeditionCoordinator",
    "FleetTrackerAdapter",
    "build_coordinator",
    "collect_readiness",
    "evaluate_bot_readiness",
    "prepare_expedition",
    "prepare_armor",
    "recover_dead_bots",
]
