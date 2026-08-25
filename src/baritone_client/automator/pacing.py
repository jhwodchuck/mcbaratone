"""Low-impact waits used between incremental automation passes."""

from __future__ import annotations

import time
from collections.abc import Callable

from ..common.tasks import PlayerDeathDetected


def _defensive_keepalive(client) -> None:
    """Run the shared survival/defense reflex during an intentional pause."""
    from ..common.combat import defend_or_flee

    defend_or_flee(client)


def _bridge_only_keepalive(client) -> None:
    """Keep the bridge warm without a combat reflex a corpse cannot run."""
    client.transport.dispatch("get_state", {})


def _player_is_already_dead(client) -> bool:
    """Report an existing corpse, treating an unreadable bridge as alive."""
    try:
        state = client.transport.dispatch("get_state", {})
    except Exception:
        return False
    if not isinstance(state, dict):
        return False
    if state.get("is_dead", False):
        return True
    health = state.get("health", 20)
    try:
        return health is not None and float(health) <= 0
    except (TypeError, ValueError):
        return False


def wait_with_bridge_keepalive(
    client,
    *,
    duration: float,
    keepalive_interval: float = 10.0,
    safety_interval: float = 2.0,
    safety_check: Callable = _defensive_keepalive,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> bool:
    """Wait calmly while keeping both the bridge and player actively safe.

    The bridge session is controller-local, so a separate supervisor heartbeat
    does not keep it alive. The same pause must remain combat-aware: live Bot15
    was killed twice by a zombie while this loop slept with no active process.
    """
    remaining_duration = max(0.0, float(duration))
    interval = max(
        0.1,
        min(float(keepalive_interval), float(safety_interval)),
    )
    deadline = monotonic() + remaining_duration
    all_probes_succeeded = True

    # Callers reach this wait *after* yielding to death recovery, so handing an
    # existing corpse to the defensive reflex raises PlayerDeathDetected back
    # out of the very pause that is meant to precede the respawn.  Neither
    # caller site wraps it -- automator.run() and phase_executor's retry delay
    # both let it escape to main() -- so the controller dies instead of
    # recovering, and the supervisor restart loop re-runs the same crash.  Both
    # dragon labs sat dead for a day on this.  Dying *during* the wait is a
    # different event and still propagates below.
    probe = _bridge_only_keepalive if _player_is_already_dead(client) else safety_check

    while remaining_duration > 0:
        try:
            probe(client)
        except PlayerDeathDetected:
            raise
        except Exception:
            all_probes_succeeded = False
            try:
                client.transport.dispatch("get_state", {})
            except Exception:
                pass
        remaining_duration = max(0.0, deadline - monotonic())
        if remaining_duration <= 0:
            break
        sleep(min(interval, remaining_duration))
        remaining_duration = max(0.0, deadline - monotonic())

    return all_probes_succeeded
