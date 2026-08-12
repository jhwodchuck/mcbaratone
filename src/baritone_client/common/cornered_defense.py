"""Last-resort combat when terrain offers no usable escape endpoint."""

from __future__ import annotations

from typing import Any, Callable

from .defense import AttackStyle, ThreatAssessment, is_projectile_threat


def engageable(threat: ThreatAssessment) -> bool:
    """Return whether last-resort melee is meaningful for this entity."""
    return (
        threat.style not in (AttackStyle.EXPLOSIVE, AttackStyle.BOSS)
        and not is_projectile_threat(threat.entity_type)
    )


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
        or not engageable(primary)
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


def handle_non_engageable_escape_failure(
    client: Any,
    primary: ThreatAssessment,
    assessments: list[ThreatAssessment],
    runtime: Any,
    *,
    relocate: Callable[[Any, dict], bool],
    fight: Callable[..., bool],
) -> bool:
    """Relocate from projectiles/explosives/bosses; fight only other mobs."""
    if engageable(primary):
        return False
    threat_id = primary.entity.get("id")
    print(
        f"DEFENSE: evasion failed {runtime.evade_failures}x against "
        f"{primary.entity.get('type')}; relocating (never melee "
        f"{primary.style.value} threats)"
    )
    relocated = relocate(client, primary.entity)
    runtime.record_evade_result(threat_id, relocated)
    if relocated:
        runtime.hold_recovery(8.0)
        return True
    alternative = next(
        (
            item
            for item in assessments
            if item.entity.get("id") != threat_id
            and engageable(item)
            and item.distance <= 6.5
        ),
        None,
    )
    if alternative is not None and fight(
        client,
        alternative.entity,
        no_retreat=True,
        abort_on_other_hostiles=False,
    ):
        runtime.record_evade_result(alternative.entity.get("id"), True)
        runtime.hold_recovery(6.0)
    return True
