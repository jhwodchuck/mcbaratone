"""Safety and resilience contracts for bounded Survival workers."""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path
from typing import Any

import pytest

from baritone_client.core.exceptions import TransportError
from baritone_client.operations import shared_storage_ingress, worker_runner
from baritone_client.operations.bridge_guard import (
    NonOpClientGuard,
    SafetyInterlockError,
    WorkerSafetyError,
)
from baritone_client.operations.resilient_read_transport import (
    BridgeResponseTimeout,
    ResilientReadTransport,
)

REPO = Path(__file__).resolve().parents[1]
WORKER_PATHS = tuple(
    REPO / "scripts" / "operations" / name
    for name in ("bounded_farmer.py", "bounded_forester.py", "bounded_miner.py")
)
sys.path.insert(0, str(REPO / "scripts" / "operations"))


class _StateTransport:
    def __init__(self, state: dict[str, Any]) -> None:
        self.state = state

    def dispatch(
        self,
        route: str,
        payload: dict[str, Any],
        timeout: float | None = None,
    ) -> dict[str, Any]:
        assert route == "get_state"
        assert payload == {}
        assert timeout is None
        return dict(self.state)


def _safe_state(**updates: Any) -> dict[str, Any]:
    state = {
        "automation_profile": "non_op_client",
        "server_authority": False,
        "player_name": "Bot17",
        "online_players": ["Bot17", "Bot18"],
        "game_mode": "survival",
        "is_dead": False,
        "health": 20.0,
        "position": {"x": 1, "y": 64, "z": 2},
    }
    state.update(updates)
    return state


def _guard(state: dict[str, Any], protected: tuple[str, ...] = ()) -> NonOpClientGuard:
    client = type("Client", (), {"transport": _StateTransport(state)})()
    return NonOpClientGuard(client, "Bot17", protected)


def test_non_op_guard_accepts_only_the_exact_attested_identity() -> None:
    assert _guard(_safe_state()).require_safe()["player_name"] == "Bot17"

    with pytest.raises(SafetyInterlockError, match="identity mismatch"):
        _guard(
            _safe_state(player_name="bot17", online_players=["bot17"])
        ).require_safe()


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"automation_profile": "op_client"}, "non_op_client"),
        ({"server_authority": True}, "non_op_client"),
        ({"game_mode": "creative"}, "Survival"),
        ({"game_mode": ""}, "Survival"),
        ({"online_players": None}, "online-player"),
        ({"online_players": ["Bot18"]}, "online roster"),
        ({"health": 0.0}, "no health"),
        ({"health": "unknown"}, "health attestation"),
    ],
)
def test_non_op_guard_fails_closed_on_incomplete_or_unsafe_attestation(
    updates: dict[str, Any],
    message: str,
) -> None:
    with pytest.raises(WorkerSafetyError, match=message):
        _guard(_safe_state(**updates)).require_safe()


def test_non_op_guard_stops_when_a_protected_player_is_online() -> None:
    with pytest.raises(SafetyInterlockError, match="protectedplayer"):
        _guard(
            _safe_state(online_players=["Bot17", "ProtectedPlayer"]),
            ("protectedplayer",),
        ).require_safe()


def test_position_read_revalidates_the_full_safety_attestation() -> None:
    with pytest.raises(SafetyInterlockError, match="Survival"):
        _guard(_safe_state(game_mode="creative")).position()


def test_direct_survival_requirement_revalidates_identity_and_profile() -> None:
    with pytest.raises(WorkerSafetyError, match="non_op_client"):
        _guard(_safe_state(automation_profile="op_client")).require_survival()


class _ScriptedTransport:
    def __init__(self, outcomes: list[Any]) -> None:
        self.outcomes = iter(outcomes)
        self.calls: list[tuple[str, dict[str, Any], float | None]] = []

    def dispatch(
        self,
        route: str,
        payload: dict[str, Any],
        timeout: float | None = None,
    ) -> Any:
        self.calls.append((route, payload, timeout))
        outcome = next(self.outcomes)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def _timeout() -> BridgeResponseTimeout:
    return BridgeResponseTimeout("get_state", transport_type="test", request_sent=True)


