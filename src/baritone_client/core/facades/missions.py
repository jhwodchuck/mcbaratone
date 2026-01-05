from typing import Any, Dict, List, Optional

from ...transport.transport import Transport
from ...automator.resource_manager import ResourceManager
from ...events.event_manager import EventManager
from ...automator.mission_coordinator import MissionCoordinator
from ...models.models import MissionRequirements, MissionPriority, MissionDependency, MissionStatus


class MissionFacade:
    """Facade for bridge-side mission coordination helpers with concurrent mission support."""

    def __init__(
        self,
        transport: Transport,
        resource_manager: Optional[ResourceManager] = None,
        event_manager: Optional[EventManager] = None,
        enable_coordination: bool = True
    ) -> None:
        self.transport = transport

        # Mission coordination (Phase 2)
        self.coordinator: Optional[MissionCoordinator] = None
        if enable_coordination and resource_manager and event_manager:
            self.coordinator = MissionCoordinator(
                resource_manager=resource_manager,
                event_manager=event_manager
            )

        # Backward compatibility mode
        self._legacy_mode = self.coordinator is None

    def status(self) -> Dict[str, Any]:
        """Retrieve aggregated mission telemetry."""
        return self.transport.dispatch("mission/status", {})

    def checkpoint(self, phase: str, note: Optional[str] = None) -> Dict[str, Any]:
        """
        Update the bridge's mission checkpoint.

        Args:
            phase: Mission phase identifier
            note: Optional note or context
        """
        payload: Dict[str, Any] = {"phase": phase}
        if note:
            payload["note"] = note
        return self.transport.dispatch("mission/checkpoint", payload)

    def queue(self, actions: List[str], clear: bool = False) -> Dict[str, Any]:
        """Queue macros on the bridge for later execution."""
        payload: Dict[str, Any] = {"actions": actions}
        if clear:
            payload["clear"] = True
        return self.transport.dispatch("mission/queue", payload)

    def macro(
        self,
        name: Optional[str] = None,
        params: Optional[Dict[str, Any]] = None,
        dequeue: bool = False,
    ) -> Dict[str, Any]:
        """
        Trigger a macro on the bridge.

        Args:
            name: Macro identifier. If omitted and dequeue=True, the oldest queued macro runs.
            params: Optional parameters forwarded to the bridge.
            dequeue: Whether to consume the earliest queued macro before execution.
        """
        payload: Dict[str, Any] = {}
        if name:
            payload["name"] = name
        if params:
            payload["params"] = params
        if dequeue:
            payload["dequeue"] = True
        return self.transport.dispatch("mission/macro", payload)

    # Mission Coordination Framework (Phase 2)

    def create_coordinated_mission(
        self,
        name: str,
        requirements: MissionRequirements,
        priority: MissionPriority = MissionPriority.NORMAL,
        dependencies: Optional[List[MissionDependency]] = None,
        description: Optional[str] = None
    ) -> Optional[str]:
        """
        Create a new mission through the coordination framework.

        Args:
            name: Mission name
            requirements: Resource and dependency requirements
            priority: Mission priority
            dependencies: Optional mission dependencies
            description: Optional mission description

        Returns:
            Mission ID if coordination is enabled, None otherwise
        """
        if self._legacy_mode or not self.coordinator:
            return None

        return self.coordinator.create_mission(
            name=name,
            requirements=requirements,
            priority=priority,
            dependencies=dependencies,
            description=description
        )

    def cancel_coordinated_mission(self, mission_id: str) -> bool:
        """
        Cancel a coordinated mission.

        Args:
            mission_id: Mission to cancel

        Returns:
            True if mission was cancelled
        """
        if self._legacy_mode or not self.coordinator:
            return False

        return self.coordinator.cancel_mission(mission_id)

    def get_coordinated_mission_status(self, mission_id: str) -> Optional[Dict[str, Any]]:
        """
        Get status of a coordinated mission.

        Args:
            mission_id: Mission ID to query

        Returns:
            Mission status dict if found, None otherwise
        """
        if self._legacy_mode or not self.coordinator:
            return None

        mission = self.coordinator.get_mission_status(mission_id)
        return mission.model_dump() if mission else None

    def list_coordinated_missions(self, status_filter: Optional[MissionStatus] = None) -> List[Dict[str, Any]]:
        """
        List all coordinated missions.

        Args:
            status_filter: Optional status to filter by

        Returns:
            List of mission status dictionaries
        """
        if self._legacy_mode or not self.coordinator:
            return []

        missions = self.coordinator.list_missions(status_filter)
        return [mission.model_dump() for mission in missions]

    def check_resource_conflicts(self, mission_id: str) -> List[Dict[str, Any]]:
        """
        Check for resource conflicts for a specific mission.

        Args:
            mission_id: Mission ID to check conflicts for

        Returns:
            List of conflict descriptions
        """
        if self._legacy_mode or not self.coordinator:
            return []

        # Get the mission handler
        mission = None
        for handler in self.coordinator._active_missions.values():
            if handler.instance_model.id == mission_id:
                mission = handler
                break

        if not mission:
            for handler in self.coordinator._pending_missions:
                if handler.instance_model.id == mission_id:
                    mission = handler
                    break

        if not mission:
            return []

        conflicts = self.coordinator.check_resource_conflicts(mission)
        return [conflict.model_dump() for conflict in conflicts]

    def get_coordinator_state(self) -> Optional[Dict[str, Any]]:
        """
        Get current coordinator state snapshot.

        Returns:
            Coordinator state dict if coordination is enabled, None otherwise
        """
        if self._legacy_mode or not self.coordinator:
            return None

        state = self.coordinator.get_coordinator_state()
        return state.model_dump()

    def shutdown_coordinator(self) -> None:
        """Shutdown the mission coordinator."""
        if self.coordinator:
            self.coordinator.shutdown()
            self.coordinator = None
