"""TCP bridge protocol translation helpers."""

from typing import Any, Dict


READ_ONLY_ROUTES = frozenset(
    {
        "get_state",
        "process/status",
        "get_block",
        "get_view",
        "get_entities",
        "get_combat_snapshot",
        "get_screen",
        "get_dimension",
        "get_version",
        "get_events",
        "get_death_location",
        "find_blocks",
    }
)
BEST_EFFORT_ROUTES = frozenset({"close_screen"})


def is_traced_command(route: str) -> bool:
    """Return whether a route emits command lifecycle telemetry."""
    return route in {
        "goto",
        "explore",
        "mine",
        "cancel",
        "command/cancel",
        "place_block",
        "break_block",
        "set_fast_break",
        "craft",
        "place_recipe",
        "smelt",
        "open_container",
        "open_chest",
        "respawn",
        "attack",
    } or route.startswith("process/")


def translate_route(route: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Translate a public transport route to the bridge wire command."""
    mapped_route = route
    params: Dict[str, Any] = dict(payload)

    if route == "process/status":
        mapped_route = "get_state"
        params = {}
    elif route == "command":
        mapped_route = payload.get("command", "")
        params = payload.get("params", {})
    elif route == "command/run":
        mapped_route = "chat"
        params = {"message": payload.get("command", "")}
    elif route == "command/cancel":
        mapped_route = "cancel"
        params = {}
    elif route == "command/explore":
        mapped_route = "explore"
    elif route == "command/follow":
        mapped_route = "follow"
    elif route == "command/get_block":
        mapped_route = "get_block"
    elif route.startswith("mission/"):
        mapped_route = route.replace("/", "_")
    elif route.startswith("settings/"):
        mode = route.split("/", 1)[1]
        mapped_route = "settings"
        name = params.pop("name", None)
        if mode == "set" and name:
            params["set"] = name
        elif mode == "get" and name:
            params["get"] = name
        elif mode == "reset" and name:
            params["reset"] = name
    elif route.startswith("schematics.upload."):
        suffix = route.split(".")[-1]
        mapped_route = {
            "init": "schematic_init",
            "chunk": "schematic_chunk",
            "commit": "schematic_commit",
        }.get(suffix, "schematic_init")
    elif route in {"goal/apply", "goal/clear"}:
        mapped_route = "goal"
        if route == "goal/clear":
            params = {"clear": True}
    elif route.startswith("process/"):
        parts = route.split("/")
        if len(parts) == 3:
            _, process, action = parts
            if action == "start":
                mapped_route = process
            elif action == "stop":
                mapped_route = "cancel"
            elif action == "status":
                mapped_route = "get_state"
                params = {}

    return {"command": mapped_route, "params": params}