def test_typed_read_timeouts_are_retried_with_a_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "baritone_client.operations.resilient_read_transport.time.sleep",
        lambda _delay: None,
    )
    delegate = _ScriptedTransport([_timeout(), _timeout(), {"health": 20.0}])
    transport = ResilientReadTransport(delegate, attempts=3)

    assert transport.dispatch("get_state", {}, timeout=2.0) == {"health": 20.0}
    assert delegate.calls == [("get_state", {}, 2.0)] * 3


def test_read_timeout_budget_is_not_unbounded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "baritone_client.operations.resilient_read_transport.time.sleep",
        lambda _delay: None,
    )
    delegate = _ScriptedTransport([_timeout(), _timeout(), _timeout()])

    with pytest.raises(BridgeResponseTimeout):
        ResilientReadTransport(delegate, attempts=3).dispatch("get_block", {})

    assert len(delegate.calls) == 3


def test_non_timeout_transport_failures_are_not_retried() -> None:
    delegate = _ScriptedTransport([TransportError("connection reset"), {"ok": True}])

    with pytest.raises(TransportError, match="connection reset"):
        ResilientReadTransport(delegate, attempts=5).dispatch("get_state", {})

    assert len(delegate.calls) == 1


def test_mutations_are_dispatched_exactly_once_even_on_timeout() -> None:
    delegate = _ScriptedTransport([_timeout(), {"should": "not run"}])

    with pytest.raises(BridgeResponseTimeout):
        ResilientReadTransport(delegate, attempts=5).dispatch(
            "break_block", {"x": 1, "y": 64, "z": 2}
        )

    assert len(delegate.calls) == 1


class _LockHandle:
    def seek(self, _offset: int) -> None:
        return None

    def close(self) -> None:
        return None


def test_storage_mutation_exception_is_never_replayed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[int, int, int]] = []

    def fail_once(_client: Any, position: tuple[int, int, int], **_kwargs: Any) -> int:
        calls.append(position)
        raise BridgeResponseTimeout(
            "quick_move", transport_type="test", request_sent=True
        )

    monkeypatch.setattr(
        shared_storage_ingress, "_acquire", lambda _timeout: _LockHandle()
    )
    monkeypatch.setattr(shared_storage_ingress, "_unlock", lambda _handle: None)
    monkeypatch.setattr(
        shared_storage_ingress,
        "_deposit_positions",
        lambda _primary: [(1, 2, 3), (4, 5, 6)],
    )
    monkeypatch.setattr(shared_storage_ingress, "deposit_excess_to_chest", fail_once)

    assert shared_storage_ingress.serialized_deposit(object(), (1, 2, 3)) == -1
    assert calls == [(1, 2, 3)]


def test_storage_rejects_a_mutation_retry_budget() -> None:
    with pytest.raises(ValueError, match="exactly once"):
        shared_storage_ingress.serialized_deposit(object(), (1, 2, 3), attempts=2)


def test_storage_stops_after_the_first_successful_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[int, int, int]] = []

    def move_once(_client: Any, position: tuple[int, int, int], **_kwargs: Any) -> int:
        calls.append(position)
        return 2

    monkeypatch.setattr(
        shared_storage_ingress, "_acquire", lambda _timeout: _LockHandle()
    )
    monkeypatch.setattr(shared_storage_ingress, "_unlock", lambda _handle: None)
    monkeypatch.setattr(
        shared_storage_ingress,
        "_deposit_positions",
        lambda _primary: [(1, 2, 3), (4, 5, 6)],
    )
    monkeypatch.setattr(shared_storage_ingress, "deposit_excess_to_chest", move_once)

    assert shared_storage_ingress.serialized_deposit(object(), (1, 2, 3)) == 2
    assert calls == [(1, 2, 3)]


def test_worker_restart_budget_resets_after_productive_work() -> None:
    assert (
        worker_runner.next_restart_count(
            worker_runner.MAX_WORKER_RESTARTS,
            worker_runner.PRODUCTIVE_RUN_SECONDS,
        )
        == 0
    )


def test_worker_restart_budget_rejects_a_real_crash_loop() -> None:
    restarts = 0
    for _ in range(worker_runner.MAX_WORKER_RESTARTS):
        restarts = worker_runner.next_restart_count(restarts, ran_for=1.0)

    with pytest.raises(worker_runner.WorkerRunnerError, match="rapid restarts"):
        worker_runner.next_restart_count(restarts, ran_for=1.0)


