from typing import Any, Dict, List, Optional, Sequence, Tuple

import pytest

from baritone_client.chat_control import (
    AllowlistedChatGateway,
    ChatParseError,
    ChatUnauthorizedActorError,
    FollowCommandConfig,
    FollowController,
    FollowControllerResult,
    FollowState,
    ObservedChatMessage,
    ParsedAction,
    PrefixedChatRequest,
    ServerLogChatSource,
    UnsupportedCapabilityError,
    parse_chat_command,
    parse_prefixed_chat,
    parse_server_chat_line,
    validate_chat_prefix,
)


class DummyTransport:
    def __init__(self, name: str = "tcp"):
        self.name = name
        self.calls: List[Tuple[str, Dict[str, Any]]] = []

    def get_current_transport_name(self) -> str:
        return self.name

    def dispatch(self, route: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        self.calls.append((route, payload))
        return {"status": "ok"}


class DummyChatSource:
    def __init__(self, messages: Sequence[ObservedChatMessage]):
        self.messages = tuple(messages)

    def poll(self) -> Tuple[ObservedChatMessage, ...]:
        messages = self.messages
        self.messages = ()
        return messages


def test_parse_chat_command_allowlist_and_targets() -> None:
    allowed = {"Alice", "Bob"}

    follow = parse_chat_command("follow Steve123", "Alice", allowed_actors=allowed)
    assert follow.action.value == "follow"
    assert follow.target == "Steve123"
    assert follow.actor == "Alice"

    assert parse_chat_command("pause", "Bob", allowed_actors=allowed).action == "pause"
    assert parse_chat_command("return   home", "Bob", allowed_actors=allowed).action == "return_home"
    assert parse_chat_command("status", "Bob", allowed_actors=allowed).action == "status"

    bedrock = parse_chat_command(
        "follow .CommanderRykerH",
        "Alice",
        allowed_actors=allowed,
    )
    assert bedrock.target == ".CommanderRykerH"


def test_parse_chat_command_rejects_invalid_actor_and_payload() -> None:
    with pytest.raises(ChatUnauthorizedActorError):
        parse_chat_command("pause", "Mallory", allowed_actors={"Alice"})

    with pytest.raises(ChatParseError):
        parse_chat_command("follow", "Alice", allowed_actors={"Alice"})

    with pytest.raises(ChatParseError):
        parse_chat_command("follow invalid/name", "Alice", allowed_actors={"Alice"})

    with pytest.raises(ChatParseError):
        parse_chat_command("teleport home", "Alice", allowed_actors={"Alice"})


def test_follow_controller_restricts_state_and_stops_duplicate_follow() -> None:
    transport = DummyTransport("tcp")
    state_updates: List[Dict[str, Any]] = []

    def state_provider() -> Dict[str, Any]:
        state_updates.append({})
        return {"is_pathing": True, "position": {"x": 0, "y": 64, "z": 0}}

    home = lambda: None
    controller = FollowController(
        transport.dispatch,
        state_provider=state_provider,
        home_provider=home,
        allowed_actors={"Alice"},
        transport=transport,
    )

    first = controller.handle("Alice", "follow Steve")
    assert first.state == FollowState.FOLLOWING
    assert first.dispatched is True
    assert first.target == "Steve"
    assert first.details["dispatched_route"] == "follow"
    assert first.details["dispatched_payload"] == {"entity": "Steve"}

    second = controller.handle("Alice", "follow Steve")
    assert second.state == FollowState.FOLLOWING
    assert second.dispatched is False
    assert second.state_changed is False
    assert len(transport.calls) == 1


def test_follow_controller_pause_resume_transitions_are_idempotent() -> None:
    transport = DummyTransport("tcp")
    controller = FollowController(
        transport.dispatch,
        state_provider=lambda: None,
        home_provider=lambda: None,
        allowed_actors={"Alice"},
        transport=transport,
    )

    idle_pause = controller.handle("Alice", "pause")
    assert idle_pause.dispatched is False
    assert idle_pause.state == FollowState.IDLE

    follow = controller.handle("Alice", "follow PlayerOne")
    assert follow.dispatched
    assert transport.calls[-1] == ("follow", {"entity": "PlayerOne"})

    paused = controller.handle("Alice", "pause")
    assert paused.state == FollowState.PAUSED
    assert paused.dispatched
    assert transport.calls[-1] == ("process/pause", {})

    paused_again = controller.handle("Alice", "pause")
    assert paused_again.dispatched is False

    resumed = controller.handle("Alice", "resume")
    assert resumed.state == FollowState.FOLLOWING
    assert resumed.dispatched
    assert transport.calls[-1] == ("process/resume", {})

    resumed_again = controller.handle("Alice", "resume")
    assert resumed_again.dispatched is False


def test_follow_controller_return_home_cancels_follow_then_sets_returning() -> None:
    transport = DummyTransport("tcp")
    controller = FollowController(
        transport.dispatch,
        state_provider=lambda: None,
        home_provider=lambda: {"x": 10, "y": 64, "z": -8},
        allowed_actors={"Alice"},
        command_config=FollowCommandConfig(
            return_home_route="command",
            return_home_command_name="goto",
        ),
        transport=transport,
    )

    controller.handle("Alice", "follow PlayerOne")
    controller.handle("Alice", "pause")

    home = controller.handle("Alice", "return home")
    assert home.state == FollowState.RETURNING
    assert home.dispatched
    assert transport.calls[-1] == ("command", {"command": "goto", "params": {"x": 10, "y": 64, "z": -8}})
    assert len(transport.calls) == 4
    assert transport.calls[0][0] == "follow"
    assert transport.calls[1][0] == "process/pause"
    assert transport.calls[2][0] == "cancel"
    assert transport.calls[3][0] == "command"


def test_follow_controller_return_home_requires_capability() -> None:
    transport = DummyTransport("tcp")
    controller = FollowController(
        transport.dispatch,
        state_provider=lambda: None,
        home_provider=lambda: {"x": 0, "y": 0, "z": 0},
        allowed_actors={"Alice"},
        command_config=FollowCommandConfig(return_home_route=None),
        transport=transport,
    )

    with pytest.raises(UnsupportedCapabilityError):
        controller.handle("Alice", "return home")


def test_status_reports_websocket_or_polling_mode() -> None:
    ws_transport = DummyTransport("websocket")
    tcp_transport = DummyTransport("tcp")

    ws = FollowController(
        ws_transport.dispatch,
        state_provider=lambda: {"foo": "bar"},
        home_provider=lambda: None,
        transport=ws_transport,
    )
    tcp = FollowController(
        tcp_transport.dispatch,
        state_provider=lambda: {"foo": "bar"},
        home_provider=lambda: None,
        transport=tcp_transport,
    )

    ws_caps = ws.get_capabilities()
    tcp_caps = tcp.get_capabilities()

    assert ws_caps["transport"]["status_mode"] == "websocket"
    assert ws_caps["transport"]["uses_websocket_events"] is True
    assert tcp_caps["transport"]["status_mode"] == "polling"
    assert tcp_caps["transport"]["uses_websocket_events"] is False

    ws_status = ws.handle("Alice", "status")
    tcp_status = tcp.handle("Alice", "status")

    assert isinstance(ws_status, FollowControllerResult)
    assert ws_status.details["state_snapshot"] is None
    assert tcp_status.details["state_snapshot"] == {"foo": "bar"}


def test_prefixed_chat_is_exact_allowlisted_and_domain_neutral() -> None:
    request = parse_prefixed_chat(
        "!crew Follow Me Now",
        ".CommanderRykerH",
        allowed_actors={".CommanderRykerH"},
        prefix="!crew",
    )

    assert request == PrefixedChatRequest(
        actor=".CommanderRykerH",
        command="Follow Me Now",
        message="!crew Follow Me Now",
    )
    assert parse_prefixed_chat(
        "ordinary conversation",
        ".CommanderRykerH",
        allowed_actors={".CommanderRykerH"},
        prefix="!crew",
    ) is None
    assert parse_prefixed_chat(
        "!crew start",
        "Mallory",
        allowed_actors={".CommanderRykerH"},
        prefix="!crew",
    ) is None
    assert validate_chat_prefix("!crew") == "!crew"


def test_server_log_chat_source_handles_real_format_and_stale_lines(tmp_path) -> None:
    log = tmp_path / "latest.log"
    log.write_text(
        "[15:00:00] [Server thread/INFO]: [Not Secure] "
        "<JHWodchuck> !crew stale\n",
        encoding="utf-8",
    )
    source = ServerLogChatSource(log, start_at_end=True)

    assert source.poll() == ()
    with log.open("ab") as handle:
        handle.write(
            b"[15:01:00] [Server thread/INFO]: [Not Secure] "
            b"<.CommanderRykerH> !crew sta"
        )
    assert source.poll() == ()
    with log.open("ab") as handle:
        handle.write(b"tus\r\n")

    expected = ObservedChatMessage(".CommanderRykerH", "!crew status")
    assert source.poll() == (expected,)
    assert parse_server_chat_line(
        "[15:02:00] [Server thread/INFO]: <Alice> hello"
    ) == ObservedChatMessage("Alice", "hello")


def test_allowlisted_gateway_works_with_any_source_and_bot_dispatch() -> None:
    source = DummyChatSource(
        (
            ObservedChatMessage("Mallory", "!crew start"),
            ObservedChatMessage("Alice", "ordinary chat"),
            ObservedChatMessage("Alice", "!crew status"),
            ObservedChatMessage("Bot07", "!crew start"),
        )
    )
    transport = DummyTransport()
    gateway = AllowlistedChatGateway(
        source,
        transport.dispatch,
        speaker_name="Bot07",
        allowed_actors=("Alice",),
        prefix="!crew",
    )

    assert gateway.poll() == (
        PrefixedChatRequest("Alice", "status", "!crew status"),
    )
    assert gateway.reply("Crew ready\nnow")
    assert transport.calls == [("chat", {"message": "Crew ready now"})]
    assert not gateway.reply("#stop")
    assert not gateway.reply("/kill @e")
    assert transport.calls == [("chat", {"message": "Crew ready now"})]
    with pytest.raises(ValueError):
        AllowlistedChatGateway(
            source,
            transport.dispatch,
            speaker_name="Bot07",
            allowed_actors=("Bot07",),
        )
