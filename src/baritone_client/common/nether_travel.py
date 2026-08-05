"""Supervised Nether travel behind :func:`nether._travel_to`."""

import time
from typing import Callable, Optional

from . import nether as api
from .tasks import PlayerDeathDetected


def execute_nether_travel(
    client,
    target: tuple[int, int, int],
    *,
    radius: int,
    timeout: float,
    on_defense: Optional[Callable[[], bool]],
    defense_check_interval: float,
) -> bool:
    """Wait for a Nether goal with survival and defense supervision."""
    x, y, z = int(target[0]), int(target[1]), int(target[2])
    interval = max(0.1, float(defense_check_interval))
    try:
        client.transport.dispatch(
            "goto", {"x": x, "y": y, "z": z, "radius": int(radius)}
        )
    except Exception as exc:
        api.logger.warning("Travel dispatch failed: %s", exc)
        return False

    deadline = time.time() + timeout
    previous_remaining = None
    stalled = 0
    egress_attempted = False
    while time.time() < deadline:
        try:
            state = api._unwrap(client.transport.dispatch("get_state", {}))
            from .combat import survival_tick
            from .navigation import run_navigation_defense

            if survival_tick(client, state):
                client.transport.dispatch("cancel", {})
                return False
            if run_navigation_defense(client, on_defense):
                client.transport.dispatch("cancel", {})
                return False
        except PlayerDeathDetected:
            try:
                client.transport.dispatch("cancel", {})
            except Exception:
                pass
            raise
        except Exception as exc:
            api.logger.warning("Travel supervision failed: %s", exc)
            try:
                client.transport.dispatch("cancel", {})
            except Exception:
                pass
            return False

        here = api._position(state)
        remaining = max(abs(here[0] - x), abs(here[2] - z))
        if remaining <= radius + 2:
            return True
        if previous_remaining is not None and remaining >= previous_remaining:
            stalled += 1
            if stalled >= 3:
                if not egress_attempted and not bool(state.get("is_pathing")):
                    egress_attempted = True
                    landing = api.try_lower_surface_egress(
                        client,
                        state,
                        minimum_altitude=0,
                        allow_upward_excavation=False,
                    )
                    if landing is not None:
                        api.logger.info(
                            "Recovered marooned travel via lower surface %s",
                            landing,
                        )
                        client.transport.dispatch(
                            "goto",
                            {
                                "x": x,
                                "y": y,
                                "z": z,
                                "radius": int(radius),
                            },
                        )
                        previous_remaining = None
                        stalled = 0
                        continue
                api.logger.info(
                    "Travel to (%d, %d, %d) stopped closing", x, y, z
                )
                return False
        else:
            stalled = 0
        previous_remaining = remaining
        time.sleep(interval)
    return False
