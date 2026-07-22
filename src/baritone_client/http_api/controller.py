from __future__ import annotations

import time
from typing import Any, Dict, Iterable, List, Optional

from .client import BaritoneClient, BaritoneError
from .spec import Endpoint, load_endpoints


class BaritoneController:
    """
    High level helper built on top of :class:`BaritoneClient`.
    """

    def __init__(self, client: BaritoneClient, endpoints: Optional[List[Endpoint]] = None) -> None:
        self.client = client
        self.endpoints = endpoints or load_endpoints()

    # Convenience wrappers -------------------------------------------------
    def go_to(
        self,
        x: float,
        y: float,
        z: float,
        *,
        dimension: str = "overworld",
        allow_break: bool = True,
        allow_place: bool = True,
    ) -> Dict[str, Any]:
        self._validate_coordinate(x, "x")
        self._validate_coordinate(y, "y")
        self._validate_coordinate(z, "z")
        if dimension not in {"overworld", "nether", "end", "custom"}:
            raise BaritoneError(f"Unsupported dimension '{dimension}'")
        return self.client.goto(x, y, z, dimension=dimension, allow_break=allow_break, allow_place=allow_place)

    def pause(self, reason: Optional[str] = None) -> Dict[str, Any]:
        return self.client.pause(reason=reason)

    def resume(self, task_id: Optional[str] = None) -> Dict[str, Any]:
        return self.client.resume(task_id=task_id)

    def stop(self, reason: Optional[str] = None) -> Dict[str, Any]:
        return self.client.stop(reason=reason)

    def set_setting(self, name: str, value: Any) -> Dict[str, Any]:
        self._validate_setting_name(name)
        return self.client.set_setting(name, value)

    def get_setting(self, name: str) -> Dict[str, Any]:
        self._validate_setting_name(name)
        return self.client.get_setting(name)

    def mine(self, blocks: Iterable[str], target_count: Optional[int] = None) -> Dict[str, Any]:
        block_list = list(blocks)
        if not block_list:
            raise BaritoneError("At least one block id must be provided to mine")
        return self.client.mine(block_list, target_count=target_count)

    def farm(self, crops: Iterable[str], radius: float = 8.0) -> Dict[str, Any]:
        crop_list = list(crops)
        if not crop_list:
            raise BaritoneError("At least one crop id must be provided to farm")
        if radius <= 0:
            raise BaritoneError("Radius must be positive")
        return self.client.farm(crop_list, radius=radius)

    def build(self, schematic: str, origin: Dict[str, float]) -> Dict[str, Any]:
        if not schematic:
            raise BaritoneError("Schematic identifier cannot be empty")
        self._validate_origin(origin)
        return self.client.build(schematic, origin)

    def poll_status(self, interval_seconds: float = 1.0, max_polls: int = 5) -> List[Dict[str, Any]]:
        """
        Poll the status endpoint repeatedly; useful for CLI demos.
        """
        results: List[Dict[str, Any]] = []
        for _ in range(max_polls):
            results.append(self.client.status())
            time.sleep(interval_seconds)
        return results

    # Validation helpers ---------------------------------------------------
    @staticmethod
    def _validate_coordinate(value: float, axis: str) -> None:
        if not isinstance(value, (int, float)):
            raise BaritoneError(f"Coordinate {axis} must be a number")

    @staticmethod
    def _validate_origin(origin: Dict[str, float]) -> None:
        for key in ("x", "y", "z"):
            if key not in origin:
                raise BaritoneError(f"Origin requires '{key}' key")
            if not isinstance(origin[key], (int, float)):
                raise BaritoneError(f"Origin {key} must be numeric")

    def _validate_setting_name(self, name: str) -> None:
        if not name or not isinstance(name, str):
            raise BaritoneError("Setting name must be a non-empty string")
        available = {ep.path.split("/{")[0] for ep in self.endpoints if ep.category == "settings"}
        if not available:
            return
        # Nothing fancy, but makes sure we at least know settings endpoints exist
        if "/api/settings" not in available:
            raise BaritoneError("Settings endpoints are not available in the loaded overview tree")
