from typing import Any, Dict, Optional

from .enums import TransportEvent
from .exceptions import ValidationError
from .goals import GoalManager
from .processes import ProcessFacade
from .serialization import validate_setting
from .transport import Transport


class CommandFacade:
    def __init__(self, transport: Transport) -> None:
        self.transport = transport

    def run(self, command: str) -> Dict[str, Any]:
        if not command or not command.strip():
            raise ValidationError("Command cannot be empty")
        return self.transport.dispatch("command/run", {"command": command})


class SettingsFacade:
    def __init__(self, transport: Transport) -> None:
        self.transport = transport

    def set(self, name: str, value: Any) -> Dict[str, Any]:
        validated_value = validate_setting(name, value)
        return self.transport.dispatch("settings/set", {"name": name, "value": validated_value})

    def get(self, name: str) -> Dict[str, Any]:
        return self.transport.dispatch("settings/get", {"name": name})

    def reset(self, name: Optional[str] = None) -> Dict[str, Any]:
        payload: Dict[str, Any] = {}
        if name:
            payload["name"] = name
        return self.transport.dispatch("settings/reset", payload)


class Client:
    def __init__(self, transport: Transport) -> None:
        self.transport = transport
        self.command = CommandFacade(transport)
        self.process = ProcessFacade(transport)
        self.goals = GoalManager(transport)
        self.settings = SettingsFacade(transport)

    def on(self, event: TransportEvent, callback) -> None:
        self.transport.subscribe(event, callback)

    def shutdown(self) -> None:
        self.transport.shutdown()
