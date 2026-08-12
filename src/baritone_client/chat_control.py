from __future__ import annotations

"""Secure chat command parsing and transport-aware follow control."""

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import (
    Any,
    Callable,
    Dict,
    Iterable,
    Mapping,
    Optional,
    Protocol,
    Sequence,
    Tuple,
    Union,
)
import re

from .transport.transport import WebSocketTransport

DispatchProvider = Callable[[str, Dict[str, Any]], Union[Mapping[str, Any], None]]
StateProvider = Callable[[], Optional[Mapping[str, Any]]]
HomeProvider = Callable[[], Optional[Union[Mapping[str, Any], Sequence[Any]]]]


_SERVER_CHAT_LINE = re.compile(
    r"^\[[^\]]+\] \[[^\]]+/INFO\]: "
    r"(?:\[Not Secure\] )?<(?P<actor>[^>\r\n]+)> (?P<message>[^\r\n]*)$"
)
_SAFE_CHAT_PREFIX = re.compile(r"^![A-Za-z0-9_-]{1,24}$")


@dataclass(frozen=True)
class ObservedChatMessage:
    """One actor/message pair from an authoritative Minecraft chat source."""

    actor: str
    message: str


@dataclass(frozen=True)
class PrefixedChatRequest:
    """Authorized, prefix-stripped input for a domain-specific controller."""

    actor: str
    command: str
    message: str


class ChatMessageSource(Protocol):
    """Minimal source contract shared by log, plugin, and event adapters."""

    def poll(self) -> Sequence[ObservedChatMessage]:
        """Return newly observed messages exactly once."""


def validate_chat_prefix(value: Any) -> str:
    """Validate a prefix without allowing whitespace or command syntax."""
    if not isinstance(value, str) or not _SAFE_CHAT_PREFIX.fullmatch(value):
        raise ValueError("chat prefix must look like !bots")
    return value


def parse_server_chat_line(line: str) -> Optional[ObservedChatMessage]:
    """Parse one vanilla dedicated-server player-chat log line."""
    match = _SERVER_CHAT_LINE.fullmatch(str(line).rstrip("\r\n"))
    if match is None:
        return None
    actor = match.group("actor").strip()
    message = match.group("message").strip()
    if not actor or not message:
        return None
    return ObservedChatMessage(actor=actor, message=message)


def parse_prefixed_chat(
    message: str,
    actor: str,
    *,
    allowed_actors: Iterable[str],
    prefix: str,
) -> Optional[PrefixedChatRequest]:
    """Authorize an exact actor and extract one explicitly prefixed command."""
    prefix = validate_chat_prefix(prefix)
    actor_name = str(actor).strip()
    allowed = {str(item).strip() for item in allowed_actors if str(item).strip()}
    if actor_name not in allowed:
        return None
    normalized = " ".join(str(message).strip().split())
    prefix_text = prefix.casefold()
    lowered = normalized.casefold()
    if lowered == prefix_text:
        command = ""
    elif lowered.startswith(prefix_text + " "):
        command = normalized[len(prefix) + 1 :].strip()
    else:
        return None
    return PrefixedChatRequest(
        actor=actor_name,
        command=command,
        message=normalized,
    )


