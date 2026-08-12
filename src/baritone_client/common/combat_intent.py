"""Explicit authorization for supervised, intentional combat.

Ordinary navigation treats every active hostile as something to defend
against.  During an intentional engagement the chosen target is different:
movement may approach it, while every *other* threat must remain visible to
the normal defense policy.  This module keeps that narrow exception scoped to
one client and one context manager.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Iterable, Iterator

from .combat_targeting import normalize_mob_type

_INTENT_STACK: ContextVar[tuple[tuple[Any, "CombatIntent"], ...]] = ContextVar(
    "mcbaratone_combat_intent_stack", default=()
)


@dataclass(frozen=True)
class CombatIntent:
    """Targets authorized for one deliberate combat operation."""

    purpose: str
    target_ids: frozenset[int] = field(default_factory=frozenset)
    target_types: frozenset[str] = field(default_factory=frozenset)
    allow_boss: bool = False
    authorize_matching_types: bool = False

    @classmethod
    def for_target(
        cls,
        target_id: Any,
        *,
        purpose: str,
        target_type: object = "",
        allow_boss: bool = False,
    ) -> "CombatIntent":
        """Build an ID-first intent from one observed entity."""
        ids = frozenset({int(target_id)}) if target_id is not None else frozenset()
        normalized = normalize_mob_type(target_type)
        types = frozenset({normalized}) if normalized else frozenset()
        return cls(
            purpose=str(purpose),
            target_ids=ids,
            target_types=types,
            allow_boss=bool(allow_boss),
        )

    def authorizes(self, entity: dict) -> bool:
        """Return True only for an entity explicitly covered by this intent."""
        entity_id = entity.get("id")
        if entity_id is not None:
            try:
                if int(entity_id) in self.target_ids:
                    return True
            except (TypeError, ValueError):
                pass
        if self.target_ids and not self.authorize_matching_types:
            return False
        entity_type = normalize_mob_type(entity.get("type"))
        return bool(entity_type and entity_type in self.target_types)


def current_combat_intent(client: Any) -> CombatIntent | None:
    """Return the innermost active intent for a client, if any."""
    return next(
        (intent for owner, intent in reversed(_INTENT_STACK.get()) if owner is client),
        None,
    )


@contextmanager
def combat_intent(client: Any, intent: CombatIntent) -> Iterator[CombatIntent]:
    """Temporarily authorize a target without suppressing other threats."""
    token = _INTENT_STACK.set(_INTENT_STACK.get() + ((client, intent),))
    try:
        yield intent
    finally:
        _INTENT_STACK.reset(token)


def exclude_authorized_threats(client: Any, assessments: Iterable[Any]) -> list[Any]:
    """Remove only the current intended target from defense assessments."""
    intent = current_combat_intent(client)
    values = list(assessments)
    if intent is None:
        return values

    def authorized(item: Any) -> bool:
        style = getattr(getattr(item, "style", None), "value", None)
        if style == "boss" and not intent.allow_boss:
            return False
        return intent.authorizes(item.entity)

    return [item for item in values if not authorized(item)]


__all__ = [
    "CombatIntent",
    "combat_intent",
    "current_combat_intent",
    "exclude_authorized_threats",
]
