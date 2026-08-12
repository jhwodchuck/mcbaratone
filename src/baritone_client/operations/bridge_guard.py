"""Fail-closed safety checks using only non-operator client state."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any


class WorkerSafetyError(RuntimeError):
    """Raised when the bridge cannot prove a worker is safe to run."""


class SafetyInterlockError(WorkerSafetyError):
    """Raised when an attested worker state violates a hard safety boundary."""


def _unwrap(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    data = value.get("data", value)
    return data if isinstance(data, dict) else {}


class NonOpClientGuard:
    """Enforce the hacked-client boundary without server authority."""

    PROFILE = "non_op_client"

    def __init__(
        self,
        client: Any,
        bot_name: str,
        protected_players: Iterable[str] = (),
    ) -> None:
        self.client = client
        self.bot_name = str(bot_name)
        self.protected_players = {
            str(name).casefold() for name in protected_players if str(name).strip()
        }

    def __enter__(self) -> "NonOpClientGuard":
        self.require_safe()
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def state(self) -> dict[str, Any]:
        state = _unwrap(self.client.transport.dispatch("get_state", {}))
        if not state:
            raise WorkerSafetyError("Bridge state is unavailable")
        return state

    def require_safe(self) -> dict[str, Any]:
        state = self.state()
        profile = str(state.get("automation_profile", ""))
        if profile != self.PROFILE or state.get("server_authority") is not False:
            raise WorkerSafetyError(
                f"Bridge did not attest {self.PROFILE} capability mode"
            )
        player_name = str(state.get("player_name", ""))
        if player_name != self.bot_name:
            raise SafetyInterlockError(
                f"Bridge identity mismatch: expected {self.bot_name}, got {player_name!r}"
            )
        online_value = state.get("online_players")
        if not isinstance(online_value, Sequence) or isinstance(
            online_value, (str, bytes)
        ):
            raise WorkerSafetyError("Bridge online-player attestation is unavailable")
        online = {str(name).casefold() for name in online_value}
        if player_name.casefold() not in online:
            raise SafetyInterlockError(
                f"Bridge identity {player_name!r} is not present in the online roster"
            )
        protected_online = sorted(self.protected_players & online)
        if protected_online:
            raise SafetyInterlockError(
                "Protected player(s) are online: " + ", ".join(protected_online)
            )
        try:
            health = float(state.get("health", 0.0) or 0.0)
        except (TypeError, ValueError) as error:
            raise WorkerSafetyError("Bridge health attestation is invalid") from error
        if bool(state.get("is_dead")) or health <= 0:
            raise WorkerSafetyError(f"{self.bot_name} has no health")
        self.require_survival(state)
        return state

    def require_survival(self, state: dict[str, Any] | None = None) -> None:
        current = self.require_safe() if state is None else state
        mode = str(current.get("game_mode", ""))
        if mode != "survival":
            raise SafetyInterlockError(
                f"{self.bot_name} must remain in Survival, found {mode!r}"
            )

    def position(self) -> tuple[float, float, float]:
        state = self.require_safe()
        position = state.get("position") or state.get("block_position") or {}
        try:
            return (
                float(position["x"]),
                float(position["y"]),
                float(position["z"]),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise WorkerSafetyError("Bridge position is unavailable") from error
