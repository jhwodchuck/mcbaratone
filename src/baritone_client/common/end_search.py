"""Bounded, centered block-search payloads shared by End controllers."""

from __future__ import annotations

from collections.abc import Mapping


def exit_portal_search_payload() -> dict:
    """Search the loaded central fountain instead of a 129-cubed player cube."""
    return {
        "blocks": ["minecraft:end_portal"],
        "center": {"x": 0, "y": 64, "z": 0},
        "radius": 16,
        "limit": 1,
    }


def crystal_cage_search_payload(position: Mapping) -> dict:
    """Search only the three-block shell around one observed End crystal."""
    center = {
        axis: int(round(float(position[axis])))
        for axis in ("x", "y", "z")
    }
    return {
        "blocks": ["minecraft:iron_bars"],
        "center": center,
        "radius": 3,
        "limit": 64,
    }


__all__ = ["crystal_cage_search_payload", "exit_portal_search_payload"]
