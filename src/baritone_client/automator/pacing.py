"""Low-impact waits used between incremental automation passes."""

from __future__ import annotations

import time
from collections.abc import Callable


def wait_with_bridge_keepalive(
    client,
    *,
    duration: float,
    keepalive_interval: float = 10.0,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> bool:
    """Wait at a calm pace without letting the controller connection go idle.

    The bridge session is controller-local, so a separate supervisor heartbeat
    does not keep it alive. Read-only state probes preserve that session while
    the bot intentionally pauses between incremental improvements.
    """
    remaining_duration = max(0.0, float(duration))
    interval = max(0.1, float(keepalive_interval))
    deadline = monotonic() + remaining_duration
    all_probes_succeeded = True

    while remaining_duration > 0:
        sleep(min(interval, remaining_duration))
        try:
            client.transport.dispatch("get_state", {})
        except Exception:
            all_probes_succeeded = False
        remaining_duration = max(0.0, deadline - monotonic())

    return all_probes_succeeded
