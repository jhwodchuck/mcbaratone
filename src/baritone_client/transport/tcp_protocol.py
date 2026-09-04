"""TCP bridge protocol translation helpers."""

from typing import Any, Dict

from ..core.exceptions import CommandError


READ_ONLY_ROUTES = frozenset(
    {
        "get_state",
        "get_inventory",
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
        "interact_block", "use_bucket", "use_item", "smelt_items",
        "inventory_click", "dig_block", "place_fire", "throw_item", "select_slot",
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


def validate_response_sequence(response: Dict[str, Any], request_seq: int) -> None:
    """Reject a response that explicitly echoes a different request sequence.

    Older bridge builds omitted the echo, so absence remains compatible; an
    explicit mismatch is unsafe because it can attach evidence to the wrong
    request even when the socket-level request id was reused.
    """
    if not isinstance(response, dict):
        raise CommandError("Malformed bridge response", response={})
    echoed = response.get("request_seq")
    for key in ("data", "result"):
        nested = response.get(key)
        if echoed is None and isinstance(nested, dict):
            echoed = nested.get("request_seq")
    if echoed is None:
        return
    try:
        matches = (
            not isinstance(echoed, bool)
            and str(echoed) == str(request_seq)
        )
    except (TypeError, ValueError):
        matches = False
    if not matches:
        raise CommandError(
            f"Bridge response sequence mismatch (expected {request_seq}, got {echoed})",
            response=response,
        )


def observe_response(route: str, payload: Dict[str, Any], response: Dict[str, Any]) -> None:
    """Run shared response observers for TCP and WebSocket transports."""
    from ..observability import (
        observe_command_response,
        observe_entities_response,
        observe_inventory_response,
        observe_state_response,
    )
    if route == "get_inventory":
        observe_inventory_response(response)
    elif route in {"get_state", "process/status"}:
        observe_state_response(response)
    elif route == "get_entities":
        observe_entities_response(response)
    observe_command_response(route, payload, response) if is_traced_command(route) else None


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