class ServerLogChatSource:
    """Read only new complete messages from a Minecraft dedicated-server log."""

    def __init__(
        self,
        path: Path,
        *,
        start_at_end: bool = True,
        line_parser: Callable[[str], Optional[ObservedChatMessage]] = (
            parse_server_chat_line
        ),
    ) -> None:
        self.path = Path(path)
        self._line_parser = line_parser
        self._identity: Optional[Tuple[int, int]] = None
        self._offset = 0
        self._pending = b""
        self._initialized = False
        self._start_at_end = bool(start_at_end)
        self._initialize_if_present()

    @staticmethod
    def _file_identity(stat: Any) -> Tuple[int, int]:
        inode = int(getattr(stat, "st_ino", 0))
        # Some Windows filesystems report inode zero. Creation time is stable
        # there and still distinguishes a replaced latest.log; Unix uses inode.
        file_marker = inode or int(getattr(stat, "st_ctime_ns", 0))
        return (int(getattr(stat, "st_dev", 0)), file_marker)

    def _initialize_if_present(self) -> None:
        try:
            stat = self.path.stat()
        except OSError:
            return
        self._identity = self._file_identity(stat)
        self._offset = int(stat.st_size) if self._start_at_end else 0
        self._pending = b""
        self._initialized = True

    def poll(self, *, max_bytes: int = 262_144) -> Tuple[ObservedChatMessage, ...]:
        """Poll a bounded byte range; partial final lines stay buffered."""
        try:
            stat = self.path.stat()
        except OSError:
            return ()
        identity = self._file_identity(stat)
        if not self._initialized:
            self._identity = identity
            self._offset = int(stat.st_size) if self._start_at_end else 0
            self._pending = b""
            self._initialized = True
            return ()
        if identity != self._identity:
            # A newly rotated log contains only events after rotation, so read it
            # from the beginning. Existing startup history was already skipped.
            self._identity = identity
            self._offset = 0
            self._pending = b""
        elif int(stat.st_size) < self._offset:
            self._offset = 0
            self._pending = b""
        try:
            with self.path.open("rb") as handle:
                handle.seek(self._offset)
                chunk = handle.read(max(1, int(max_bytes)))
                self._offset = handle.tell()
        except OSError:
            return ()
        if not chunk:
            return ()
        data = self._pending + chunk
        complete = data.splitlines(keepends=True)
        self._pending = b""
        if complete and not complete[-1].endswith((b"\n", b"\r")):
            self._pending = complete.pop()
        messages = []
        for raw_line in complete:
            parsed = self._line_parser(
                raw_line.decode("utf-8", errors="replace")
            )
            if parsed is not None:
                messages.append(parsed)
        return tuple(messages)


class AllowlistedChatGateway:
    """Authorize prefixed requests and send replies through any bot transport."""

    def __init__(
        self,
        source: ChatMessageSource,
        dispatch: DispatchProvider,
        *,
        speaker_name: str,
        allowed_actors: Sequence[str],
        prefix: str = "!bot",
        max_reply_length: int = 240,
    ) -> None:
        if source is None:
            raise ValueError("chat source is required")
        if dispatch is None:
            raise ValueError("chat reply dispatch is required")
        speaker = str(speaker_name).strip()
        if not speaker:
            raise ValueError("chat speaker name is required")
        actors = tuple(
            dict.fromkeys(
                str(item).strip() for item in allowed_actors if str(item).strip()
            )
        )
        if speaker in set(actors):
            raise ValueError("the bot spokesperson must not be a chat-control actor")
        if not 1 <= int(max_reply_length) <= 256:
            raise ValueError("max_reply_length must be between 1 and 256")
        self.source = source
        self._dispatch = dispatch
        self.speaker_name = speaker
        self.allowed_actors = actors
        self.prefix = validate_chat_prefix(prefix)
        self.max_reply_length = int(max_reply_length)

    def poll(self) -> Tuple[PrefixedChatRequest, ...]:
        """Return only new prefixed requests from exact allowlisted actors."""
        requests = []
        for chat in self.source.poll():
            request = parse_prefixed_chat(
                chat.message,
                chat.actor,
                allowed_actors=self.allowed_actors,
                prefix=self.prefix,
            )
            if request is not None:
                requests.append(request)
        return tuple(requests)

    def reply(self, message: str) -> bool:
        """Send one short, sanitized, non-command response through the bot."""
        clean = " ".join(
            str(message).replace("\r", " ").replace("\n", " ").split()
        )
        if not clean or clean.startswith(("#", "/")):
            return False
        clean = clean[: self.max_reply_length]
        try:
            self._dispatch("chat", {"message": clean})
            return True
        except Exception:
            return False


class ChatCommandError(ValueError):
    """Base error for chat control parsing and execution."""


class ChatUnauthorizedActorError(ChatCommandError):
    """Raised when a chat actor is not in the allowlist."""

    def __init__(self, actor: str):
        super().__init__(f"Actor '{actor}' is not authorized")
        self.actor = actor


class ChatParseError(ChatCommandError):
    """Raised when a command is not in the strict allowlist."""


class UnsupportedCapabilityError(ChatCommandError):
    """Raised when a command is configured as unsupported."""


class FollowState(str, Enum):
    """Internal follow-controller state."""

    IDLE = "idle"
    FOLLOWING = "following"
    PAUSED = "paused"
    RETURNING = "returning"


