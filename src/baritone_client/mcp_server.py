"""
Expose the Baritone bridge as a Model Context Protocol (MCP) server.

The MCP server lets LLM-centric tools retrieve bridge telemetry (state, inventory,
mission status) and call high-level Baritone actions such as running commands,
setting goals, queueing macros, or starting mining jobs.

Example usage (stdio transport for tools such as Claude Desktop):

    python -m baritone_client.mcp_server --host localhost --port 5555

Example usage (streamable HTTP transport for the MCP Inspector):

    python -m baritone_client.mcp_server --transport streamable-http --http-host 127.0.0.1

The exposed tools and resources are transport-agnostic since the server wraps the
existing :mod:`baritone_client` facade and only adds an MCP-friendly surface.
"""

from __future__ import annotations

import argparse
import json
import logging
import threading
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable, Dict, Optional, TypeVar

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.session import ServerSession

from .client import Client
from .exceptions import TransportError
from .goals import GoalFactory
from .transport import TcpTransport

logger = logging.getLogger(__name__)
T = TypeVar("T")


@dataclass
class BridgeConfig:
    """Runtime configuration for the MCP server."""

    host: str = "localhost"
    port: int = 5555
    timeout: float = 15.0


class BridgeSession:
    """
    Lazily connect to the Java bridge and serialize access to the shared client.

    The MCP server can receive concurrent requests when exposed over HTTP. The
    session protects the underlying :class:`Client` with a re-entrant lock and
    recreates it automatically when the socket closes or raises a
    :class:`TransportError`.
    """

    def __init__(
        self,
        config: BridgeConfig,
        client_factory: Optional[Callable[[], Client]] = None,
    ) -> None:
        self.config = config
        self._client_factory = client_factory or self._default_factory
        self._client: Optional[Client] = None
        self._lock = threading.RLock()

    def _default_factory(self) -> Client:
        logger.info(
            "Connecting to Baritone bridge at %s:%s (timeout %.1fs)",
            self.config.host,
            self.config.port,
            self.config.timeout,
        )
        transport = TcpTransport(host=self.config.host, port=self.config.port, timeout=self.config.timeout)
        return Client(transport)

    def _ensure_client(self) -> Client:
        if self._client is None:
            self._client = self._client_factory()
        return self._client

    def _reset_client_locked(self) -> None:
        if self._client is None:
            return
        try:
            self._client.shutdown()
        except Exception:
            logger.exception("Failed to shut down the Baritone client cleanly")
        finally:
            self._client = None

    def run(self, action: Callable[[Client], T]) -> T:
        """Execute an action while holding the session lock."""
        with self._lock:
            client = self._ensure_client()
            try:
                return action(client)
            except (OSError, TransportError):
                # Reset so the next request reconnects automatically.
                self._reset_client_locked()
                raise

    def close(self) -> None:
        """Release resources explicitly (invoked when the MCP server stops)."""
        with self._lock:
            self._reset_client_locked()


@dataclass
class BridgeLifespanContext:
    """Typed context exposed to MCP handlers."""

    session: BridgeSession


RequestContext = Context[ServerSession, BridgeLifespanContext]


def _format_json(data: Any) -> str:
    return json.dumps(data, indent=2, sort_keys=True)


def _session_from_context(ctx: RequestContext) -> BridgeSession:
    lifespan_ctx = ctx.request_context.lifespan_context
    if lifespan_ctx is None:
        raise RuntimeError("Bridge session is not initialized")
    return lifespan_ctx.session


def _call_bridge(ctx: RequestContext, label: str, fn: Callable[[Client], T]) -> T:
    session = _session_from_context(ctx)
    try:
        return session.run(fn)
    except Exception as exc:
        raise RuntimeError(f"Bridge operation '{label}' failed: {exc}") from exc


