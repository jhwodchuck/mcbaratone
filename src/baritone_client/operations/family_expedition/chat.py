"""Family-expedition vocabulary built on reusable chat-control primitives."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, Optional, Sequence, Tuple

from ...chat_control import (
    AllowlistedChatGateway,
    ChatMessageSource,
    ObservedChatMessage,
    PrefixedChatRequest,
    ServerLogChatSource,
    parse_prefixed_chat,
    parse_server_chat_line,
    validate_chat_prefix,
)


class FamilyChatVerb(str, Enum):
    """Small, explicit command vocabulary accepted from family chat."""

    HELP = "help"
    HELLO = "hello"
    STATUS = "status"
    START = "start"
    HOLD = "hold"
    RESUME = "resume"
    ACTIVATE = "activate"
    ENTER = "enter"
    FIGHT = "fight"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class FamilyChatRequest:
    actor: str
    verb: FamilyChatVerb
    message: str


_VERB_ALIASES = {
    "": FamilyChatVerb.HELP,
    "help": FamilyChatVerb.HELP,
    "commands": FamilyChatVerb.HELP,
    "hello": FamilyChatVerb.HELLO,
    "hi": FamilyChatVerb.HELLO,
    "hey": FamilyChatVerb.HELLO,
    "status": FamilyChatVerb.STATUS,
    "ready": FamilyChatVerb.STATUS,
    "ready?": FamilyChatVerb.STATUS,
    "start": FamilyChatVerb.START,
    "go": FamilyChatVerb.START,
    "let's go": FamilyChatVerb.START,
    "lets go": FamilyChatVerb.START,
    "hold": FamilyChatVerb.HOLD,
    "stop": FamilyChatVerb.HOLD,
    "pause": FamilyChatVerb.HOLD,
    "resume": FamilyChatVerb.RESUME,
    "continue": FamilyChatVerb.RESUME,
    "activate": FamilyChatVerb.ACTIVATE,
    "open portal": FamilyChatVerb.ACTIVATE,
    "open the portal": FamilyChatVerb.ACTIVATE,
    "enter": FamilyChatVerb.ENTER,
    "come through": FamilyChatVerb.ENTER,
    "fight": FamilyChatVerb.FIGHT,
    "fight dragon": FamilyChatVerb.FIGHT,
    "fight the dragon": FamilyChatVerb.FIGHT,
}


def _family_request(request: PrefixedChatRequest) -> FamilyChatRequest:
    command = request.command.casefold()
    return FamilyChatRequest(
        actor=request.actor,
        verb=_VERB_ALIASES.get(command, FamilyChatVerb.UNKNOWN),
        message=request.message,
    )


def parse_family_chat(
    message: str,
    actor: str,
    *,
    allowed_actors: Iterable[str],
    prefix: str = "!family",
) -> Optional[FamilyChatRequest]:
    """Map one authorized prefixed request into the expedition vocabulary."""
    request = parse_prefixed_chat(
        message,
        actor,
        allowed_actors=allowed_actors,
        prefix=prefix,
    )
    return _family_request(request) if request is not None else None


class FamilyChatGateway(AllowlistedChatGateway):
    """Thin family-vocabulary adapter over the shared chat gateway."""

    def __init__(
        self,
        tailer: ChatMessageSource,
        speaker_client: Any,
        *,
        speaker_name: str,
        allowed_actors: Sequence[str],
        prefix: str = "!family",
    ) -> None:
        super().__init__(
            tailer,
            speaker_client.transport.dispatch,
            speaker_name=speaker_name,
            allowed_actors=allowed_actors,
            prefix=prefix,
        )

    def poll(self) -> Tuple[FamilyChatRequest, ...]:
        return tuple(_family_request(request) for request in super().poll())


# Compatibility names keep the expedition API stable while their
# implementations now live in the public, reusable chat-control module.
ServerChatMessage = ObservedChatMessage
ServerChatTailer = ServerLogChatSource


__all__ = [
    "FamilyChatGateway",
    "FamilyChatRequest",
    "FamilyChatVerb",
    "ServerChatMessage",
    "ServerChatTailer",
    "parse_family_chat",
    "parse_server_chat_line",
    "validate_chat_prefix",
]
