"""Bounded, close-range planting without weakening block visibility checks."""

from .tasks import PlayerDeathDetected, SurvivalRecoveryRequired
from math import dist, isfinite


def _within_block_reach(client, x, y, z):
    """Prefer a nearby standing point; the bridge still verifies the real ray."""
    try:
        live = client.transport.dispatch("get_state", {})
        position = live["position"]
        eyes = (float(position["x"]), float(position["y"]) + 1.62, float(position["z"]))
        return (live.get("is_dead") is False and float(live["health"]) >= 14
                and all(isfinite(value) for value in eyes)
                and dist(eyes, (x + .5, y + .5, z + .5)) <= 4.0)
    except (PlayerDeathDetected, SurvivalRecoveryRequired):
        raise
    except Exception:
        return False


def plant_farm_tiles(client, tiles):
    """Plant from a reachable stand first, avoiding needless crop trampling."""
    from . import farming

    planted = 0
    for x, y, z in tiles:
        if farming.count_item(client, "minecraft:wheat_seeds") < 1:
            break
        try:
            if (_within_block_reach(client, x, y, z)
                    and farming._till_and_plant_tile(client, x, y, z)):
                planted += 1
                continue
            if not farming.goto(client, x, y + 1, z, timeout=15,
                                tolerance=1.5, radius=1):
                continue
            if farming._till_and_plant_tile(client, x, y, z):
                planted += 1
        except (PlayerDeathDetected, SurvivalRecoveryRequired):
            raise
        except Exception as exc:
            print(f"  Farm planting skipped {(x, y, z)}: {exc}")
    return planted
