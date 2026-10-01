"""Keep emergency bread crafting inside freshly observed home shelter."""

from math import floor

from .navigation import goto
from .tasks import PlayerDeathDetected, SurvivalRecoveryRequired


def sheltered_bread_table(client):
    """Return (restricted, table): no table in shelter means no outside hunt.

    A saved home anchor enables the policy but is not enclosure proof. Unknown
    observations fail closed; an observed unenclosed location keeps ordinary
    workstation behavior for bootstrap recovery.
    """
    if getattr(client, "_protected_home_anchor", None) is None:
        return False, None
    from .survival_farm import _enclosure_bounds, _position
    from .farming import _block_data

    try:
        live = client.transport.dispatch("get_state", {})
        here = _position(live)
        if here is None:
            return True, None
        bounds = _enclosure_bounds(client, live, strict=True)
        if not bounds:
            return False, None
        west, east, y, north, south = bounds
        tables = []
        for x in range(west + 1, east):
            for z in range(north + 1, south):
                if _block_data(client, x, y, z).get("id") == "minecraft:crafting_table":
                    tables.append((x, y, z))
        if not tables:
            return True, None
        return True, min(tables, key=lambda p: sum(
            (p[i] - here[i]) ** 2 for i in range(3)
        ))
    except PlayerDeathDetected:
        raise
    except Exception:
        return True, None


def prepare_sheltered_bread_craft(client, open_table) -> bool:
    """Guard the food worker's direct bread craft as well as recovery."""
    restricted, table = sheltered_bread_table(client)
    return not restricted or (
        table is not None and _approach_inside_table(client, table)
        and bool(open_table(client, table_pos=table))
    )


def _approach_inside_table(client, table) -> bool:
    """Move beside the table without letting a generic stand search leave home."""
    from .farming import _block_data
    from .survival_farm import _enclosure_bounds, _position

    try:
        bounds = _enclosure_bounds(client, client.transport.dispatch("get_state", {}), strict=True)
        if not bounds:
            return False
        west, east, y, north, south = bounds
        tx, _, tz = table
        for x, z in ((tx + 1, tz), (tx - 1, tz), (tx, tz + 1), (tx, tz - 1)):
            if not (west < x < east and north < z < south):
                continue
            if any(_block_data(client, x, dy, z).get("id") != "minecraft:air" for dy in (y, y + 1)):
                continue
            support = _block_data(client, x, y - 1, z).get("id", "")
            if not support or any(token in support for token in (
                "air", "water", "lava", "powder_snow", "magma", "campfire", "cactus", "dripstone",
            )):
                continue
            if not goto(client, x, y, z, timeout=15, tolerance=1.5, radius=1):
                continue
            arrived = client.transport.dispatch("get_state", {})
            if arrived.get("is_dead"):
                raise PlayerDeathDetected("death during sheltered table approach")
            here = _position({"position": arrived.get("position", {})})
            if (here is not None and west < floor(here[0]) < east
                    and north < floor(here[2]) < south and abs(here[1] - y) <= 0.2):
                return True
        return False
    except (PlayerDeathDetected, SurvivalRecoveryRequired):
        raise
    except Exception:
        return False
