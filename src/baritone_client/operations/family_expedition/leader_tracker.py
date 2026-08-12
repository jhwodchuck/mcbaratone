"""RCON leader tracking primitives for family expedition coordination."""

from __future__ import annotations

import re
import time
from typing import Optional, Protocol, Tuple

from .models import Vec3, PlayerSnapshot, validate_player_name


_FLOAT_PATTERN = re.compile(
    r"[-+]?(?:\d+\.\d+|\d+|\.\d+)(?:[eE][-+]?\d+)?[dDfF]?"
)


class RconCommandResponse(Protocol):
    """Minimal response contract from an injected RCON command client."""

    body: str


class RconCommandClient(Protocol):
    """Dependency-light command transport contract."""

    def command(self, command: str) -> RconCommandResponse:
        ...


class RconLeaderTracker:
    """Track one leader via RCON entity lookups."""

    LIST_PREFIX = "players online:"
    END_DIMENSIONS = frozenset({"minecraft:the_end", "the_end", "end"})
    ENTITY_PREFIX = "has the following entity data:"

    def __init__(
        self,
        client: RconCommandClient,
        leader_name: str,
        *,
        stale_threshold_seconds: float = 30.0,
        monotonic_clock=time.monotonic,
        wall_clock=time.time,
    ) -> None:
        self._client = client
        self._leader_name = validate_player_name(leader_name)
        self._stale_threshold_seconds = float(stale_threshold_seconds)
        self._monotonic_clock = monotonic_clock
        self._wall_clock = wall_clock
        self._latest_snapshot: Optional[PlayerSnapshot] = None

    @property
    def leader_name(self) -> str:
        return self._leader_name

    def snapshot(self) -> PlayerSnapshot:
        """Capture a fresh snapshot of the leader."""
        now_mono = float(self._monotonic_clock())
        now_wall = float(self._wall_clock())
        if self._leader_name not in self._online_players():
            snapshot = PlayerSnapshot(
                name=self._leader_name,
                online=False,
                observed_at_wall=now_wall,
                observed_at_monotonic=now_mono,
            )
            self._latest_snapshot = snapshot
            return snapshot

        snapshot = PlayerSnapshot(
            name=self._leader_name,
            online=True,
            position=self._read_position(),
            rotation=self._read_rotation(),
            dimension=self._read_dimension(),
            health=self._read_health(),
            observed_at_wall=now_wall,
            observed_at_monotonic=now_mono,
        )
        self._latest_snapshot = snapshot
        return snapshot

    def is_fresh(self, snapshot: Optional[PlayerSnapshot] = None, *, now: Optional[float] = None) -> bool:
        """Return whether a snapshot is still within the staleness threshold."""
        current = snapshot if snapshot is not None else self._latest_snapshot
        if (
            current is None
            or not current.online
            or current.position is None
            or not current.dimension
            or current.observed_at_monotonic is None
        ):
            return False
        if self._stale_threshold_seconds <= 0:
            return False
        now = float(self._monotonic_clock() if now is None else now)
        age = now - float(current.observed_at_monotonic)
        return age <= self._stale_threshold_seconds

    def leader_in_end(self, snapshot: Optional[PlayerSnapshot] = None) -> bool:
        """Return whether the leader is currently in the End dimension."""
        current = snapshot if snapshot is not None else self._latest_snapshot
        if current is None:
            current = self.snapshot()
        if not current.online or not current.dimension:
            return False
        return current.dimension.lower() in self.END_DIMENSIONS

    def _online_players(self) -> Tuple[str, ...]:
        response = self._command("list")
        marker = response.casefold().find(self.LIST_PREFIX)
        if marker >= 0:
            response = response[marker + len(self.LIST_PREFIX) :]
        response = response.strip()
        if not response:
            return tuple()
        return tuple(
            player_name.strip()
            for player_name in response.split(",")
            if player_name.strip()
        )

    def _read_position(self) -> Optional[Vec3]:
        values = self._read_float_list("Pos")
        if values is None or len(values) < 3:
            return None
        return Vec3(values[0], values[1], values[2])

    def _read_rotation(self) -> Optional[Tuple[float, float]]:
        values = self._read_float_list("Rotation")
        if values is None or len(values) < 2:
            return None
        return (values[0], values[1])

    def _read_dimension(self) -> Optional[str]:
        value = self._read_scalar("Dimension")
        if not value:
            return None
        return value

    def _read_health(self) -> Optional[float]:
        value = self._read_scalar("Health")
        if value is None:
            return None
        try:
            return float(value.rstrip("dDfF"))
        except (TypeError, ValueError):
            return None

    def _read_float_list(self, path: str) -> Optional[Tuple[float, ...]]:
        payload = self._read_entity(path)
        if not payload:
            return None
        start = payload.find("[")
        if start < 0:
            return None
        end = self._find_matching_bracket(payload, start)
        if end < 0:
            return None
        return self._parse_float_list(payload[start + 1 : end])

    def _read_scalar(self, path: str) -> Optional[str]:
        payload = self._read_entity(path)
        if not payload:
            return None
        if payload.startswith('"') and payload.endswith('"') and len(payload) > 1:
            return payload[1:-1]
        quoted = self._extract_quoted(payload)
        if quoted:
            return quoted
        value = self._extract_first_float(payload)
        if value is not None:
            return value
        token = payload.strip()
        if " " in token:
            token = token.split(" ", 1)[0].strip()
        return token if token else None

    def _read_entity(self, path: str) -> Optional[str]:
        body = self._command(f"data get entity {self._leader_name} {path}")
        return self._extract_entity_payload(body)

    def _extract_entity_payload(self, body: str) -> Optional[str]:
        if not body:
            return None
        if self.ENTITY_PREFIX in body:
            return body.split(self.ENTITY_PREFIX, 1)[1].strip()
        return body.strip()

    def _parse_float_list(self, text: str) -> Optional[Tuple[float, ...]]:
        if not text:
            return None
        values = []
        for match in _FLOAT_PATTERN.finditer(text):
            try:
                values.append(float(match.group(0).rstrip("dDfF")))
            except ValueError:
                return None
        return tuple(values)

    @staticmethod
    def _extract_first_float(text: str) -> Optional[str]:
        match = _FLOAT_PATTERN.search(text)
        return match.group(0) if match else None

    @staticmethod
    def _extract_quoted(text: str) -> Optional[str]:
        if not text.startswith('"'):
            return None
        escaped = False
        for index in range(1, len(text)):
            char = text[index]
            if escaped:
                escaped = False
                continue
            if char == "\\":
                escaped = True
                continue
            if char == '"':
                return text[1:index]
        return None

    def _command(self, command: str) -> str:
        response = self._client.command(command)
        return str(response.body)

    @staticmethod
    def _find_matching_bracket(payload: str, start: int) -> int:
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(payload)):
            char = payload[index]
            if in_string:
                if escaped:
                    escaped = False
                    continue
                if char == "\\":
                    escaped = True
                    continue
                if char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
                continue
            if char == "[":
                depth += 1
            elif char == "]":
                depth -= 1
                if depth == 0:
                    return index
        return -1

    def is_leader_online(self) -> bool:
        current = self.snapshot()
        return current.online
