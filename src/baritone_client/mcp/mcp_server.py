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
from ..transport.transport import TcpTransport, WebSocketTransport
from ..utils.upload_manager import UploadProgress
from ..build_plans import compile_plan_preview
from ..mcp.guidance import (
    BUILD_PLAN_GUIDE_ALIAS,
    BUILD_PLAN_RESOURCE_URI,
    BUILDSITE_RESOURCE_URI,
    WORKFLOW_GUIDE_ALIAS,
    WORKFLOW_RESOURCE_URI,
    build_build_plan_prompt,
    build_buildsite_prompt,
    build_workflow_prompt,
    get_agent_guide,
    get_tool_description,
)

logger = logging.getLogger(__name__)
T = TypeVar("T")


def _normalize_guidance_topic(topic: Optional[str]) -> str:
    """Normalize guidance topic values to a simple hyphenated key."""
    if topic is None:
        return WORKFLOW_GUIDE_ALIAS

    normalized = str(topic).strip().lower().replace("_", "-").replace(" ", "-")
    while "--" in normalized:
        normalized = normalized.replace("--", "-")
    return normalized.strip("-")


@dataclass
class BridgeConfig:
    """Runtime configuration for the MCP server."""

    host: str = "localhost"
    port: int = 5555
    timeout: float = 15.0
    transport_type: str = "websocket"  # "tcp" or "websocket"


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
        if self.config.transport_type == "websocket":
            url = f"ws://{self.config.host}:{self.config.port}"
            logger.info(
                "Connecting to Baritone bridge via WebSocket at %s (timeout %.1fs)",
                url,
                self.config.timeout,
            )
            transport = WebSocketTransport(url=url, timeout=self.config.timeout, enable_event_storage=False)
        else:
            logger.info(
                "Connecting to Baritone bridge via TCP at %s:%s (timeout %.1fs)",
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

    @mcp.resource("baritone://analytics/health")
    def system_health(ctx: RequestContext) -> str:
        """Return comprehensive system health metrics."""
        health = _call_bridge(ctx, "system/health", lambda client: client.get_bridge_health())
        return _format_json(health.model_dump() if hasattr(health, 'model_dump') else health.__dict__)

    @mcp.resource("baritone://analytics/performance")
    def performance_metrics(ctx: RequestContext) -> str:
        """Return performance metrics and system throughput."""
        metrics = _call_bridge(ctx, "system/metrics", lambda client: client.get_bridge_metrics())
        return _format_json(metrics)

    @mcp.resource("baritone://analytics/report")
    def analytics_report(ctx: RequestContext) -> str:
        """Return comprehensive analytics report."""
        report = _call_bridge(ctx, "analytics/report", lambda client: client.get_performance_report())
        return _format_json(report.model_dump() if hasattr(report, 'model_dump') else report.__dict__)

    @mcp.resource(WORKFLOW_RESOURCE_URI)
    def workflow_guidance_resource() -> str:
        """Return survival-first guidance for agent workflow."""
        return _format_json({
            "topic": "workflow",
            "guidance": get_agent_guide("workflow"),
        })

    @mcp.resource(BUILD_PLAN_RESOURCE_URI)
    def build_plan_guidance_resource() -> str:
        """Return survival-first guidance for build plan execution."""
        return _format_json({
            "topic": "build-plan",
            "guidance": get_agent_guide("build-plan"),
        })

    @mcp.resource(BUILDSITE_RESOURCE_URI)
    def buildsite_guidance_resource() -> str:
        """Return survival-first guidance for build site inspections."""
        return _format_json({
            "topic": "buildsite",
            "guidance": get_agent_guide("buildsite"),
        })

    if hasattr(FastMCP, "prompt"):

        @mcp.prompt(name=WORKFLOW_GUIDE_ALIAS)
        def workflow_guidance_prompt(task: Optional[str] = None) -> str:
            """Return workflow prompt for agent guidance."""
            return build_workflow_prompt(task)

        @mcp.prompt(name=BUILD_PLAN_GUIDE_ALIAS)
        def build_plan_guidance_prompt(task: Optional[str] = None) -> str:
            """Return prompt for build-plan guidance."""
            return build_build_plan_prompt(task)

    #
    # Tools
    #

    @mcp.tool()
    def run_command(command: str, ctx: RequestContext) -> Dict[str, Any]:
        """Execute a raw Baritone/mission command string."""
        return _call_bridge(ctx, "command/run", lambda client: client.command.run(command))

    @mcp.tool()
    def batch_command(commands: List[str], ctx: RequestContext, stop_on_error: bool = True) -> Dict[str, Any]:
        """Execute multiple Baritone commands sequentially with proper error handling."""
        results = []
        for i, command in enumerate(commands):
            try:
                result = _call_bridge(ctx, f"batch/command/{i}", lambda client: client.command.run(command))
                results.append({"command": command, "result": result, "success": True})
            except Exception as e:
                error_result = {"command": command, "error": str(e), "success": False}
                results.append(error_result)
                if stop_on_error:
                    break
        return {"batch_results": results, "total_commands": len(commands), "executed_commands": len(results)}

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
    def subscribe_events(
        ctx: RequestContext,
        event_types: List[str],
        priority_threshold: Optional[int] = None
    ) -> Dict[str, Any]:
        """Subscribe to real-time event streaming for specified event types."""
        from ..transport.enums import TransportEvent
        try:
            event_enums = {TransportEvent(et) for et in event_types}
            success = _call_bridge(ctx, "events/subscribe", lambda client: client.transport.subscribe_events(
                event_types=event_enums,
                priority_threshold=priority_threshold or 0
            ))
            return {"success": True, "subscribed_events": event_types}
        except ValueError as e:
            return {"success": False, "error": f"Invalid event type: {e}"}

    @mcp.tool()
    def unsubscribe_events(
        ctx: RequestContext,
        event_types: List[str]
    ) -> Dict[str, Any]:
        """Unsubscribe from real-time event streaming for specified event types."""
        from ..transport.enums import TransportEvent
        try:
            event_enums = {TransportEvent(et) for et in event_types}
            success = _call_bridge(ctx, "events/unsubscribe", lambda client: client.transport.unsubscribe_events(event_types=event_enums))
            return {"success": True, "unsubscribed_events": event_types}
        except ValueError as e:
            return {"success": False, "error": f"Invalid event type: {e}"}

    @mcp.tool()
    def subscribe_mission_updates(ctx: RequestContext, priority_threshold: Optional[int] = None) -> Dict[str, Any]:
        """Subscribe to real-time mission state updates and collaborative mission events."""
        return _call_bridge(ctx, "mission/subscribe", lambda client: client.transport.subscribe_events(
            event_types={"mission"}, priority_threshold=priority_threshold or 0
        ))

    @mcp.tool()
    def unsubscribe_mission_updates(ctx: RequestContext) -> Dict[str, Any]:
        """Unsubscribe from real-time mission state updates."""
        return _call_bridge(ctx, "mission/unsubscribe", lambda client: client.transport.unsubscribe_events(event_types={"mission"}))

    @mcp.tool()
    def broadcast_mission_state(ctx: RequestContext, mission_data: Dict[str, Any], priority: int = 0) -> Dict[str, Any]:
        """Broadcast mission state update to all subscribed MCP clients."""
        return _call_bridge(ctx, "mission/broadcast", lambda client: client.transport.emit(
            "mission", mission_data, priority=priority
        ))

    @mcp.tool()
    def get_rate_limit_info(ctx: RequestContext) -> Dict[str, Any]:
        """Get current rate limiting information."""
        info = _call_bridge(ctx, "rate_limit/info", lambda client: client.command_dispatcher.get_rate_limit_info())
        return info

    @mcp.tool()
    def get_command_analytics(command_name: str, ctx: RequestContext) -> Dict[str, Any]:
        """Get performance analytics for a specific command."""
        analytics = _call_bridge(ctx, "analytics/command", lambda client: client.get_command_analytics(command_name))
        if analytics is None:
            return {"error": f"No analytics available for command: {command_name}"}
        return analytics.model_dump() if hasattr(analytics, 'model_dump') else analytics.__dict__

    @mcp.tool()
    def get_system_health_alerts(ctx: RequestContext) -> Dict[str, Any]:
        """Get current system health alerts and warnings."""
        status = _call_bridge(ctx, "health/alerts", lambda client: client.get_circuit_breaker_status())
        health = _call_bridge(ctx, "health/status", lambda client: client.get_bridge_health())

        alerts = []
        if hasattr(health, 'thread_pool_utilization') and health.thread_pool_utilization > 0.9:
            alerts.append({"level": "warning", "message": "Thread pool utilization is high", "metric": "thread_pool_utilization", "value": health.thread_pool_utilization})

        if hasattr(health, 'memory_usage_mb') and health.memory_usage_mb > 1000:
            alerts.append({"level": "warning", "message": "High memory usage detected", "metric": "memory_usage_mb", "value": health.memory_usage_mb})

        return {
            "circuit_breaker_status": status,
            "health_alerts": alerts,
            "overall_status": "healthy" if not alerts else "warning"
        }

    @mcp.tool()
    def get_performance_trends(ctx: RequestContext, hours: int = 24) -> Dict[str, Any]:
        """Get performance trend analysis over the specified time period."""
        report = _call_bridge(ctx, "analytics/trends", lambda client: client.get_performance_report())
        return {
            "performance_report": report.model_dump() if hasattr(report, 'model_dump') else report.__dict__,
            "analysis_period_hours": hours,
            "trend_analysis": "Performance data collected for trend analysis"
        }

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
        return _call_bridge(ctx, "command/chat", lambda client: client.transport.dispatch("chat", {"message": "#path"}))

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
    def minecraft_help(
        topic: str = "workflow",
        task: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Return static, read-only guidance for MCP agents."""
        normalized = _normalize_guidance_topic(topic)
        if normalized in {"build-plan", "buildplan", "planner", "build-plan-guidance", "build_planner"}:
            return {"topic": normalized, "prompt": build_build_plan_prompt(task)}
        if normalized in {"buildsite", "build-site", "build-site-guidance", "build-site-review"}:
            return {"topic": normalized, "prompt": build_buildsite_prompt(task)}
        return {"topic": normalized, "prompt": build_workflow_prompt(task)}

    @mcp.tool()
    def minecraft_describe_tool(name: str) -> Dict[str, Any]:
        """Return stable guidance metadata for the selected MCP tool."""
        return get_tool_description(name)

    @mcp.tool()
    def inspect_build_site(radius: int, ctx: RequestContext) -> Dict[str, Any]:
        """Inspect a build site candidate by radius before any mutation."""
        if radius < 4 or radius > 24:
            raise ValueError("inspect_build_site radius must be between 4 and 24 blocks.")
        return _call_bridge(ctx, "inspect_build_site", lambda client: client.command.run(f"inspect_build_site {radius}"))

    @mcp.tool()
    def preview_build_plan(build_plan: Any) -> Dict[str, Any]:
        """Generate a deterministic build-plan preview without bridge connection."""
        preview = compile_plan_preview(build_plan)
        return preview.model_dump()

    @mcp.tool()
    def damn(ctx: RequestContext) -> Dict[str, Any]:
        """daniel"""
        return _call_bridge(ctx, "command/run", lambda client: client.command.run("damn"))

    @mcp.tool()
    def read_file(file_path: str, ctx: RequestContext) -> Dict[str, Any]:
        """Read the contents of a file."""
        try:
            with open(file_path, 'r') as f:
                content = f.read()
            return {"success": True, "content": content}
        except Exception as e:
            return {"success": False, "error": str(e)}

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