class ParsedAction(str, Enum):
    """Supported strict chat actions."""

    FOLLOW = "follow"
    PAUSE = "pause"
    RESUME = "resume"
    RETURN_HOME = "return_home"
    STATUS = "status"


@dataclass(frozen=True)
class ParsedCommand:
    """Result from `parse_chat_command`."""

    action: ParsedAction
    actor: str
    target: Optional[str] = None


_FOLLOW_RE = re.compile(r"^follow\s+(?P<target>\S+)$", re.IGNORECASE)
_PLAYER_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


def parse_chat_command(
    message: str,
    actor: str,
    allowed_actors: Optional[Iterable[str]] = None,
) -> ParsedCommand:
    """Parse and authorize a chat command.

    Allowed commands are strictly:

    - ``follow <player>``
    - ``pause``
    - ``resume``
    - ``return home``
    - ``status``
    """

    actor_name = str(actor).strip() if actor is not None else ""
    if not actor_name:
        raise ChatParseError("Actor is required")

    normalized_message = " ".join(str(message).strip().split()) if message is not None else ""
    if not normalized_message:
        raise ChatParseError("Empty command")

    if allowed_actors is not None:
        allowed = {str(a).strip() for a in allowed_actors if str(a).strip()}
        if actor_name not in allowed:
            raise ChatUnauthorizedActorError(actor_name)

    lowered = normalized_message.lower()
    if lowered == "pause":
        return ParsedCommand(action=ParsedAction.PAUSE, actor=actor_name)
    if lowered == "resume":
        return ParsedCommand(action=ParsedAction.RESUME, actor=actor_name)
    if lowered == "return home":
        return ParsedCommand(action=ParsedAction.RETURN_HOME, actor=actor_name)
    if lowered == "status":
        return ParsedCommand(action=ParsedAction.STATUS, actor=actor_name)

    follow_match = _FOLLOW_RE.fullmatch(normalized_message)
    if follow_match:
        target = follow_match.group("target")
        if not _PLAYER_NAME_RE.fullmatch(target):
            raise ChatParseError(f"Invalid follow target: {target!r}")
        return ParsedCommand(action=ParsedAction.FOLLOW, actor=actor_name, target=target)

    raise ChatParseError(f"Unsupported command: {normalized_message!r}")


@dataclass(frozen=True)
class FollowCommandConfig:
    """Injectable route/name configuration for bridge commands.

    `follow_route` and `return_home_route` are the logical route names passed to
    `dispatch`. Some bridges support direct routes while others expose command
    wrappers; both can be represented by this config.
    """

    follow_route: str = "follow"
    follow_param_name: str = "entity"
    follow_command_name: Optional[str] = None

    pause_route: Optional[str] = "process/pause"
    resume_route: Optional[str] = "process/resume"
    cancel_route: Optional[str] = "cancel"

    return_home_route: Optional[str] = None
    return_home_command_name: Optional[str] = None
    return_home_param_names: Tuple[str, str, str] = ("x", "y", "z")


@dataclass(frozen=True)
class FollowControllerResult:
    """Result object for chat-controller actions."""

    action: ParsedAction
    actor: str
    state: FollowState
    target: Optional[str]
    dispatched: bool
    state_changed: bool
    message: str
    details: Dict[str, Any] = field(default_factory=dict)


