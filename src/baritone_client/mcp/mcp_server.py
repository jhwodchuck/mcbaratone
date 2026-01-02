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
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable, Dict, List, Optional, TypeVar

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.session import ServerSession

from ..utils.cache_manager import CacheStats
from ..core.client import Client
from ..transport.command_dispatcher import CommandResult
from ..core.exceptions import TransportError
from ..core.facades.goals import GoalFactory
from ..transport.transport import TcpTransport
from ..utils.upload_manager import UploadProgress

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


def _call_bridge(ctx: RequestContext, label: str, fn: Callable[[Client], T], max_retries: int = 3) -> T:
    session = _session_from_context(ctx)
    last_exc = None
    for attempt in range(max_retries):
        try:
            return session.run(fn)
        except Exception as exc:
            last_exc = exc
            if "rate limit" in str(exc).lower():
                # Wait before retrying for rate limit
                wait_time = (attempt + 1) * 2  # Exponential backoff
                logger.warning(f"Rate limited on attempt {attempt + 1}, waiting {wait_time}s before retry")
                time.sleep(wait_time)
                continue
            elif attempt < max_retries - 1:
                # Retry for other errors
                logger.warning(f"Bridge operation '{label}' failed on attempt {attempt + 1}, retrying: {exc}")
                time.sleep(0.5)
                continue
            else:
                break
    raise RuntimeError(f"Bridge operation '{label}' failed after {max_retries} attempts: {last_exc}") from last_exc


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
        def _get_state(client: Client) -> Dict[str, Any]:
            result = client.command_dispatcher.dispatch("get_state", {})
            if result.is_success():
                return result.get_data()
            else:
                raise RuntimeError(f"get_state failed: {result.get_error_message()}")
        data = _call_bridge(ctx, "get_state", _get_state)
        return _format_json(data)

    @mcp.resource("baritone://inventory")
    def inventory(ctx: RequestContext) -> str:
        """Return the latest inventory snapshot."""
        def _get_inventory(client: Client) -> Dict[str, Any]:
            result = client.command_dispatcher.dispatch("get_inventory", {})
            if result.is_success():
                return result.get_data()
            else:
                raise RuntimeError(f"get_inventory failed: {result.get_error_message()}")
        data = _call_bridge(ctx, "get_inventory", _get_inventory)
        return _format_json(data)

    @mcp.resource("baritone://mission/status")
    def mission_status(ctx: RequestContext) -> str:
        """Expose bridge-side mission telemetry and queued macros."""
        data = _call_bridge(ctx, "mission_status", lambda client: client.mission.status())
        return _format_json(data)

    @mcp.resource("baritone://cache/stats")
    def cache_stats(ctx: RequestContext) -> str:
        """Return cache statistics."""
        stats = _call_bridge(ctx, "cache_stats", lambda client: client.cache.get_stats())
        return _format_json(stats.to_dict())

    @mcp.resource("baritone://upload/status")
    def upload_status(ctx: RequestContext) -> str:
        """Return upload manager status."""
        status = _call_bridge(ctx, "upload_status", lambda client: {
            "active_uploads": [p.to_dict() for p in client.upload.get_active_uploads()],
            "statistics": client.upload.get_statistics()
        })
        return _format_json(status)

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
                result = client.command_dispatcher.dispatch("get_state", {})
                data["state"] = result.get_data() if result.is_success() else {"error": result.get_error_message()}
            if include_inventory:
                result = client.command_dispatcher.dispatch("get_inventory", {})
                data["inventory"] = result.get_data() if result.is_success() else {"error": result.get_error_message()}
            if include_mission:
                data["mission"] = client.mission.status()
            return data

        return _call_bridge(ctx, "snapshot", _collect)

    @mcp.tool()
    def get_cached_block(x: int, y: int, z: int, ctx: RequestContext) -> Dict[str, Any]:
        """Get cached block information at coordinates."""
        return _call_bridge(ctx, "cache/get_block", lambda client: client.cache.get_cached_block(x, y, z))

    @mcp.tool()
    def cache_block(x: int, y: int, z: int, block_state: str, ctx: RequestContext) -> Dict[str, Any]:
        """Cache block state information."""
        success = _call_bridge(ctx, "cache/put_block", lambda client: client.cache.cache_block(x, y, z, block_state))
        return {"success": success}

    @mcp.tool()
    def clear_cache(ctx: RequestContext) -> Dict[str, Any]:
        """Clear all cached data."""
        success = _call_bridge(ctx, "cache/clear", lambda client: client.cache.clear())
        return {"success": success}

    @mcp.tool()
    def start_schematic_upload(name: str, expected_size: int, ctx: RequestContext, priority: str = "normal") -> Dict[str, Any]:
        """Start uploading a schematic file."""
        from ..utils.upload_manager import UploadPriority
        upload_priority = UploadPriority(priority.lower())
        success = _call_bridge(ctx, "upload/start", lambda client: client.upload.start_upload(name, expected_size, upload_priority))
        return {"success": success}

    @mcp.tool()
    def get_upload_progress(name: str, ctx: RequestContext) -> Dict[str, Any]:
        """Get progress of an upload."""
        progress = _call_bridge(ctx, "upload/progress", lambda client: client.upload.get_upload_progress(name))
        if progress is None:
            return {"error": "Upload not found"}
        return progress.to_dict()

    @mcp.tool()
    def cancel_upload(name: str, ctx: RequestContext) -> Dict[str, Any]:
        """Cancel an upload."""
        success = _call_bridge(ctx, "upload/cancel", lambda client: client.upload.cancel_upload(name))
        return {"success": success}

    @mcp.tool()
    def poll_events(
        ctx: RequestContext,
        event_types: Optional[List[str]] = None,
        max_events: Optional[int] = None
    ) -> Dict[str, Any]:
        """Poll events from the EventManager buffer."""
        events = _call_bridge(ctx, "events/poll", lambda client: client.poll_events(
            event_types=set(event_types) if event_types else None,
            max_events=max_events
        ))
        return {"events": [event.to_dict() for event in events]}

    @mcp.tool()
    def get_rate_limit_info(ctx: RequestContext) -> Dict[str, Any]:
        """Get current rate limiting information."""
        info = _call_bridge(ctx, "rate_limit/info", lambda client: client.command_dispatcher.get_rate_limit_info())
        return info

    #
    # Movement & Goals
    #

    @mcp.tool()
    def thisway(blocks: int, ctx: RequestContext) -> Dict[str, Any]:
        """Go in the direction you are facing for a specified number of blocks."""
        return _call_bridge(ctx, "command/run", lambda client: client.command.run(f"thisway {blocks}"))

    @mcp.tool()
    def path(ctx: RequestContext) -> Dict[str, Any]:
        """Start pathing to the current goal."""
        return _call_bridge(ctx, "command/run", lambda client: client.command.run("path"))

    @mcp.tool()
    def goal_coordinates(x: Optional[int], y: Optional[int], z: Optional[int], ctx: RequestContext) -> Dict[str, Any]:
        """Set a goal to the specified coordinates. Provide at least one coordinate."""
        parts = []
        if x is not None: parts.append(str(x))
        if y is not None: parts.append(str(y))
        if z is not None: parts.append(str(z))
        if not parts:
            return {"error": "At least one coordinate must be specified"}
        
        command = f"goal {' '.join(parts)}"
        return _call_bridge(ctx, "command/run", lambda client: client.command.run(command))

    @mcp.tool()
    def goto_block_type(block_type: str, ctx: RequestContext) -> Dict[str, Any]:
        """Go to a block of a specific type (e.g. 'diamond_ore', 'portal', 'ender_chest')."""
        return _call_bridge(ctx, "command/run", lambda client: client.command.run(f"goto {block_type}"))

    @mcp.tool()
    def follow_entity(entity_type: str, ctx: RequestContext) -> Dict[str, Any]:
        """Follow entities of a specific type (e.g. 'pig', 'cow')."""
        return _call_bridge(ctx, "command/run", lambda client: client.command.run(f"follow entity {entity_type}"))

    @mcp.tool()
    def follow_player(player_name: str, ctx: RequestContext) -> Dict[str, Any]:
        """Follow a specific player."""
        return _call_bridge(ctx, "command/run", lambda client: client.command.run(f"follow player {player_name}"))

    @mcp.tool()
    def come(ctx: RequestContext) -> Dict[str, Any]:
        """Tell Baritone to head towards your camera."""
        return _call_bridge(ctx, "command/run", lambda client: client.command.run("come"))

    @mcp.tool()
    def invert(ctx: RequestContext) -> Dict[str, Any]:
        """Invert the current goal and path (run away from goal)."""
        return _call_bridge(ctx, "command/run", lambda client: client.command.run("invert"))

    @mcp.tool()
    def blacklist(ctx: RequestContext) -> Dict[str, Any]:
        """Blacklist the closest block so Baritone won't attempt to get to it."""
        return _call_bridge(ctx, "command/run", lambda client: client.command.run("blacklist"))

    @mcp.tool()
    def explore(x: Optional[int], z: Optional[int], ctx: RequestContext) -> Dict[str, Any]:
        """Explore the world from the origin of x,z (or player feet if omitted)."""
        cmd = "explore"
        if x is not None and z is not None:
            cmd += f" {x} {z}"
        return _call_bridge(ctx, "command/run", lambda client: client.command.run(cmd))

    @mcp.tool()
    def axis(ctx: RequestContext) -> Dict[str, Any]:
        """Go to an axis or diagonal axis."""
        return _call_bridge(ctx, "command/run", lambda client: client.command.run("axis"))

    @mcp.tool()
    def surface(ctx: RequestContext) -> Dict[str, Any]:
        """Head towards the closest surface-like area."""
        return _call_bridge(ctx, "command/run", lambda client: client.command.run("surface"))

    #
    # Building & Mining
    #

    @mcp.tool()
    def build_schematic(name: str, x: Optional[int], y: Optional[int], z: Optional[int], ctx: RequestContext) -> Dict[str, Any]:
        """Build a schematic. Optionally specify origin coordinates."""
        cmd = f"build {name}"
        if x is not None and y is not None and z is not None:
             cmd += f" {x} {y} {z}"
        return _call_bridge(ctx, "command/run", lambda client: client.command.run(cmd))

    @mcp.tool()
    def tunnel(height: int, width: int, depth: int, ctx: RequestContext) -> Dict[str, Any]:
        """Dig a tunnel with specified dimensions."""
        return _call_bridge(ctx, "command/run", lambda client: client.command.run(f"tunnel {height} {width} {depth}"))

    @mcp.tool()
    def farm(range: Optional[int], waypoint: Optional[str], ctx: RequestContext) -> Dict[str, Any]:
        """Automatically harvest, replant, or bone meal crops."""
        cmd = "farm"
        if range is not None:
            cmd += f" {range}"
            if waypoint is not None:
                cmd += f" {waypoint}"
        return _call_bridge(ctx, "command/run", lambda client: client.command.run(cmd))

    @mcp.tool()
    def mine_quantity(block_type: str, quantity: int, ctx: RequestContext) -> Dict[str, Any]:
        """Mine a specific quantity of a block type."""
        return _call_bridge(ctx, "command/run", lambda client: client.command.run(f"mine {quantity} {block_type}"))

    #
    # Utility & Info
    #

    @mcp.tool()
    def eta(ctx: RequestContext) -> Dict[str, Any]:
        """Get information about the estimated time until the next segment and the goal."""
        return _call_bridge(ctx, "command/run", lambda client: client.command.run("eta"))

    @mcp.tool()
    def proc(ctx: RequestContext) -> Dict[str, Any]:
        """View miscellaneous information about the process currently controlling Baritone."""
        return _call_bridge(ctx, "command/run", lambda client: client.command.run("proc"))

    @mcp.tool()
    def find(block_type: str, ctx: RequestContext) -> Dict[str, Any]:
        """Search through Baritone's cache and attempt to find the location of the block."""
        return _call_bridge(ctx, "command/run", lambda client: client.command.run(f"find {block_type}"))

    @mcp.tool()
    def version(ctx: RequestContext) -> Dict[str, Any]:
        """Get the version of Baritone."""
        return _call_bridge(ctx, "command/run", lambda client: client.command.run("version"))

    @mcp.tool()
    def gc(ctx: RequestContext) -> Dict[str, Any]:
        """Call System.gc() to free up memory."""
        return _call_bridge(ctx, "command/run", lambda client: client.command.run("gc"))

    @mcp.tool()
    def repack(ctx: RequestContext) -> Dict[str, Any]:
        """Re-cache the chunks around you."""
        return _call_bridge(ctx, "command/run", lambda client: client.command.run("repack"))

    @mcp.tool()
    def render(ctx: RequestContext) -> Dict[str, Any]:
        """Fix glitched chunk rendering."""
        return _call_bridge(ctx, "command/run", lambda client: client.command.run("render"))

    @mcp.tool()
    def reload_all(ctx: RequestContext) -> Dict[str, Any]:
        """Reload Baritone's world cache."""
        return _call_bridge(ctx, "command/run", lambda client: client.command.run("reloadall"))

    @mcp.tool()
    def save_all(ctx: RequestContext) -> Dict[str, Any]:
        """Save Baritone's world cache."""
        return _call_bridge(ctx, "command/run", lambda client: client.command.run("saveall"))

    #
    # Waypoints
    #

    @mcp.tool()
    def wp_save(name: str, x: Optional[int], y: Optional[int], z: Optional[int], ctx: RequestContext) -> Dict[str, Any]:
        """Save a waypoint. Uses current position if coordinates are not provided."""
        cmd = f"wp save {name}"
        if x is not None and y is not None and z is not None:
             cmd += f" {x} {y} {z}"
        return _call_bridge(ctx, "command/run", lambda client: client.command.run(cmd))

    @mcp.tool()
    def wp_delete(name: str, ctx: RequestContext) -> Dict[str, Any]:
        """Delete a waypoint by name."""
        return _call_bridge(ctx, "command/run", lambda client: client.command.run(f"wp delete {name}"))

    @mcp.tool()
    def wp_list(tag: str, ctx: RequestContext) -> Dict[str, Any]:
        """List waypoints with a specific tag."""
        return _call_bridge(ctx, "command/run", lambda client: client.command.run(f"wp list {tag}"))

    @mcp.tool()
    def wp_goal(name: str, ctx: RequestContext) -> Dict[str, Any]:
        """Set a goal to a waypoint."""
        return _call_bridge(ctx, "command/run", lambda client: client.command.run(f"wp goal {name}"))

    #
    # Interaction
    #

    @mcp.tool()
    def click(ctx: RequestContext) -> Dict[str, Any]:
        """Click the destination on the screen (where player is looking)."""
        return _call_bridge(ctx, "command/run", lambda client: client.command.run("click"))

    #
    # Schematica Integration
    #

    @mcp.tool()
    def build_open_schematic(ctx: RequestContext) -> Dict[str, Any]:
        """Build the schematic that is currently open in Schematica."""
        return _call_bridge(ctx, "command/run", lambda client: client.command.run("schematica"))

    #
    # Settings Management
    #

    @mcp.tool()
    def set_setting(name: str, value: Any, ctx: RequestContext) -> Dict[str, Any]:
        """Set a Baritone setting. For booleans, the value is option (toggles if omitted in chat, but here we require value)."""
        # Note: In chat "allowBreak" toggles. "allowBreak true" sets it.
        # We will encourage explicit values.
        # If value is string "reset", it resets.
        return _call_bridge(ctx, "command/run", lambda client: client.command.run(f"{name} {value}"))

    @mcp.tool()
    def reset_setting(name: str, ctx: RequestContext) -> Dict[str, Any]:
        """Reset a specific setting to its default value."""
        return _call_bridge(ctx, "command/run", lambda client: client.command.run(f"{name} reset"))

    @mcp.tool()
    def reset_all_settings(ctx: RequestContext) -> Dict[str, Any]:
        """Reset all settings to their default values."""
        return _call_bridge(ctx, "command/run", lambda client: client.command.run("reset"))

    @mcp.tool()
    def get_modified_settings(ctx: RequestContext) -> Dict[str, Any]:
        """See all settings that have been modified from their default values."""
        # Note: This prints to chat. The bridge might capture chat, or we assume the user checks chat log.
        # Ideally we'd return them. For now, we invoke the command.
        return _call_bridge(ctx, "command/run", lambda client: client.command.run("modified"))

    #
    # Miscellaneous
    #

    @mcp.tool()
    def help_command(query: Optional[str], ctx: RequestContext) -> Dict[str, Any]:
        """Get help for Baritone commands."""
        cmd = "help"
        if query:
            cmd += f" {query}"
        return _call_bridge(ctx, "command/run", lambda client: client.command.run(cmd))

    @mcp.tool()
    def damn(ctx: RequestContext) -> Dict[str, Any]:
        """daniel"""
        return _call_bridge(ctx, "command/run", lambda client: client.command.run("damn"))

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
