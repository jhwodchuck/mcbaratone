"""Industrial-camp adapter for the shared survival provisioner."""

from __future__ import annotations

from typing import Any, Sequence

from .survival_provisioning import provision_survival_kit


Position = tuple[int, int, int]


def provision_camp_armor(
    client: Any,
    *,
    storage_sources: Sequence[Position],
    furnace: Position,
    crafting_table: Position,
    target_pieces: int,
    minimum_torches: int,
) -> tuple[bool, str]:
    """Make a camp worker's safety kit from manifest-supplied landmarks."""
    return provision_survival_kit(
        client,
        storage_sources=storage_sources,
        furnace=furnace,
        crafting_table=crafting_table,
        target_pieces=target_pieces,
        minimum_torches=minimum_torches,
    )


__all__ = ["provision_camp_armor"]
