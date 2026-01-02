from typing import Any, Dict, Optional

from ...transport.serialization import validate_setting
from ...transport.transport import Transport


class SettingsFacade:
    """Facade for managing Baritone settings."""

    def __init__(self, transport: Transport) -> None:
        self.transport = transport

    def set(self, name: str, value: Any) -> Dict[str, Any]:
        """
        Set a Baritone setting.

        Args:
            name: Setting name
            value: Setting value (must be str, bool, int, or float)

        Returns:
            Response dictionary

        Raises:
            ValidationError: If value type is invalid
            CommandError: If setting fails
        """
        validated_value = validate_setting(name, value)
        return self.transport.dispatch("settings/set", {"name": name, "value": validated_value})

    def get(self, name: str) -> Dict[str, Any]:
        """
        Get a Baritone setting value.

        Args:
            name: Setting name

        Returns:
            Response dictionary containing setting value

        Raises:
            CommandError: If setting retrieval fails
        """
        return self.transport.dispatch("settings/get", {"name": name})

    def reset(self, name: Optional[str] = None) -> Dict[str, Any]:
        """
        Reset Baritone setting(s) to default.

        Args:
            name: Setting name to reset, or None to reset all settings

        Returns:
            Response dictionary
        """
        payload: Dict[str, Any] = {}
        if name:
            payload["name"] = name
        return self.transport.dispatch("settings/reset", payload)
