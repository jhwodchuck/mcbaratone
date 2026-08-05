import pytest

from tests.functional.world_safety import (
    READ_ONLY_SUITE,
    normalize_server_address,
    selection_requires_disposable_world,
    validate_disposable_world,
)


def test_local_server_aliases_normalize_to_same_identity():
    assert normalize_server_address("localhost:25580") == "loopback:25580"
    assert normalize_server_address("127.0.0.1:25580") == "loopback:25580"


def test_only_survival_acceptance_suite_is_read_only():
    assert not selection_requires_disposable_world([READ_ONLY_SUITE])
    assert selection_requires_disposable_world(["Suite_600_Combat"])
    assert selection_requires_disposable_world(
        [READ_ONLY_SUITE, "Suite_600_Combat"]
    )


def test_world_guard_accepts_explicit_disposable_target():
    state = {"world_identity": {"server_address": "127.0.0.1:25580"}}
    assert (
        validate_disposable_world("localhost:25580", state) == "loopback:25580"
    )


def test_world_guard_rejects_live_fleet_even_when_explicitly_named():
    state = {"world_identity": {"server_address": "localhost:25565"}}
    with pytest.raises(ValueError, match="live fleet"):
        validate_disposable_world("localhost:25565", state)


def test_world_guard_rejects_wrong_bridge_target():
    state = {"world_identity": {"server_address": "localhost:25565"}}
    with pytest.raises(ValueError, match="not the expected"):
        validate_disposable_world("localhost:25580", state)
