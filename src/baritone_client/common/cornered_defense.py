"""Last-resort combat when terrain offers no usable escape endpoint."""

from __future__ import annotations

from typing import Any, Callable

from .defense import AttackStyle, ThreatAssessment


def fight_if_no_escape(
    client: Any,
    primary: ThreatAssessment,
    runtime: Any,
    *,
    equip_weapon: Callable[[Any], bool],
    fight: Callable[..., bool],
) -> bool:
    """Fight a non-explosive attacker after escape planning proves impossible.

    A crowded underground storage route produced no terrain-safe endpoint and
    spent long enough planning for four mobs to reduce a fully armored Bot16
    from 20 health to 10.6. Repeating that route cannot help. The explosive
    policy remains absolute: this helper never selects a creeper or boss.
    """
    if (
        getattr(client, "_last_escape_failure_reason", None)
        != "no_safe_endpoint"
        or primary.style in (AttackStyle.EXPLOSIVE, AttackStyle.BOSS)
        or not equip_weapon(client)
    ):
        return False

    print(
        "DEFENSE: no escape endpoint; fighting the immediate "
        f"{primary.entity.get('type')} before more health is lost"
    )
    defeated = fight(
        client,
        primary.entity,
        no_retreat=True,
        abort_on_other_hostiles=False,
        max_duration=12,
    )
    if defeated:
        runtime.record_evade_result(primary.entity.get("id"), True)
        runtime.hold_recovery(6.0)
    return True
