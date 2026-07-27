"""Readiness-condition model used by the progression state manager."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class PhaseCondition:
    """One weighted condition contributing to phase readiness."""

    name: str
    description: str
    weight: float = 1.0
    is_optional: bool = False

    def evaluate(self, context: Any) -> float:
        """Return no credit until a concrete evaluator is registered."""
        return 0.0


__all__ = ["PhaseCondition"]
