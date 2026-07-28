"""Focused helpers for bounded emergency-food recovery."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .movement_recovery import (
    ExplorationWaypoints,
    MovementWatchdog,
    block_position,
)
from .surface_egress import try_lower_surface_egress


PRIMARY_LAND_FOOD = ("cow", "pig")
SECONDARY_LAND_FOOD = ("sheep", "chicken", "rabbit")
WATER_FOOD = ("salmon", "cod", "tropical_fish")


def select_target(
    client: Any,
    nearby: List[Dict],
    *,
    current_food: int,
    elapsed: float,
    timeout: float,
    renewable_source_callback: Optional[
        Callable[[str, tuple[int, int, int]], None]
    ],
    in_water: bool = False,
) -> Optional[Dict]:
    """Choose a renewable land target, with nearby fish as fallback.

    ``in_water`` reports that the player is already submerged, which lifts the
    normal restrictions on aquatic targets -- see the fallback below.
    """
    from . import combat as api

    for animal_type in PRIMARY_LAND_FOOD + SECONDARY_LAND_FOOD:
        group = [
            entity
            for entity in nearby
            if animal_type in str(entity.get("type", "")).lower()
            and not entity.get("is_baby", False)
        ]
        if len(group) >= 3:
            positions = [api.entity_position(entity) for entity in group]
            positions = [position for position in positions if position is not None]
            if positions and renewable_source_callback is not None:
                centroid = tuple(
                    round(sum(position[axis] for position in positions) / len(positions))
                    for axis in range(3)
                )
                renewable_source_callback(animal_type, centroid)
            return min(group, key=lambda entity: float(entity.get("distance", 999)))
        if len(group) == 1 and current_food <= 8:
            return group[0]
        if len(group) == 2 and current_food <= 2:
            return min(group, key=lambda entity: float(entity.get("distance", 999)))

    # Standing in water changes the calculus. The 16-block cap exists so a
    # bot on land is not dragged into water chasing a distant fish it can
    # never reach -- but a bot already submerged has nothing left to be
    # dragged into, and in a lush cave there are no land animals to wait for,
    # so the land-search delay is pure starvation. Live: Bot07 and Bot08 sat
    # at 8.0/7.3 health in lush caves, feet in water, with 8-10 tropical fish
    # inside 128 blocks -- their only food source -- and never targeted one.
    if in_water:
        # 64, not something tighter: measured live, the nearest fish to a
        # starving bot in a lush cave sat at 48.7m, and a radius-48 query
        # returned nothing at all. A shorter leash simply means never eating.
        target = api.find_entity_by_type(client, list(WATER_FOOD), radius=64)
        if target is not None:
            return target

    water_fallback_after = min(90.0, max(15.0, timeout / 2.0))
    if current_food <= 6 or elapsed >= water_fallback_after:
        return api.find_entity_by_type(client, list(WATER_FOOD), radius=16)
    return None


@dataclass
class EmergencyExploration:
    """Own movement proof, waypoint rotation, and sprint restoration."""

    origin_x: float
    origin_z: float
    maximum_radius: float
    movement: MovementWatchdog = field(default_factory=MovementWatchdog)
    exploring: bool = False
    sprint_suppressed: bool = False
    surface_egress_attempted: bool = False

    def __post_init__(self) -> None:
        self.waypoints = ExplorationWaypoints(
            self.origin_x, self.origin_z, self.maximum_radius
        )

    def start(self, client: Any, state: Dict) -> None:
        """Start or rotate toward a non-zero bounded exploration target."""
        if self.exploring:
            client.transport.dispatch("cancel", {})
            client.transport.dispatch("chat", {"message": "#stop"})
        target_x, target_z = self.waypoints.next()
        print(
            "RECOVERY: no passive food source loaded; rotating bounded "
            f"daylight exploration toward ({target_x}, {target_z})"
        )
        client.transport.dispatch(
            "explore", {"x": target_x, "z": target_z}
        )
        self.exploring = True
        self.movement.reset(state)

    def observe(self, state: Dict) -> None:
        self.movement.observe(state)

    def needs_rotation(self, state: Dict) -> bool:
        return (
            not self.exploring
            or (
                self.movement.stalled(3)
                and not bool(state.get("is_pathing", False))
            )
            or self.movement.stalled(6)
        )

    def try_surface_egress(self, client: Any, state: Dict) -> bool:
        """Attempt a single local egress from a high shelf."""
        if self.surface_egress_attempted or block_position(state)[1] < 96:
            return False
        self.surface_egress_attempted = True
        egress = try_lower_surface_egress(
            client,
            state,
            attempt_limit=6,
            timeout_per_candidate=18.0,
        )
        if egress is None:
            return False
        self.movement.reset(
            {
                "block_position": {
                    "x": egress[0],
                    "y": egress[1],
                    "z": egress[2],
                }
            }
        )
        return True

    def suppress_sprint(self, client: Any) -> None:
        client.transport.dispatch("chat", {"message": "#set allowSprint false"})
        self.sprint_suppressed = True

    def stop(self, client: Any) -> None:
        if self.exploring:
            client.transport.dispatch("cancel", {})
            client.transport.dispatch("chat", {"message": "#stop"})
            self.exploring = False
        if self.sprint_suppressed:
            client.transport.dispatch(
                "chat", {"message": "#set allowSprint true"}
            )
            self.sprint_suppressed = False


def hunt_target(
    client: Any,
    target: Dict,
    *,
    minimum_health: float,
    recovery_complete: Callable[[Optional[Dict]], bool],
) -> Optional[bool]:
    """Hunt one selected target; return True recovered, False failed, None retry."""
    from . import combat as api

    target_id = target.get("id")
    target_pos = api.entity_position(target)
    if target_id is None or target_pos is None:
        time.sleep(1)
        return None
    print(
        f"RECOVERY: safely hunting {target.get('type')} at "
        f"{target.get('distance', 999):.1f}m"
    )
    target_type = str(target.get("type", ""))
    aquatic = any(water_type in target_type for water_type in WATER_FOOD)
    if aquatic and float(target.get("distance", 999)) >= 4.5:
        # Scale the follow window to the distance. A flat 8s cannot cover the
        # ~48m separation measured live in a lush cave, so every attempt
        # reported "aquatic target could not be reached safely" and the bot
        # starved beside its only food source. Bounded so a fish that keeps
        # swimming away still ends the attempt. The drowning reflex inside
        # the follow loop remains the safety net for the longer swim.
        approach_timeout = min(
            30.0, max(8.0, float(target.get("distance", 8.0)) * 0.5)
        )
        if not api._approach_aquatic_food(
            client, target_id, target_type, timeout=approach_timeout
        ):
            print("RECOVERY: aquatic target could not be reached safely")
            time.sleep(1)
            return None
    if not api.safe_combat(
        client,
        target_id,
        retreat_health=1.0,
        max_duration=35,
        abort_on_other_hostiles=True,
    ):
        return False
    if aquatic:
        if not api._surface_after_aquatic_hunt(client):
            print("RECOVERY: could not prove breathing air after aquatic hunt")
            return False
    else:
        from .navigation import goto

        goto(
            client,
            int(target_pos[0]),
            int(target_pos[1]),
            int(target_pos[2]),
            timeout=45,
            tolerance=1.5,
        )
    time.sleep(2)
    api.heal_if_needed(client, threshold=minimum_health)
    api.recover_health(client, minimum_health=minimum_health, timeout=25.0)
    return True if recovery_complete(None) else None