@pytest.fixture
def detail():
    import bounded_forester

    return bounded_forester._cycle_detail


def test_forester_never_reports_completion_without_a_felled_tree(detail) -> None:
    for seen, harvested, skipped in ((4, 0, 4), (4, 0, 0), (1, 0, 1), (0, 0, 0)):
        message = detail(
            trees_seen=seen,
            harvested_now=harvested,
            skipped_no_sapling=skipped,
            saplings_on_hand=0,
            restocked=0,
            source="world",
        )
        assert not re.search(r"\bcompleted\b", message), (seen, harvested, message)


def test_forester_reports_real_work_and_resource_blockers(detail) -> None:
    completed = detail(
        trees_seen=4,
        harvested_now=3,
        skipped_no_sapling=1,
        saplings_on_hand=12,
        restocked=16,
        source="world",
    )
    blocked = detail(
        trees_seen=4,
        harvested_now=0,
        skipped_no_sapling=4,
        saplings_on_hand=0,
        restocked=0,
        source="world",
    )

    assert "3/4 trees" in completed and "restocked 16" in completed
    assert "BLOCKED" in blocked and "0 saplings" in blocked


def _worker_tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def test_all_bounded_workers_use_only_non_operator_safety_wiring() -> None:
    offenders = []
    for path in WORKER_PATHS:
        tree = _worker_tree(path)
        imports = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imports.update(
            node.module or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        )
        if any(name.endswith((".commissioning", ".rcon")) for name in imports):
            offenders.append(f"{path.name}: privileged module import")

        names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        attributes = {
            node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
        }
        if "NonOpClientGuard" not in names:
            offenders.append(f"{path.name}: missing NonOpClientGuard")
        if "require_survival" not in names | attributes:
            offenders.append(f"{path.name}: missing require_survival")
        if "set_mode" in names | attributes:
            offenders.append(f"{path.name}: misleading set_mode")

    assert not offenders, offenders


def test_all_bounded_workers_have_no_privileged_command_literals_or_calls() -> None:
    command_pattern = re.compile(
        r"^\s*(?:gamemode|teleport|tp|spreadplayers|give|setblock|fill|clear|data\s+modify)\b",
        re.IGNORECASE,
    )
    forbidden_calls = {"command", "teleport"}
    offenders = []
    for path in WORKER_PATHS:
        tree = _worker_tree(path)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in forbidden_calls
            ):
                offenders.append(f"{path.name}:{node.lineno}: {node.func.attr} call")
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and command_pattern.search(node.value)
            ):
                offenders.append(
                    f"{path.name}:{node.lineno}: privileged command literal"
                )

    assert not offenders, offenders


def test_every_bounded_worker_enables_bounded_typed_read_retries() -> None:
    offenders = []
    for path in WORKER_PATHS:
        tree = _worker_tree(path)
        wrappers = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "ResilientReadTransport"
        ]
        if len(wrappers) != 1:
            offenders.append(f"{path.name}: expected one read wrapper")
            continue
        attempts = next(
            (
                keyword.value
                for keyword in wrappers[0].keywords
                if keyword.arg == "attempts"
            ),
            None,
        )
        if not isinstance(attempts, ast.Constant) or not 2 <= attempts.value <= 5:
            offenders.append(f"{path.name}: retry bound must be 2..5")

    assert not offenders, offenders


def test_miner_banks_existing_cargo_before_selecting_a_descent_route() -> None:
    source = (REPO / "scripts" / "operations" / "bounded_miner.py").read_text(
        encoding="utf-8"
    )
    preflight = source.index("banked, moved = preflight_bank_cargo()")
    descent = source.index("if not walk_route(descent):", preflight)

    assert preflight < descent


def test_forester_uses_sorted_storage_when_restocking_saplings() -> None:
    source = (REPO / "scripts" / "operations" / "bounded_forester.py").read_text(
        encoding="utf-8"
    )
    restock = source[source.index("def restock_saplings") :]
    restock = restock[: restock.index("def matching_sapling")]

    assert "self.sapling_sources" in restock
    assert "withdraw_required_from_chest" in restock
