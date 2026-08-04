"""Bounded exploration state for passive-mob hunts."""

from dataclasses import dataclass
from typing import Any, Callable, Optional

from .combat_targeting import matches_requested_mob


def find_unrequested_hostile(entities: list[dict], requested: list[str]):
    """Return a nearby hostile that is not one of the requested targets."""
    hostile_names = (
        "zombie",
        "skeleton",
        "creeper",
        "spider",
        "witch",
        "pillager",
        "slime",
    )
    return next(
        (
            entity
            for entity in entities
            if any(
                hostile in str(entity.get("type", "")).lower()
                for hostile in hostile_names
            )
            and not matches_requested_mob(
                str(entity.get("type", "")), requested
            )
        ),
        None,
    )


@dataclass
class PassiveHuntNavigation:
    """Recover an accepted-but-idle centered exploration command."""

    active: bool = False
    idle_checks: int = 0
    route_failures: int = 0

    def reset(self) -> None:
        self.active = False
        self.idle_checks = 0

    def stop(self, client: Any) -> None:
        if self.active:
            client.transport.dispatch("chat", {"message": "#stop"})
        self.reset()

    def advance(
        self,
        client: Any,
        live_state: dict,
        center: Optional[tuple[int, int]],
        *,
        enabled: bool,
        navigate: Callable[..., bool],
    ) -> Optional[str]:
        """Advance exploration and return a terminal failure reason if any."""
        if not enabled:
            return None
        if not self.active:
            print("  No targets found, starting exploration...")
            if center is not None:
                client.transport.dispatch(
                    "explore", {"x": int(center[0]), "z": int(center[1])}
                )
            else:
                client.transport.dispatch("chat", {"message": "#explore"})
            self.active = True
            self.idle_checks = 0
            return None

        self.idle_checks = self.idle_checks + 1 if live_state.get(
            "is_pathing"
        ) is False else 0
        if self.idle_checks < 3 or center is None:
            return None

        self.stop(client)
        client.transport.dispatch("cancel", {})
        print(f"  Exploration goal was idle; staging toward sector {center}...")
        staged = navigate(
            client,
            int(center[0]),
            int(center[1]),
            timeout=45,
            tolerance=24.0,
        )
        if staged:
            self.route_failures = 0
            return None
        self.route_failures += 1
        if self.route_failures >= 3:
            return "Passive-hunt exploration routes were rejected"
        return None
