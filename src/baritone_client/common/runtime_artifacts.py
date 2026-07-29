"""Paths and writers for local runtime evidence that must not dirty Git."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any


def runtime_artifact_path(name: str, state: Any = None) -> Path:
    """Return a bot-local artifact path outside tracked source files."""
    checkpoint_dir = getattr(state, "checkpoint_dir", None)
    root = Path(
        checkpoint_dir
        or os.environ.get("MC_RUN_DIR")
        or (Path.cwd() / "Logs")
    )
    path = root / "monitor" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def append_world_map_entry(
    label: str,
    position: tuple[int, int, int],
    *,
    state: Any = None,
) -> bool:
    """Append one unique landmark to the bot-local world-map evidence."""
    path = runtime_artifact_path("world_map.md", state)
    entry = f"({position[0]}, {position[1]}, {position[2]})"
    try:
        content = path.read_text(encoding="utf-8") if path.exists() else ""
        if entry in content:
            return False
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"\n- **{label}**: {entry}")
        return True
    except OSError:
        return False
