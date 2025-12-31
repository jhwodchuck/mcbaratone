from typing import Any, Dict, Optional

from .enums import MovementStatus, PathCalculationResultType
from .exceptions import CommandError
from .models import Selection
from .serialization import serialize_selection
from .transport import Transport


class BaseProcess:
    def __init__(self, transport: Transport, route: str) -> None:
        self.transport = transport
        self.route = route

    def start(self, **params: Any) -> Dict[str, Any]:
        response = self.transport.dispatch(f"process/{self.route}/start", params)
        self._raise_on_error(response)
        return response

    def stop(self) -> Dict[str, Any]:
        response = self.transport.dispatch(f"process/{self.route}/stop", {})
        self._raise_on_error(response)
        return response

    def status(self) -> Dict[str, Any]:
        response = self.transport.dispatch(f"process/{self.route}/status", {})
        self._raise_on_error(response)
        return response

    def _raise_on_error(self, response: Dict[str, Any]) -> None:
        if response.get("error"):
            raise CommandError(response["error"])


class BuilderProcess(BaseProcess):
    def __init__(self, transport: Transport) -> None:
        super().__init__(transport, "builder")

    def start(self, schematic_id: str, selection: Optional[Selection] = None, **params: Any) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"schematicId": schematic_id, **params}
        if selection:
            payload["selection"] = serialize_selection(selection)
        return super().start(**payload)


class MineProcess(BaseProcess):
    def __init__(self, transport: Transport) -> None:
        super().__init__(transport, "mine")

    def start(self, target_block: str, quantity: Optional[int] = None, **params: Any) -> Dict[str, Any]:
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
    def __init__(self, transport: Transport) -> None:
        super().__init__(transport, "farm")

    def start(self, crop: str, replant: bool = True, **params: Any) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"crop": crop, "replant": bool(replant), **params}
        return super().start(**payload)


class ProcessFacade:
    def __init__(self, transport: Transport) -> None:
        self.builder = BuilderProcess(transport)
        self.mine = MineProcess(transport)
        self.get_to_block = GetToBlockProcess(transport)
        self.farm = FarmProcess(transport)
        self.transport = transport

    def pause(self) -> Dict[str, Any]:
        return self.transport.dispatch("process/pause", {})

    def resume(self) -> Dict[str, Any]:
        return self.transport.dispatch("process/resume", {})

    def movement_status(self, status: MovementStatus) -> Dict[str, Any]:
        return self.transport.dispatch("process/status", {"status": status.value})

    def calculation_result(self, result: PathCalculationResultType) -> Dict[str, Any]:
        return self.transport.dispatch("process/path_result", {"result": result.value})
