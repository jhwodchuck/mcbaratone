"""Deterministic, collision-free route geometry for perpetual builders."""

from __future__ import annotations

from dataclasses import dataclass

ROAD_WIDTH = 3
# Each bot owns a ten-block-wide Z corridor.  Three rows of three-wide roads
# fit inside that corridor without touching either neighboring bot.
ROUTES = (
    (1, 0),
    (-1, 0),
    (1, 3),
    (-1, 3),
    (1, -3),
    (-1, -3),
)


@dataclass(frozen=True)
class LaneAssignment:
    """One exclusive corridor containing six resumable road routes."""

    bot: str
    anchor_x: int
    anchor_z: int
    expected_y: int = 75

    def cell(self, slot: int, route_index: int = 0) -> tuple[int, int]:
        """Return one X/Z cell on the selected three-wide route."""
        direction_x, row_offset = ROUTES[route_index % len(ROUTES)]
        longitudinal, width_slot = divmod(slot, ROAD_WIDTH)
        # Westbound routes begin immediately west of the hub so their first
        # course never duplicates the eastbound course at anchor_x.
        if direction_x < 0:
            longitudinal += 1
        cross = width_slot - ROAD_WIDTH // 2
        return (
            self.anchor_x + direction_x * longitudinal,
            self.anchor_z + row_offset + cross,
        )


def fleet_lanes(center_x: int = 700, center_z: int = -702) -> dict[str, LaneAssignment]:
    """Return six corridors separated enough that all routes remain disjoint."""
    bots = ("Bot07", "Bot15", "Bot16", "Bot17", "Bot18", "Bot19")
    offsets = (-25, -15, -5, 5, 15, 25)
    return {
        bot: LaneAssignment(bot, center_x, center_z + offset)
        for bot, offset in zip(bots, offsets)
    }


__all__ = ["LaneAssignment", "ROAD_WIDTH", "ROUTES", "fleet_lanes"]
