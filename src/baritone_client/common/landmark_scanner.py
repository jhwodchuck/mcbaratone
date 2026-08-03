"""Bounded passive landmark discovery backed by the shared fleet catalog."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Mapping

from .storage_catalog import catalog_for

logger = logging.getLogger(__name__)


LANDMARK_BLOCKS = {
    "minecraft:nether_portal": "nether_portal",
    "minecraft:end_portal": "end_portal",
    "minecraft:end_portal_frame": "end_portal",
    "minecraft:bell": "village",
    "minecraft:chest": "chest",
    "minecraft:trapped_chest": "chest",
    "minecraft:barrel": "chest",
    "minecraft:spawner": "spawner",
    "minecraft:trial_spawner": "trial_chamber",
    "minecraft:vault": "trial_chamber",
    "minecraft:beacon": "beacon",
    "minecraft:lodestone": "lodestone",
}


def _unwrap(payload: Any) -> Mapping[str, Any]:
    if not isinstance(payload, Mapping):
        return {}
    nested = payload.get("data")
    return nested if isinstance(nested, Mapping) else payload


def _dimension(state: Mapping[str, Any]) -> str:
    return str(state.get("dimension") or "minecraft:overworld")


def _bot_name(state_manager: Any) -> str | None:
    run_dir = getattr(state_manager, "checkpoint_dir", None)
    if run_dir:
        path = Path(run_dir)
        bot_dir = path.parent if path.name == "controller" else path
        if bot_dir.name.lower().startswith("bot"):
            return bot_dir.name
    return os.environ.get("MC_BOT_NAME")


def _sync_location(state_manager: Any, category: str, row: Mapping[str, Any]) -> None:
    if state_manager is None:
        return
    state_manager.add_location(
        category,
        int(row["x"]),
        int(row["y"]),
        int(row["z"]),
        dimension=str(row["dimension"]),
        tags=["shared", "observed"],
    )


def import_shared_landmarks(client: Any, state_manager: Any) -> int:
    """Make fleet discoveries available through the bot's normal locations API."""
    catalog = catalog_for(client, state_manager)
    imported = 0
    for row in catalog.list_landmarks():
        before = len(
            state_manager.get_locations(str(row["category"])).get(
                str(row["category"]), []
            )
        )
        _sync_location(state_manager, str(row["category"]), row)
        after = len(
            state_manager.get_locations(str(row["category"])).get(
                str(row["category"]), []
            )
        )
        imported += int(after > before)
    return imported


def scan_visible_landmarks(
    client: Any,
    state_manager: Any,
    *,
    radius: int = 32,
    limit: int = 256,
) -> int:
    """Scan loaded surroundings once and publish observations fleet-wide."""
    state = _unwrap(client.transport.dispatch("get_state", {}))
    dimension = _dimension(state)
    response = _unwrap(
        client.transport.dispatch(
            "find_blocks",
            {
                "blocks": sorted(LANDMARK_BLOCKS),
                "radius": max(1, min(128, int(radius))),
                "limit": max(1, int(limit)),
            },
        )
    )
    found = response.get("found", response.get("blocks", []))
    if not isinstance(found, list):
        found = []

    catalog = catalog_for(client, state_manager)
    discovered_by = _bot_name(state_manager)
    recorded = 0
    clustered: dict[str, list[tuple[int, int, int]]] = {
        "nether_portal": [],
        "end_portal": [],
    }
    for block in found:
        if not isinstance(block, Mapping):
            continue
        block_id = str(block.get("block") or block.get("id") or "")
        category = LANDMARK_BLOCKS.get(block_id)
        if category is None:
            continue
        position = (
            int(block.get("x", 0)),
            int(block.get("y", 64)),
            int(block.get("z", 0)),
        )
        # A portal is made of several matching blocks but is one landmark.
        # Keep the first active block as its usable navigation coordinate.
        if category in clustered and any(
            max(abs(position[index] - existing[index]) for index in range(3)) <= 4
            for existing in clustered[category]
        ):
            continue
        if category in clustered:
            clustered[category].append(position)
        catalog.register_landmark(
            category,
            position,
            dimension=dimension,
            block_id=block_id,
            discovered_by=discovered_by,
            metadata={"source": "periodic_visible_scan"},
        )
        row = {
            "category": category,
            "dimension": dimension,
            "x": position[0],
            "y": position[1],
            "z": position[2],
        }
        _sync_location(state_manager, category, row)
        if category == "chest":
            catalog.register_container(
                position,
                dimension=dimension,
                container_type=block_id,
                status="known",
                metadata={"source": "periodic_visible_scan"},
            )
        recorded += 1

    # A bot also learns discoveries made by fleet peers on every scan pass.
    import_shared_landmarks(client, state_manager)
    return recorded
