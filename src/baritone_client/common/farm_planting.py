"""Bounded, close-range planting without weakening block visibility checks."""

from .tasks import PlayerDeathDetected, SurvivalRecoveryRequired


def plant_farm_tiles(client, tiles):
    """Approach each soil tile at crop height; one refusal cannot abort all."""
    from . import farming

    planted = 0
    for x, y, z in tiles:
        if farming.count_item(client, "minecraft:wheat_seeds") < 1:
            break
        try:
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
