"""Shared survival guards for long construction operations."""


class BuildSurvivalHold(Exception):
    """Stop a placement batch before hunger or health becomes unsafe."""


def has_build_survival_margin(
    client,
    *,
    minimum_health: float = 12.0,
    minimum_food: int = 12,
) -> bool:
    """Return whether exposed construction may safely continue."""
    state = client.transport.dispatch("get_state", {})
    nested = state.get("data")
    if isinstance(nested, dict):
        state = {**state, **nested}
    health = float(state.get("health", 20) or 0)
    food = int(state.get("food_level", state.get("food", 20)) or 0)
    return (
        not state.get("is_dead", False)
        and health >= minimum_health
        and food >= minimum_food
    )
