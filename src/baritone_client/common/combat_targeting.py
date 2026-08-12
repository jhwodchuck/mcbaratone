"""Small target-classification helpers shared by combat policies."""

from collections.abc import Iterable


def normalize_mob_type(entity_type: object) -> str:
    """Return the exact lower-case registry path for an entity type."""
    return str(entity_type or "").strip().lower().split(":", 1)[-1]


def matches_requested_mob(entity_type: str, mob_types: Iterable[str]) -> bool:
    """Return whether an entity exactly matches one requested registry type.

    Substring matching is unsafe for combat selection: a request for ``pig``
    used to match ``piglin`` and could turn a food hunt into an armed fight.
    Both namespaced and path-only IDs remain accepted for callers.
    """
    normalized = normalize_mob_type(entity_type)
    requested = {normalize_mob_type(mob_type) for mob_type in mob_types}
    requested.discard("")
    return normalized in requested


__all__ = ["matches_requested_mob", "normalize_mob_type"]
