from typing import Any, Dict, Optional, Tuple

from ...transport.enums import MovementStatus, PathCalculationResultType
from ...core.exceptions import CommandError
from ...models.models import Selection
from ...transport.serialization import serialize_selection
from ...transport.transport import Transport


class BaseProcess:
    """Base class for Baritone processes."""
    
    def __init__(self, transport: Transport, route: str) -> None:
        """
        Initialize a process.
        
        Args:
            transport: Transport instance
            route: Process route name (e.g., "mine", "farm", "builder")
        """
        self.transport = transport
        self.route = route

    def start(self, **params: Any) -> Dict[str, Any]:
        """
        Start the process with given parameters.
        
        Args:
            **params: Process-specific parameters
        
        Returns:
            Response dictionary
        
        Raises:
            CommandError: If process fails to start
        """
        response = self.transport.dispatch(f"process/{self.route}/start", params)
        self._raise_on_error(response)
        return response

    def stop(self) -> Dict[str, Any]:
        """
        Stop the process.
        
        Returns:
            Response dictionary
        
        Raises:
            CommandError: If process fails to stop
        """
        response = self.transport.dispatch(f"process/{self.route}/stop", {})
        self._raise_on_error(response)
        return response

    def status(self) -> Dict[str, Any]:
        """
        Get process status.
        
        Returns:
            Status dictionary
        
        Raises:
            CommandError: If status retrieval fails
        """
        response = self.transport.dispatch(f"process/{self.route}/status", {})
        self._raise_on_error(response)
        return response

    def _raise_on_error(self, response: Dict[str, Any]) -> None:
        """Raise CommandError if response contains an error."""
        if response.get("error"):
            raise CommandError(
                response["error"],
                command=f"process/{self.route}",
                error_code=response.get("error_code")
            )


class BuilderProcess(BaseProcess):
    """Process for building structures from schematics."""
    
    def __init__(self, transport: Transport) -> None:
        super().__init__(transport, "builder")

    def start(self, schematic_id: str, selection: Optional[Selection] = None, **params: Any) -> Dict[str, Any]:
        """
        Start building from a schematic.
        
        Args:
            schematic_id: Name/ID of the schematic to build
            selection: Optional selection area (if not provided, uses schematic position)
            **params: Additional parameters (x, y, z for build position)
        
        Returns:
            Response dictionary
        
        Raises:
            CommandError: If build fails to start
        """
        payload: Dict[str, Any] = {"schematicId": schematic_id, **params}
        if selection:
            payload["selection"] = serialize_selection(selection)
        return super().start(**payload)


class MineProcess(BaseProcess):
    """Process for mining blocks."""
    
    def __init__(self, transport: Transport) -> None:
        super().__init__(transport, "mine")

    def start(self, target_block: str, quantity: Optional[int] = None, **params: Any) -> Dict[str, Any]:
        """
        Start mining a specific block type.
        
        Args:
            target_block: Block type to mine (e.g., "diamond_ore", "iron_ore")
            quantity: Optional quantity to mine (0 = mine until inventory full)
            **params: Additional parameters
        
        Returns:
            Response dictionary
        
        Raises:
            CommandError: If mining fails to start
        """
        payload: Dict[str, Any] = {"target": target_block, **params}
        if quantity is not None:
            payload["quantity"] = int(quantity)
        return super().start(**payload)


class GetToBlockProcess(BaseProcess):
    def __init__(self, transport: Transport) -> None:
        super().__init__(transport, "get_to_block")

    def start(self, block: str, **params: Any) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"block": block, **params}
        return super().start(**payload)


class FarmProcess(BaseProcess):
    """Baritone crop harvesting and replanting process."""
    
    def __init__(self, transport: Transport) -> None:
        super().__init__(transport, "farm")

    def start(
        self,
        range: int = 0,
        center: Optional[Tuple[int, int, int]] = None,
        replant: Optional[bool] = None,
        **params: Any,
    ) -> Dict[str, Any]:
        """
        Start farming crops.
        
        Args:
            range: Search radius. Zero uses Baritone's default nearby search.
            center: Optional ``(x, y, z)`` search center.
            replant: Optionally set Baritone's crop-replant behavior.
            **params: Additional bridge-supported parameters.
        
        Returns:
            Response dictionary
        
        Raises:
            CommandError: If farming fails to start
        """
        if "crop" in params:
            raise ValueError("Baritone's farm process does not support crop filtering")
        payload: Dict[str, Any] = {"range": int(range), **params}
        if center is not None:
            if len(center) != 3:
                raise ValueError("center must contain x, y, and z")
            payload.update({"x": int(center[0]), "y": int(center[1]), "z": int(center[2])})
        if replant is not None:
            payload["replant"] = bool(replant)
        return super().start(**payload)


class ProcessFacade:
    """Facade for managing Baritone processes."""
    
    def __init__(self, transport: Transport) -> None:
        """
        Initialize process facade.
        
        Args:
            transport: Transport instance
        """
        self.builder = BuilderProcess(transport)
        self.mine = MineProcess(transport)
        self.get_to_block = GetToBlockProcess(transport)
        self.farm = FarmProcess(transport)
        self.transport = transport

    def pause(self) -> Dict[str, Any]:
        """
        Pause all active processes.
        
        Returns:
            Response dictionary
        """
        return self.transport.dispatch("process/pause", {})

    def resume(self) -> Dict[str, Any]:
        """
        Resume paused processes.
        
        Returns:
            Response dictionary
        """
        return self.transport.dispatch("process/resume", {})

    def movement_status(self, status: MovementStatus) -> Dict[str, Any]:
        """
        Update movement status (internal use).
        
        Args:
            status: Movement status enum
        
        Returns:
            Response dictionary
        """
        return self.transport.dispatch("process/status", {"status": status.value})

    def calculation_result(self, result: PathCalculationResultType) -> Dict[str, Any]:
        """
        Report path calculation result (internal use).
        
        Args:
            result: Path calculation result type
        
        Returns:
            Response dictionary
        """
        return self.transport.dispatch("process/path_result", {"result": result.value})
