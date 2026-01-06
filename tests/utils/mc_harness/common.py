"""Common safe dispatch helpers for the Minecraft test harness."""

from typing import Any, Dict, Optional


def safe_dispatch(ctx, route: str, payload: Optional[Dict[str, Any]] = None, **kwargs) -> Dict[str, Any]:
    """Dispatch a transport call with minimal error handling."""
    try:
        return ctx.client.transport.dispatch(route, payload or {}, **kwargs)
    except Exception as exc:
        if hasattr(ctx, "log_event"):
            ctx.log_event(f"Dispatch failed for {route}: {exc}")
        return {"error": str(exc), "route": route}


def safe_run_command(ctx, command: str) -> None:
    """Run a server command with minimal error handling."""
    try:
        ctx.run_command(command)
    except Exception as exc:
        if hasattr(ctx, "log_event"):
            ctx.log_event(f"Command failed '{command}': {exc}")
