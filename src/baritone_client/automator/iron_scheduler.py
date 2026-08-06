"""Small scheduler adapter for the dedicated iron-supply role."""

from __future__ import annotations

from typing import Any, MutableMapping

from ..common.iron_supply import run_iron_cycle


def iron_cycle_ready(signals: Any, *, boot_completed: bool) -> bool:
    """Require observed threat safety, but not daylight, for mining work."""
    return bool(
        boot_completed
        and signals.observed
        and signals.entities_observed
        and "overworld" in signals.dimension
        and signals.health >= 16.0
        and signals.food >= 14
        and signals.nearby_hostiles == 0
    )


def run_scheduled_iron_cycle(
    client: Any,
    state: Any,
    runtime: MutableMapping[str, Any],
) -> tuple[bool, str, int, int]:
    """Run one cycle and return scheduler-compatible durable progress."""
    before = int(runtime.get("iron_units_banked", 0) or 0)
    cycle = run_iron_cycle(client, state)
    after = max(before, int(cycle.total_iron_banked or 0))
    runtime["iron_units_banked"] = after
    return cycle.success, cycle.detail, before, after


__all__ = ["iron_cycle_ready", "run_scheduled_iron_cycle"]
