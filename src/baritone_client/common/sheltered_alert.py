"""Keep an occluded alert from forcing departure from observed shelter."""

import math

from .defense import AttackStyle, DefenseDecision, DefenseMode
from .tasks import PlayerDeathDetected, SurvivalRecoveryRequired


def hold_sheltered_alert(client, decision, state, assessments=None):
    """Override only distant alert-dwell evasion, not actionable threats."""
    threat = decision.primary
    if (decision.mode is not DefenseMode.EVADE or threat is None
            or (assessments is not None and len(assessments) != 1)
            or decision.reason != "threat stalled in alert range too long; forcing evasion"
            or threat.style not in (AttackStyle.MELEE, AttackStyle.RANGED)
            or threat.always_evade or threat.entity_type == "vex"
            or threat.entity.get("can_see_player") is not False):
        return decision
    try:
        health = float(state.get("health", 0))
        if (not math.isfinite(threat.distance) or not 10 < threat.distance < 999
                or not math.isfinite(threat.closing_speed) or threat.closing_speed > .05
                or state.get("is_dead") is not False or state.get("on_fire") is not False
                or not math.isfinite(health) or health < 12):
            return decision
        for vector in (threat.entity.get("position"), threat.entity.get("velocity"),
                       state.get("position"), state.get("velocity")):
            if not isinstance(vector, dict) or not all(
                    math.isfinite(float(vector[axis])) for axis in ("x", "y", "z")):
                return decision
        from .survival_farm import _enclosure_bounds
        if not _enclosure_bounds(client, state, strict=True):
            return decision
        return DefenseDecision(
            DefenseMode.ALERT, "occluded distant threat outside verified shelter; holding", threat,
        )
    except (PlayerDeathDetected, SurvivalRecoveryRequired):
        raise
    except Exception:
        return decision
