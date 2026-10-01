"""Keep an occluded alert from forcing departure from observed shelter."""

import math

from .defense import AttackStyle, DefenseDecision, DefenseMode
from .tasks import PlayerDeathDetected, SurvivalRecoveryRequired


def hold_sheltered_alert(client, decision, state, assessments=None):
    """Keep distant occluded mobs outside observed shelter from forcing exit."""
    threat = decision.primary
    if (decision.mode is not DefenseMode.EVADE or threat is None
            or (assessments is not None and len(assessments) != 1)
            or decision.reason not in (
                "threat stalled in alert range too long; forcing evasion", "avoid ranged threat",
            )
            or threat.style not in (AttackStyle.MELEE, AttackStyle.RANGED)
            or (threat.always_evade and threat.entity_type not in ("skeleton", "stray", "bogged"))
            or threat.entity_type == "vex"
            or threat.entity.get("can_see_player") is not False):
        return decision
    try:
        health = float(state.get("health", 0))
        if (not math.isfinite(threat.distance) or not 10 < threat.distance < 999
                or not math.isfinite(threat.closing_speed)
                or state.get("is_dead") is not False or state.get("on_fire") is not False
                or not math.isfinite(health) or health < 12):
            return decision
        for vector in (threat.entity.get("position"), threat.entity.get("velocity"),
                       state.get("position"), state.get("velocity")):
            if not isinstance(vector, dict) or not all(
                    math.isfinite(float(vector[axis])) for axis in ("x", "y", "z")):
                return decision
        from .survival_farm import _enclosure_bounds
        bounds = _enclosure_bounds(client, state, strict=True)
        if not bounds:
            return decision
        west, east, y, north, south = bounds
        position = threat.entity["position"]
        tx, ty, tz = (float(position[axis]) for axis in ("x", "y", "z"))
        if not (tx < west or tx >= east + 1 or tz < north or tz >= south + 1
                or ty <= y - 2 or ty >= y + 5):
            return decision  # Inside/on the enclosure boundary is not protected.
        return DefenseDecision(
            DefenseMode.ALERT, "occluded distant threat outside verified shelter; holding", threat,
        )
    except (PlayerDeathDetected, SurvivalRecoveryRequired):
        raise
    except Exception:
        return decision
