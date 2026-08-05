"""Safety checks for live functional-test world selection."""

from __future__ import annotations

from typing import Iterable, Mapping


READ_ONLY_SUITE = "Suite_1200_Survival_Progression"
KNOWN_FLEET_SERVERS = {"loopback:25565"}


def normalize_server_address(address: str) -> str:
    """Normalize local aliases while retaining the selected server port."""
    host, separator, port = str(address or "").strip().lower().rpartition(":")
    if not separator or not port.isdigit():
        return str(address or "").strip().lower()
    if host in {"localhost", "127.0.0.1", "::1", "[::1]"}:
        host = "loopback"
    return f"{host}:{int(port)}"


def selection_requires_disposable_world(suite_names: Iterable[str]) -> bool:
    """Return whether any selected suite may mutate Minecraft state."""
    names = set(suite_names)
    return bool(names) and names != {READ_ONLY_SUITE}


def validate_disposable_world(
    expected_server: str,
    state: Mapping[str, object],
) -> str:
    """Verify that a bridge belongs to the explicitly selected test server."""
    identity = state.get("world_identity")
    if not isinstance(identity, Mapping):
        raise ValueError("bridge state has no world_identity")
    actual_raw = str(identity.get("server_address") or "")
    expected = normalize_server_address(expected_server)
    actual = normalize_server_address(actual_raw)
    if not expected:
        raise ValueError("--expect-server is required for mutating functional tests")
    if actual != expected:
        raise ValueError(
            f"bridge is connected to {actual_raw or 'an unknown server'}, "
            f"not the expected {expected_server}"
        )
    if actual in KNOWN_FLEET_SERVERS:
        raise ValueError(
            "refusing to run mutating functional tests against the live fleet server"
        )
    return actual
