"""Reusable fleet provisioning for repeatable family expeditions."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from ...common.combat import eat_until_hunger
from ..survival_provisioning import provision_food_reserve


Position = tuple[int, int, int]


def provision_expedition_food(
    bot_clients: Mapping[str, Any],
    bot_names: Sequence[str],
    *,
    storage_sources: Sequence[Position],
    crafting_table: Position | None,
    minimum_reserve: int,
    required_food: int,
) -> dict[str, tuple[bool, str]]:
    """Feed and refill every live Survival bot before an expedition."""
    outcomes: dict[str, tuple[bool, str]] = {}
    for name in bot_names:
        client = bot_clients[name]
        try:
            state = client.transport.dispatch("get_state", {})
            state = state.get("data", state)
            if bool(state.get("is_dead")) or float(state.get("health", 0) or 0) <= 0:
                outcomes[name] = (False, "bot is dead")
                continue
            if str(state.get("game_mode", "")).casefold() != "survival":
                outcomes[name] = (False, "bot is not in Survival")
                continue
            ready, detail = provision_food_reserve(
                client,
                storage_sources=storage_sources,
                minimum_reserve=minimum_reserve,
                crafting_table=crafting_table,
            )
            if not ready:
                outcomes[name] = (False, detail)
                continue
            eat_until_hunger(client, minimum_food=required_food)
            outcomes[name] = provision_food_reserve(
                client,
                storage_sources=storage_sources,
                minimum_reserve=minimum_reserve,
                crafting_table=crafting_table,
            )
        except Exception as error:
            outcomes[name] = (False, f"{type(error).__name__}: {error}")
    return outcomes


__all__ = ["provision_expedition_food"]