def create_mcp_server(config: BridgeConfig) -> FastMCP:
    """Create a FastMCP server wired to the configured bridge."""
    session = BridgeSession(config)

    @asynccontextmanager
    async def lifespan(_server: FastMCP) -> AsyncIterator[BridgeLifespanContext]:
        try:
            yield BridgeLifespanContext(session=session)
        finally:
            session.close()

    instructions = (
        "Expose key Baritone bridge controls (state, inventory, missions, goals, processes) "
        "as MCP tools and resources. Use these endpoints to orchestrate End-to-End Minecraft runs."
    )
    mcp = FastMCP(
        name="mcbaratone-bridge",
        instructions=instructions,
        lifespan=lifespan,
        json_response=True,
    )

    #
    # Resources
    #

    @mcp.resource("baritone://state")
    def runtime_state(ctx: RequestContext) -> str:
        """Return aggregated Baritone status (position, health, active goals)."""
        data = _call_bridge(ctx, "get_state", lambda client: client.transport.dispatch("get_state", {}))
        return _format_json(data)

    @mcp.resource("baritone://inventory")
    def inventory(ctx: RequestContext) -> str:
        """Return the latest inventory snapshot."""
        data = _call_bridge(ctx, "get_inventory", lambda client: client.transport.dispatch("get_inventory", {}))
        return _format_json(data)

    @mcp.resource("baritone://mission/status")
    def mission_status(ctx: RequestContext) -> str:
        """Expose bridge-side mission telemetry and queued macros."""
        data = _call_bridge(ctx, "mission_status", lambda client: client.mission.status())
        return _format_json(data)

    #
    # Tools
    #

    @mcp.tool()
    def run_command(command: str, ctx: RequestContext) -> Dict[str, Any]:
        """Execute a raw Baritone/mission command string."""
        return _call_bridge(ctx, "command/run", lambda client: client.command.run(command))

    @mcp.tool()
    def cancel_command(ctx: RequestContext) -> Dict[str, Any]:
        """Cancel the active Baritone task."""
        return _call_bridge(ctx, "command/cancel", lambda client: client.command.cancel())

    @mcp.tool()
    def set_goal_block(x: int, y: int, z: int, ctx: RequestContext) -> Dict[str, Any]:
        """Apply a block-level navigation goal using goto command (bridge doesn't support GoalBlock)."""
        command = f"goto {x} {y} {z}"
        return _call_bridge(ctx, "command/run", lambda client: client.command.run(command))

    @mcp.tool()
    def clear_goal(ctx: RequestContext) -> Dict[str, Any]:
        """Clear the current pathing goal."""
        return _call_bridge(ctx, "goal/clear", lambda client: client.goals.clear())

    @mcp.tool()
    def mission_macro(
        name: Optional[str],
        ctx: RequestContext,
        dequeue: bool = False,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Invoke a mission macro or consume the queued macros (dequeue=True)."""
        return _call_bridge(
            ctx,
            "mission/macro",
            lambda client: client.mission.macro(name=name, params=params, dequeue=dequeue),
        )

    @mcp.tool()
    def mission_queue(actions: list[str], ctx: RequestContext, clear: bool = False) -> Dict[str, Any]:
        """Queue mission macros that the bridge should execute sequentially."""
        return _call_bridge(ctx, "mission/queue", lambda client: client.mission.queue(actions, clear=clear))

    @mcp.tool()
    def mission_checkpoint(phase: str, ctx: RequestContext, note: Optional[str] = None) -> Dict[str, Any]:
        """Persist the active phase/checkpoint on the bridge."""
        return _call_bridge(ctx, "mission/checkpoint", lambda client: client.mission.checkpoint(phase, note=note))

    @mcp.tool()
    def start_mining(target_block: str, ctx: RequestContext, quantity: Optional[int] = None) -> Dict[str, Any]:
        """Start the mining process for a given target block."""
        return _call_bridge(
            ctx,
            "process/mine/start",
            lambda client: client.process.mine.start(target_block=target_block, quantity=quantity),
        )

    @mcp.tool()
    def stop_mining(ctx: RequestContext) -> Dict[str, Any]:
        """Stop the mining process."""
        return _call_bridge(ctx, "process/mine/stop", lambda client: client.process.mine.stop())

    @mcp.tool()
    def snapshot(
        ctx: RequestContext,
        include_state: bool = True,
        include_inventory: bool = True,
        include_mission: bool = True,
    ) -> Dict[str, Any]:
        """Return a combined snapshot of bridge telemetry."""

        def _collect(client: Client) -> Dict[str, Any]:
            data: Dict[str, Any] = {}
            if include_state:
                data["state"] = client.transport.dispatch("get_state", {})
            if include_inventory:
                data["inventory"] = client.transport.dispatch("get_inventory", {})
            if include_mission:
                data["mission"] = client.mission.status()
            return data

        return _call_bridge(ctx, "snapshot", _collect)

    return mcp


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Expose the Baritone bridge as an MCP server.")
    parser.add_argument("--host", default="localhost", help="Baritone bridge host")
    parser.add_argument("--port", type=int, default=5555, help="Baritone bridge port")
    parser.add_argument(
        "--timeout",
        type=float,
        default=15.0,
        help="Per-request TCP timeout when talking to the bridge (seconds)",
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http"],
        default="stdio",
        help="MCP transport to expose",
    )
    parser.add_argument("--http-host", default="127.0.0.1", help="Bind address for streamable HTTP transport")
    parser.add_argument("--http-port", type=int, default=8000, help="TCP port for streamable HTTP transport")
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="Logging level (DEBUG, INFO, WARNING, ERROR)",
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    config = BridgeConfig(host=args.host, port=args.port, timeout=args.timeout)
    server = create_mcp_server(config)

    logger.info(
        "Starting MCP server '%s' via %s transport",
        server.name,
        args.transport,
    )

    if args.transport == "stdio":
        server.run(transport="stdio")
    else:
        server.settings.host = args.http_host
        server.settings.port = args.http_port
        server.run(transport="streamable-http")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
