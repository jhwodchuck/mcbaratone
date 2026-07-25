"""Small target-classification helpers shared by combat policies."""

from collections.abc import Iterable


def matches_requested_mob(entity_type: str, mob_types: Iterable[str]) -> bool:
    """Return whether an entity type matches one of the requested hunt types."""
    normalized = str(entity_type).lower()
    return any(str(mob_type).lower() in normalized for mob_type in mob_types)
