"""Keep emergency bread crafting inside freshly observed home shelter."""

from .tasks import PlayerDeathDetected


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
    return not restricted or (table is not None and bool(open_table(client, table_pos=table)))
