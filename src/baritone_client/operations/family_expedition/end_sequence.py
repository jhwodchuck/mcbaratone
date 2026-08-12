"""Explicit portal and coordinated dragon-support gates."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from enum import Enum
import threading
import time
from typing import Any, Callable, Mapping, Optional, Sequence, Tuple

from ...common.end import activate_end_portal, enter_end_portal
from .models import BotReadiness, ExpeditionConfig, PlayerSnapshot


def _unwrap(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    nested = value.get("data")
    return nested if isinstance(nested, Mapping) else value


@dataclass(frozen=True)
class PortalInspection:
    frame_count: int
    missing_eyes: int
    active: bool
    center: Optional[Tuple[int, int, int]]


def inspect_portal(client: Any, radius: int = 48) -> PortalInspection:
    try:
        payload = _unwrap(
            client.transport.dispatch(
                "find_blocks",
                {
                    "blocks": ["minecraft:end_portal_frame", "minecraft:end_portal"],
                    "radius": radius,
                    "limit": 64,
                },
            )
        )
        found = tuple(item for item in payload.get("found", ()) if isinstance(item, Mapping))
        frames = tuple(item for item in found if item.get("block") == "minecraft:end_portal_frame")
        active = any(item.get("block") == "minecraft:end_portal" for item in found)
        missing = 0
        coordinates: list[Tuple[int, int, int]] = []
        for frame in frames:
            coordinate = (int(frame["x"]), int(frame["y"]), int(frame["z"]))
            coordinates.append(coordinate)
            block = _unwrap(
                client.transport.dispatch(
                    "get_block",
                    {"x": coordinate[0], "y": coordinate[1], "z": coordinate[2]},
                )
            )
            state = block.get("state", {})
            if str(state.get("eye", "false")).casefold() != "true":
                missing += 1
        center = None
        if coordinates:
            center = tuple(round(sum(value[index] for value in coordinates) / len(coordinates)) for index in range(3))
        return PortalInspection(len(frames), missing, active, center)
    except Exception:
        # Portal mutation must never proceed from incomplete or malformed telemetry.
        return PortalInspection(0, 0, False, None)


class PortalSequence:
    """Keep activation and entry independently operator-gated."""

    def __init__(
        self,
        eye_carrier: str = "Bot07",
        activate_fn: Callable[[Any], bool] = activate_end_portal,
        enter_fn: Callable[..., bool] = enter_end_portal,
    ) -> None:
        self.eye_carrier = eye_carrier
        self.activate_fn = activate_fn
        self.enter_fn = enter_fn
        self.activation_requested = False
        self.entry_requested = False
        self.entered_bots: set[str] = set()

    def request_activation(self) -> None:
        self.activation_requested = True

    def request_entry(self) -> None:
        self.entry_requested = True

    def activate(
        self,
        carrier_name: str,
        carrier_client: Any,
        readiness: BotReadiness,
        config: ExpeditionConfig,
        inspection: PortalInspection,
    ) -> bool:
        if not self.activation_requested or carrier_name != self.eye_carrier:
            return False
        if inspection.frame_count < 12 or inspection.active:
            return inspection.active
        if not readiness.ready_for(config) or readiness.eye_count < inspection.missing_eyes:
            return False
        if not self.activate_fn(carrier_client):
            return False
        return inspect_portal(carrier_client).active

    def entry_allowed(
        self,
        leader: PlayerSnapshot,
        *,
        leader_fresh: bool,
        portal_active: bool,
    ) -> bool:
        return self.entry_requested and portal_active and leader_fresh and leader.online and leader.in_end

    def enter_bots(
        self,
        bot_clients: Mapping[str, Any],
        portal_center: Optional[Sequence[int]],
    ) -> Tuple[str, ...]:
        if not self.entry_requested:
            return tuple(sorted(self.entered_bots))
        for name in sorted(bot_clients):
            if name in self.entered_bots:
                continue
            if not self.enter_fn(bot_clients[name], portal=portal_center):
                break
            self.entered_bots.add(name)
        return tuple(sorted(self.entered_bots))


class DragonRole(str, Enum):
    CRYSTAL_ARCHER = "CRYSTAL_ARCHER"
    PERCH_ATTACKER = "PERCH_ATTACKER"
    CHILD_GUARD = "CHILD_GUARD"
    RESERVE = "RESERVE"


def assign_dragon_roles(
    bot_names: Sequence[str],
    archer_names: Optional[Sequence[str]] = None,
) -> dict[str, DragonRole]:
    """Assign deterministic roles while honoring verified bow loadouts."""
    names = sorted(set(str(name) for name in bot_names))[:6]
    requested = names if archer_names is None else sorted(set(archer_names))
    archers = [name for name in requested if name in names][:2]
    remaining = [name for name in names if name not in archers]
    assignments = {name: DragonRole.CRYSTAL_ARCHER for name in archers}
    support_roles = (
        DragonRole.PERCH_ATTACKER,
        DragonRole.PERCH_ATTACKER,
        DragonRole.CHILD_GUARD,
        DragonRole.RESERVE,
    )
    assignments.update(
        {
            name: support_roles[min(index, len(support_roles) - 1)]
            for index, name in enumerate(remaining)
        }
    )
    return assignments


class CrystalReservationPool:
    def __init__(self, ttl_seconds: float = 20.0, clock: Callable[[], float] = time.monotonic) -> None:
        self.ttl_seconds = ttl_seconds
        self.clock = clock
        self._values: dict[str, tuple[str, float]] = {}
        self._lock = threading.Lock()

    def reserve(self, crystal_id: str, bot_name: str) -> bool:
        with self._lock:
            self._purge()
            current = self._values.get(crystal_id)
            if current and current[0] != bot_name:
                return False
            self._values[crystal_id] = (bot_name, self.clock() + self.ttl_seconds)
            return True

    def release(self, crystal_id: str, bot_name: str) -> None:
        with self._lock:
            if self._values.get(crystal_id, (None,))[0] == bot_name:
                self._values.pop(crystal_id, None)

    def complete(self, crystal_id: str, bot_name: str) -> None:
        self.release(crystal_id, bot_name)

    def discard_missing(self, crystal_ids: Sequence[str]) -> None:
        """Release reservations as soon as their observed crystal disappears."""
        present = set(crystal_ids)
        with self._lock:
            self._purge()
            self._values = {
                key: value for key, value in self._values.items() if key in present
            }

    def _purge(self) -> None:
        now = self.clock()
        self._values = {key: value for key, value in self._values.items() if value[1] > now}


def is_dragon_support_target(entity: Mapping[str, Any], protected_names: Sequence[str]) -> bool:
    entity_type = str(entity.get("type", "")).casefold()
    if entity_type not in {"minecraft:end_crystal", "minecraft:ender_dragon"}:
        return False
    name = str(entity.get("name", entity.get("display_name", ""))).casefold()
    return not name or name not in {item.casefold() for item in protected_names}


class DragonSupportOutcome(str, Enum):
    HOLD = "HOLD"
    RUNNING = "RUNNING"
    COMPLETE = "COMPLETE"


class DragonSupportCoordinator:
    """One coordinated target assignment, not six independent fight loops."""

    def __init__(
        self,
        callbacks: Mapping[str, Callable[[str, Any, DragonRole, Optional[Mapping[str, Any]]], bool]],
        protected_names: Sequence[str],
        reservation_pool: Optional[CrystalReservationPool] = None,
        completion_confirmations: int = 3,
        required_archers: int = 2,
        required_arrows_per_archer: int = 8,
        hold_on_callback_failure: bool = True,
    ) -> None:
        self.callbacks = dict(callbacks)
        self.protected_names = tuple(protected_names)
        self.reservations = reservation_pool or CrystalReservationPool()
        self.completion_confirmations = max(1, int(completion_confirmations))
        self.required_archers = max(0, int(required_archers))
        self.required_arrows_per_archer = max(0, int(required_arrows_per_archer))
        self.hold_on_callback_failure = bool(hold_on_callback_failure)
        self._dragon_seen = False
        self._completion_observations = 0

    @property
    def dragon_seen(self) -> bool:
        return self._dragon_seen

    @property
    def completion_observations(self) -> int:
        return self._completion_observations

    def restore_progress(self, dragon_seen: bool, completion_observations: int) -> None:
        """Restore only monotonic completion evidence from a checkpoint."""
        self._dragon_seen = bool(dragon_seen)
        self._completion_observations = max(0, int(completion_observations))

    @staticmethod
    def _qualified_archers(
        bot_clients: Mapping[str, Any],
        minimum_arrows: int = 8,
    ) -> tuple[str, ...]:
        qualified: list[str] = []
        for name in sorted(bot_clients):
            try:
                payload = _unwrap(bot_clients[name].transport.dispatch("get_inventory", {}))
                carried = []
                for key in ("inventory", "armor", "offhand"):
                    values = payload.get(key, ())
                    if isinstance(values, Sequence) and not isinstance(values, (str, bytes)):
                        carried.extend(item for item in values if isinstance(item, Mapping))
                has_bow = any(
                    str(item.get("id", item.get("item", ""))).casefold()
                    == "minecraft:bow"
                    and int(item.get("count", 0) or 0) > 0
                    for item in carried
                )
                arrows = sum(
                    int(item.get("count", 0) or 0)
                    for item in carried
                    if str(item.get("id", item.get("item", ""))).casefold()
                    == "minecraft:arrow"
                )
                if has_bow and arrows >= minimum_arrows:
                    qualified.append(name)
            except Exception:
                continue
        return tuple(qualified)

    def run_tick(
        self,
        bot_clients: Mapping[str, Any],
        observer_client: Any,
        leader: PlayerSnapshot,
        *,
        leader_fresh: bool,
    ) -> DragonSupportOutcome:
        if not leader_fresh or not leader.in_end:
            return DragonSupportOutcome.HOLD
        try:
            payload = _unwrap(observer_client.transport.dispatch("get_entities", {"radius": 128}))
        except Exception:
            return DragonSupportOutcome.HOLD
        entities = tuple(item for item in payload.get("entities", ()) if isinstance(item, Mapping))
        targets = tuple(item for item in entities if is_dragon_support_target(item, self.protected_names))
        crystals = sorted(
            (item for item in targets if item.get("type") == "minecraft:end_crystal"),
            key=lambda item: float(item.get("distance", 9999)),
        )
        dragon = next((item for item in targets if item.get("type") == "minecraft:ender_dragon"), None)
        if dragon is not None:
            self._dragon_seen = True
        if dragon is None and not crystals:
            try:
                portal = _unwrap(
                    observer_client.transport.dispatch(
                        "find_blocks", {"blocks": ["minecraft:end_portal"], "radius": 64, "limit": 1}
                    )
                )
            except Exception:
                return DragonSupportOutcome.HOLD
            if self._dragon_seen and portal.get("found"):
                self._completion_observations += 1
                if self._completion_observations >= self.completion_confirmations:
                    return DragonSupportOutcome.COMPLETE
                return DragonSupportOutcome.RUNNING
            self._completion_observations = 0
            # The End spawn platform is commonly more than the bridge scan
            # radius from the flying dragon.  No current target is a waiting
            # condition, not a reason to globally stop six healthy fighters.
            return DragonSupportOutcome.RUNNING
        self._completion_observations = 0
        crystal_ids = tuple(
            str(item.get("id", item.get("uuid", "")))
            for item in crystals
            if item.get("id", item.get("uuid")) is not None
        )
        self.reservations.discard_missing(crystal_ids)
        qualified_archers = self._qualified_archers(
            bot_clients,
            self.required_arrows_per_archer,
        )
        if len(qualified_archers) < self.required_archers:
            return DragonSupportOutcome.HOLD
        assignments = assign_dragon_roles(tuple(bot_clients), qualified_archers)

        def run_one(name: str) -> bool:
            role = assignments[name]
            target = dragon
            if role is DragonRole.CRYSTAL_ARCHER:
                target = None
                for crystal in crystals:
                    candidate = str(crystal.get("id", crystal.get("uuid", "")))
                    if candidate and self.reservations.reserve(candidate, name):
                        target = crystal
                        break
            callback = self.callbacks.get(name)
            try:
                return callback is not None and callback(name, bot_clients[name], role, target)
            except Exception:
                return False

        with ThreadPoolExecutor(max_workers=len(assignments)) as pool:
            results = tuple(pool.map(run_one, sorted(assignments)))
        if all(results) or not self.hold_on_callback_failure:
            return DragonSupportOutcome.RUNNING
        return DragonSupportOutcome.HOLD


__all__ = [
    "CrystalReservationPool",
    "DragonRole",
    "DragonSupportCoordinator",
    "DragonSupportOutcome",
    "PortalInspection",
    "PortalSequence",
    "assign_dragon_roles",
    "inspect_portal",
    "is_dragon_support_target",
]