class FollowController:
    """Stateful follow controller with transport-aware status handling."""

    def __init__(
        self,
        dispatch: DispatchProvider,
        state_provider: StateProvider,
        home_provider: HomeProvider,
        *,
        allowed_actors: Optional[Iterable[str]] = None,
        command_config: Optional[FollowCommandConfig] = None,
        transport: Any = None,
    ) -> None:
        if dispatch is None:
            raise ValueError("dispatch is required")
        if state_provider is None:
            raise ValueError("state_provider is required")
        if home_provider is None:
            raise ValueError("home_provider is required")

        self._dispatch = dispatch
        self._state_provider = state_provider
        self._home_provider = home_provider
        self._allowed_actors = set(allowed_actors) if allowed_actors is not None else None
        self._command_config = command_config or FollowCommandConfig()
        self._transport = transport if transport is not None else getattr(dispatch, "__self__", None)

        self._state = FollowState.IDLE
        self._target: Optional[str] = None

    def get_capabilities(self) -> Dict[str, Any]:
        """Report capabilities with transport-aware mode."""
        websocket_mode = self._uses_websocket_events()
        return {
            "transport": {
                "name": self._resolve_transport_name(),
                "status_mode": "websocket" if websocket_mode else "polling",
                "uses_websocket_events": websocket_mode,
            },
            "commands": {
                "follow": self._command_config.follow_route is not None,
                "pause": self._command_config.pause_route is not None,
                "resume": self._command_config.resume_route is not None,
                "cancel": self._command_config.cancel_route is not None,
                "return_home": self._is_return_home_configured(),
            },
        }

    def handle(self, actor: str, raw_message: str) -> FollowControllerResult:
        """Parse and execute one command from one actor."""
        command = parse_chat_command(raw_message, actor, self._allowed_actors)

        if command.action == ParsedAction.FOLLOW:
            return self._handle_follow(command)
        if command.action == ParsedAction.PAUSE:
            return self._handle_pause(command)
        if command.action == ParsedAction.RESUME:
            return self._handle_resume(command)
        if command.action == ParsedAction.RETURN_HOME:
            return self._handle_return_home(command)
        if command.action == ParsedAction.STATUS:
            return self._handle_status(command)

        raise ChatParseError(f"Unhandled action: {command.action}")

    def _handle_follow(self, command: ParsedCommand) -> FollowControllerResult:
        if command.target is None:
            raise ChatParseError("follow requires a player")

        if self._state == FollowState.FOLLOWING and self._target == command.target:
            return FollowControllerResult(
                action=ParsedAction.FOLLOW,
                actor=command.actor,
                state=self._state,
                target=self._target,
                dispatched=False,
                state_changed=False,
                message="Already following requested player",
            )

        previous_state = self._state
        route, payload = self._build_follow_payload(command.target)
        self._dispatch(route, payload)

        self._state = FollowState.FOLLOWING
        self._target = command.target

        return FollowControllerResult(
            action=ParsedAction.FOLLOW,
            actor=command.actor,
            state=self._state,
            target=self._target,
            dispatched=True,
            state_changed=previous_state != self._state,
            message=f"Following {command.target}",
            details={"dispatched_route": route, "dispatched_payload": payload},
        )

    def _handle_pause(self, command: ParsedCommand) -> FollowControllerResult:
        if self._state == FollowState.PAUSED:
            return FollowControllerResult(
                action=ParsedAction.PAUSE,
                actor=command.actor,
                state=self._state,
                target=self._target,
                dispatched=False,
                state_changed=False,
                message="Already paused",
            )

        if self._state not in (FollowState.FOLLOWING, FollowState.RETURNING):
            return FollowControllerResult(
                action=ParsedAction.PAUSE,
                actor=command.actor,
                state=self._state,
                target=self._target,
                dispatched=False,
                state_changed=False,
                message="Nothing to pause",
            )

        route = self._command_config.pause_route
        if route is None:
            raise UnsupportedCapabilityError("pause")

        previous_state = self._state
        self._dispatch(route, {})

        self._state = FollowState.PAUSED

        return FollowControllerResult(
            action=ParsedAction.PAUSE,
            actor=command.actor,
            state=self._state,
            target=self._target,
            dispatched=True,
            state_changed=previous_state != self._state,
            message="Paused follow",
            details={"dispatched_route": route, "dispatched_payload": {}},
        )

    def _handle_resume(self, command: ParsedCommand) -> FollowControllerResult:
        if self._state != FollowState.PAUSED:
            return FollowControllerResult(
                action=ParsedAction.RESUME,
                actor=command.actor,
                state=self._state,
                target=self._target,
                dispatched=False,
                state_changed=False,
                message="Nothing to resume",
            )

        route = self._command_config.resume_route
        if route is None:
            raise UnsupportedCapabilityError("resume")

        previous_state = self._state
        self._dispatch(route, {})

        self._state = FollowState.FOLLOWING

        return FollowControllerResult(
            action=ParsedAction.RESUME,
            actor=command.actor,
            state=self._state,
            target=self._target,
            dispatched=True,
            state_changed=previous_state != self._state,
            message="Resumed follow",
            details={"dispatched_route": route, "dispatched_payload": {}},
        )

    def _handle_return_home(self, command: ParsedCommand) -> FollowControllerResult:
        if self._state == FollowState.RETURNING:
            return FollowControllerResult(
                action=ParsedAction.RETURN_HOME,
                actor=command.actor,
                state=self._state,
                target=self._target,
                dispatched=False,
                state_changed=False,
                message="Already returning home",
            )

        if not self._is_return_home_configured():
            raise UnsupportedCapabilityError("return_home")

        canceled = False
        if self._state in (FollowState.FOLLOWING, FollowState.PAUSED):
            cancel_route = self._command_config.cancel_route
            if cancel_route is None:
                raise UnsupportedCapabilityError("cancel")
            self._dispatch(cancel_route, {})
            canceled = True

        home = self._resolve_home_position()
        route, payload = self._build_payload(self._command_config.return_home_route, self._command_config.return_home_command_name, home)

        previous_state = self._state
        self._dispatch(route, payload)
        self._state = FollowState.RETURNING
        self._target = None

        details = {
            "canceled": canceled,
            "home": dict(home),
            "dispatched_route": route,
            "dispatched_payload": payload,
        }

        return FollowControllerResult(
            action=ParsedAction.RETURN_HOME,
            actor=command.actor,
            state=self._state,
            target=self._target,
            dispatched=True,
            state_changed=previous_state != self._state,
            message="Returning home",
            details=details,
        )

    def _handle_status(self, command: ParsedCommand) -> FollowControllerResult:
        capabilities = self.get_capabilities()
        use_polling = capabilities["transport"]["status_mode"] == "polling"
        snapshot = self._get_state_snapshot(use_polling)

        return FollowControllerResult(
            action=ParsedAction.STATUS,
            actor=command.actor,
            state=self._state,
            target=self._target,
            dispatched=False,
            state_changed=False,
            message="Status",
            details={"capabilities": capabilities, "state_snapshot": snapshot},
        )

    def _build_follow_payload(self, target: str) -> Tuple[str, Dict[str, Any]]:
        return self._build_payload(
            self._command_config.follow_route,
            self._command_config.follow_command_name,
            {self._command_config.follow_param_name: target},
        )

    def _build_payload(
        self,
        route: Optional[str],
        command_name: Optional[str],
        params: Mapping[str, Any],
    ) -> Tuple[str, Dict[str, Any]]:
        if route is None:
            raise UnsupportedCapabilityError("unsupported route")
        if route == "command" and not command_name:
            raise UnsupportedCapabilityError("unsupported command route configuration")
        if route == "command":
            return (route, {"command": command_name, "params": dict(params)})
        return (route, dict(params))

    def _is_return_home_configured(self) -> bool:
        if self._command_config.return_home_route is None:
            return False
        if self._command_config.return_home_route == "command" and not self._command_config.return_home_command_name:
            return False
        return self._home_provider() is not None

    def _resolve_home_position(self) -> Dict[str, Any]:
        home = self._home_provider()
        if home is None:
            raise UnsupportedCapabilityError("return_home")

        x_key, y_key, z_key = self._command_config.return_home_param_names
        if isinstance(home, Mapping):
            x = home.get(x_key)
            y = home.get(y_key)
            z = home.get(z_key)
            if x is None or y is None or z is None:
                raise ChatParseError("Home provider returned incomplete coordinates")
            return {x_key: x, y_key: y, z_key: z}

        if isinstance(home, Sequence) and len(home) == 3:
            return {x_key: home[0], y_key: home[1], z_key: home[2]}

        raise ChatParseError("Home provider must return mapping or 3-value sequence")

    def _get_state_snapshot(self, use_polling: bool) -> Optional[Mapping[str, Any]]:
        if not use_polling:
            return None
        return self._state_provider()

    def _uses_websocket_events(self) -> bool:
        transport_name = self._resolve_transport_name()
        if transport_name.lower() == "websocket":
            return True
        transport = self._transport
        return isinstance(transport, WebSocketTransport)

    def _resolve_transport_name(self) -> str:
        transport = self._transport
        if transport is None:
            return "unknown"
        if isinstance(transport, WebSocketTransport):
            return "websocket"

        transport_name_getter = getattr(transport, "get_current_transport_name", None)
        if callable(transport_name_getter):
            try:
                name = transport_name_getter()
                if name:
                    return str(name)
            except Exception:
                pass

        return getattr(transport, "name", type(transport).__name__) or "unknown"
