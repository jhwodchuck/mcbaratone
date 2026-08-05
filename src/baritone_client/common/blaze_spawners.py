"""Fleet sharing and fixed-position hunting for Nether blaze spawners."""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, List, Mapping, Tuple

from .combat import hunt_mobs
from .inventory import count_item, has_full_armor

logger = logging.getLogger(__name__)

Position = Tuple[int, int, int]
Travel = Callable[..., bool]


def _position(payload: Mapping[str, Any]) -> Position:
    nested = payload.get("data")
    if isinstance(nested, Mapping):
        payload = nested
    pos = payload.get("block_position", payload.get("position", payload))
    if not isinstance(pos, Mapping):
        pos = {}
    return (
        int(pos.get("x", 0)),
        int(pos.get("y", 64)),
        int(pos.get("z", 0)),
    )


def shared_blaze_spawners(client, state) -> List[Position]:
    """Return fleet-observed Nether spawners nearest to this bot first."""
    if state is None:
        return []
    try:
        from .storage_catalog import catalog_for

        catalog = catalog_for(client, state)
        rows = catalog.list_landmarks(
            "blaze_spawner", dimension="minecraft:the_nether"
        )
        # Passive scans know only the block identity. They remain useful
        # bootstrap candidates, while direct fortress sightings are promoted.
        rows += catalog.list_landmarks(
            "spawner", dimension="minecraft:the_nether"
        )
        here = _position(client.transport.dispatch("get_state", {}))
        positions = {
            (int(row["x"]), int(row["y"]), int(row["z"])) for row in rows
        }
        return sorted(
            positions,
            key=lambda pos: sum(
                (pos[index] - here[index]) ** 2 for index in range(3)
            ),
        )
    except Exception as exc:
        logger.debug("Shared blaze spawner lookup deferred: %s", exc)
        return []


def publish_blaze_spawner(client, state, position: Position) -> None:
    """Publish a directly observed fortress spawner to the fleet catalog."""
    if state is None:
        return
    try:
        from .storage_catalog import catalog_for

        catalog_for(client, state).register_landmark(
            "blaze_spawner",
            position,
            dimension="minecraft:the_nether",
            block_id="minecraft:spawner",
            metadata={"source": "blaze_hunt", "fortress_context": True},
        )
    except Exception as exc:
        logger.debug("Shared blaze spawner publish deferred: %s", exc)


def _observed_block(client, position: Position) -> str:
    payload = client.transport.dispatch(
        "get_block",
        {"x": position[0], "y": position[1], "z": position[2]},
    )
    if not isinstance(payload, Mapping):
        return ""
    nested = payload.get("data")
    if isinstance(nested, Mapping):
        payload = nested
    return str(payload.get("id") or payload.get("block") or payload.get("type") or "")


def camp_blaze_spawner(
    client,
    position: Position,
    *,
    target_count: int,
    deadline: float,
    travel_to: Travel,
) -> bool:
    """Stay inside activation range and hunt until the total rod target is met."""
    if not has_full_armor(client, minimum_material="iron"):
        logger.warning(
            "Refusing to camp blaze spawner at %s without full iron-or-better armor",
            position,
        )
        return False
    remaining = deadline - time.time()
    if remaining <= 0 or not travel_to(
        client, position, radius=8, timeout=min(120.0, remaining)
    ):
        return False
    try:
        observed = _observed_block(client, position)
    except Exception as exc:
        logger.info("Could not verify shared spawner at %s: %s", position, exc)
        return False
    if observed != "minecraft:spawner":
        logger.info("Skipping stale shared spawner at %s (%s)", position, observed)
        return False

    logger.info("Camping blaze spawner at %s until %d rods", position, target_count)
    while time.time() < deadline:
        current_rods = count_item(client, "minecraft:blaze_rod")
        if current_rods >= target_count:
            return True
        hunt_mobs(
            client,
            mob_types=["blaze"],
            required_loot={"minecraft:blaze_rod": target_count - current_rods},
            search_radius=20,
            timeout=max(1, min(60, int(deadline - time.time()))),
            # Blazes can chain ranged and fire damage. Ten health left too
            # little margin to disengage; begin healing while regeneration can
            # still outpace a follow-up volley.
            heal_threshold=16.0,
            explore_when_empty=False,
            # A live Bot16 fight reduced a blaze to 2 HP, then the generic
            # retreat floor suppressed the finishing hit while fire damage
            # continued to death. This path already requires durable full
            # iron and a shield, so finish one bounded target and stabilize.
            no_retreat=True,
            recover_after_combat=True,
        )
        if count_item(client, "minecraft:blaze_rod") < target_count:
            remaining = deadline - time.time()
            if remaining <= 0 or not travel_to(
                client, position, radius=8, timeout=min(45.0, remaining)
            ):
                return False
    return count_item(client, "minecraft:blaze_rod") >= target_count
