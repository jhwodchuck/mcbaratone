from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from ..core.client import Client
from ..transport.enums import TransportEvent


class MissionPhase(Enum):
    """High-level checkpoints for defeating the Ender Dragon."""

    IDLE = "idle"
    BOOTSTRAP = "bootstrap"
    BASE_ESTABLISHED = "base_established"
    RESOURCE_GATHERING = "resource_gathering"
    NETHER_READY = "nether_ready"
    EYES_READY = "eyes_ready"
    STRONGHOLD_HUNT = "stronghold_hunt"
    FINAL_BATTLE = "final_battle"
    COMPLETE = "complete"

    @classmethod
    def from_value(cls, value: Optional[str]) -> "MissionPhase":
        if not value:
            return cls.IDLE
        for phase in cls:
            if phase.value == value or phase.name.lower() == value.lower():
                return phase
        return cls.IDLE


@dataclass
class MissionState:
    """Mutable snapshot of the mission status returned by the bridge."""

    phase: MissionPhase = MissionPhase.IDLE
    note: str = "not_started"
    checkpoints: List[str] = field(default_factory=list)
    queue: List[str] = field(default_factory=list)
    history: List[str] = field(default_factory=list)
    telemetry: Dict[str, Any] = field(default_factory=dict)
    last_chat: Optional[str] = None
    last_movement_status: Optional[str] = None
    safety: Dict[str, Any] = field(default_factory=dict)

    def apply_status_payload(self, payload: Dict[str, Any]) -> None:
        mission = payload.get("mission", payload)
        self.phase = MissionPhase.from_value(mission.get("phase"))
        self.note = mission.get("note", self.note)
        self.telemetry = mission.get("telemetry", self.telemetry)
        self.queue = list(mission.get("queue", self.queue))
        self.history = list(mission.get("history", self.history))

    def record_checkpoint(self, checkpoint: str) -> None:
        if checkpoint not in self.checkpoints:
            self.checkpoints.append(checkpoint)


class EndGameMission:
    """
    High-level mission scaffolding that orchestrates all steps required to reach
    the Ender Dragon. Each helper issues deterministic bridge commands so the
    coordinating bot (spand) can chain them together.
    """

    def __init__(self, client: Client, state: Optional[MissionState] = None) -> None:
        self.client = client
        self.state = state or MissionState()
        self._events_attached = False
        self._subscriptions: List[TransportEvent] = []

    def synchronize(self) -> MissionState:
        """Fetch the latest mission snapshot from the bridge."""
        payload = self.client.mission.status()
        self.state.apply_status_payload(payload)
        return self.state

    def attach_default_monitors(self) -> None:
        """Listen for chat and movement events to enrich local state."""
        if self._events_attached:
            return

        def _chat_callback(message: Dict[str, Any]) -> None:
            text = message.get("text") or message.get("message")
            if text:
                self.state.last_chat = text

        def _movement_callback(event: Dict[str, Any]) -> None:
            status = event.get("status") or event.get("state")
            if status:
                self.state.last_movement_status = status

        try:
            self.client.on(TransportEvent.CHAT, _chat_callback)
            self.client.on(TransportEvent.MOVEMENT_STATUS, _movement_callback)
            self._subscriptions.extend([TransportEvent.CHAT, TransportEvent.MOVEMENT_STATUS])
        finally:
            self._events_attached = True

    def _invoke_macro(self, name: str, params: Optional[Dict[str, Any]] = None, dequeue: bool = False) -> Dict[str, Any]:
        payload = self.client.mission.macro(name=name, params=params or {}, dequeue=dequeue)
        self.synchronize()
        return payload

    def bootstrap_world(self, settings: Optional[Dict[str, Any]] = None) -> MissionState:
        """Apply safe Baritone defaults and prime bridge telemetry."""
        self.attach_default_monitors()
        self._invoke_macro("bootstrap", {"settings": settings or {}})
        self.state.record_checkpoint("bootstrap")
        return self.state

    def establish_base(self, size: int = 13, height: int = 6) -> MissionState:
        """Clear an area around spawn so storage, furnaces, and portal fit."""
        self._invoke_macro("establish_base", {"size": size, "height": height})
        self.state.record_checkpoint("base_established")
        return self.state

    def gather_resources(self, targets: Optional[List[Dict[str, Any]]] = None) -> MissionState:
        """Kick off sequential mining jobs for ores, obsidian, and wood."""
        payload: Dict[str, Any] = {}
        if targets is not None:
            payload["targets"] = targets
        self._invoke_macro("resource_pipeline", payload)
        self.state.record_checkpoint("resources")
        return self.state

    def prepare_for_nether(self, obsidian: int = 12) -> MissionState:
        """Verify obsidian + flint availability prior to lighting a portal."""
        self._invoke_macro("enter_nether", {"obsidian": obsidian})
        self.state.record_checkpoint("nether_ready")
        return self.state

    def craft_eyes(self, required: int = 12) -> MissionState:
        """Craft Eyes of Ender based on the materials reported by the bridge."""
        self._invoke_macro("craft_eyes", {"required": required})
        self.state.record_checkpoint("eyes_ready")
        return self.state

    def locate_stronghold(self) -> MissionState:
        """Instruct Baritone to begin the stronghold triangulation routine."""
        self._invoke_macro("locate_stronghold", {})
        self.state.record_checkpoint("stronghold_hunt")
        return self.state

    def fight_dragon(self) -> MissionState:
        """Mark the mission as actively fighting the dragon."""
        self._invoke_macro("fight_dragon", {})
        self.state.record_checkpoint("final_battle")
        return self.state

    def run_full_mission(self) -> MissionState:
        """
        Execute all mission stages sequentially. This remains idempotent—the
        bridge tracks the active phase so re-entry simply resumes.
        """
        self.bootstrap_world()
        self.establish_base()
        self.gather_resources()
        self.prepare_for_nether()
        self.craft_eyes()
        self.locate_stronghold()
        self.fight_dragon()
        return self.state

    def schedule_actions(self, actions: List[str], clear_existing: bool = False) -> MissionState:
        """Queue macros server-side so detached clients can pick up later."""
        self.client.mission.queue(actions=actions, clear=clear_existing)
        return self.synchronize()


# Convenience exports for scripts that prefer function-level helpers
def bootstrap_world(client: Client, state: Optional[MissionState] = None) -> MissionState:
    return EndGameMission(client, state).bootstrap_world()


def establish_base(client: Client, state: Optional[MissionState] = None, size: int = 13, height: int = 6) -> MissionState:
    return EndGameMission(client, state).establish_base(size=size, height=height)


def gather_resources(
    client: Client, state: Optional[MissionState] = None, targets: Optional[List[Dict[str, Any]]] = None
) -> MissionState:
    return EndGameMission(client, state).gather_resources(targets=targets)


def prepare_for_nether(client: Client, state: Optional[MissionState] = None, obsidian: int = 12) -> MissionState:
    return EndGameMission(client, state).prepare_for_nether(obsidian=obsidian)


def locate_stronghold(client: Client, state: Optional[MissionState] = None) -> MissionState:
    return EndGameMission(client, state).locate_stronghold()


def fight_dragon(client: Client, state: Optional[MissionState] = None) -> MissionState:
    return EndGameMission(client, state).fight_dragon()
