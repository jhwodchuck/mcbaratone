"""Bounded, non-destructive home capacity for a mining expedition."""

import math

from ..common.home_surface import bind_home_surface, protect_home_route


AIR = {"minecraft:air", "minecraft:cave_air"}


def _block(client, cell):
    return client.transport.dispatch("get_block", dict(zip(("x", "y", "z"), cell))).get("id", "")


def _at_home(client, anchor):
    from ..common.food_workstation import _fresh_safe_position
    from ..common.combat import scan_for_threats

    position = _fresh_safe_position(client, require_grounded=True)
    if position is None or math.dist(position, anchor) > 4 or abs(position[1] - anchor[1]) > 1:
        return False
    live = client.transport.dispatch("get_state", {})
    return (live.get("is_dead") is False and live.get("is_pathing") is False
            and live.get("dimension") == "minecraft:overworld" and live.get("game_mode") == "survival"
            and live.get("is_on_ground") is True and float(live.get("health", 0)) >= 18
            and int(live.get("food_level", 0)) >= 14
            and not scan_for_threats(client, radius=8, raise_on_error=True, player_state=live))


def _spot(client, state, anchor):
    from ..common.base import _ALL_PLANKS

    house_blocks = {"minecraft:cobblestone", "minecraft:stone"} | set(_ALL_PLANKS)
    house = state.custom_data.get("structures", {}).get("starter_house", {})
    origin = house.get("origin")
    if not isinstance(origin, (list, tuple)) or len(origin) != 3:
        return None
    if not all(not isinstance(v, bool) and isinstance(v, (int, float)) and math.isfinite(v) for v in origin):
        return None
    ox, oy, oz = map(int, origin)
    excluded = {tuple(v) for v in house.values() if isinstance(v, (list, tuple)) and len(v) == 3}
    for dx, dz in ((5, 1), (5, 5), (1, 5), (1, 1)):
        cell = (ox + dx, oy + 1, oz + dz)
        if cell in excluded or math.dist(cell, anchor) > 4.5:
            continue
        wall = (ox + (6 if dx == 5 else 0), oy + 1, oz + dz)
        if (_block(client, cell) in AIR
                and _block(client, (cell[0], cell[1] + 1, cell[2])) in AIR
                and _block(client, (cell[0], cell[1] - 1, cell[2])) in house_blocks
                and _block(client, wall) in house_blocks):
            return cell
    return None


@protect_home_route(surface_work=True, safe_movement=True)
def _bank(client, state, anchor, required, items, retains):
    from ..common.harness_ops import place_block_exact
    from ..common.inventory import count_item, craft, deposit_excess_to_chest, get_inventory
    from ..common.resources import LOG_TO_PLANKS, PLANK_ITEMS
    from ..common.storage_catalog import catalog_for
    from ..common.tunnel_miner import free_slots
    from .charcoal_wood import gather_charcoal_logs

    record = state.custom_data.setdefault("mining_storage", {})
    saved = record.get("chest")
    chest = tuple(saved) if isinstance(saved, (list, tuple)) and len(saved) == 3 else None
    if chest is not None and (math.dist(chest, anchor) > 4.5 or _block(client, chest) != "minecraft:chest"):
        chest = None
    if chest is None:
        chest = _spot(client, state, anchor)
        if chest is None:
            return False
        if count_item(client, "minecraft:chest") < 1:
            if sum(count_item(client, p) for p in PLANK_ITEMS) < 8:
                if sum(count_item(client, log) for log in LOG_TO_PLANKS) < 2:
                    gather_charcoal_logs(client, state, 2, anchor)
                if not _at_home(client, anchor):
                    return False
                for log, plank in LOG_TO_PLANKS.items():
                    missing = 8 - sum(count_item(client, p) for p in PLANK_ITEMS)
                    if missing > 0 and count_item(client, log) > 0:
                        craft(client, plank, min(missing, count_item(client, log) * 4))
            if sum(count_item(client, p) for p in PLANK_ITEMS) < 8:
                return False
            craft(client, "minecraft:chest", 1)
        if count_item(client, "minecraft:chest") < 1 or not _at_home(client, anchor):
            return False
        # Recheck the target after gathering/crafting; never clear an obstruction.
        if _spot(client, state, anchor) != chest:
            return False
        place_block_exact(client, *chest, "minecraft:chest", allow_break=False)
        if _block(client, chest) != "minecraft:chest":
            return False
        record["chest"] = list(chest)
        catalog_for(client, state).register_container(chest, dimension="minecraft:overworld", purpose="mining_overflow")
        writer = getattr(state, "save_checkpoint", None)
        if callable(writer):
            writer(get_inventory(client))
    if not _at_home(client, anchor):
        return False
    deposit_excess_to_chest(client, chest, deposit_items=items, retain_counts=retains, state=state)
    return free_slots(client) >= required


def bank_mining_overflow(client, state, anchor, required, items, retains):
    """One chest, one nearby daylight wood batch, then verified free slots."""
    from ..common.tasks import PlayerDeathDetected, SurvivalRecoveryRequired

    bind_home_surface(client, state)
    try:
        if not _at_home(client, anchor):
            from ..common.food_workstation import _fresh_safe_position
            from ..common.food_return import _return

            position = _fresh_safe_position(client, require_grounded=True)
            if (position is None or math.dist(position, anchor) > 32
                    or abs(position[1] - anchor[1]) > 2):
                return False
            _return(client, *anchor)
            if not _at_home(client, anchor):
                return False
        return _bank(client, state, anchor, required, items, retains)
    except (PlayerDeathDetected, SurvivalRecoveryRequired):
        raise
    except Exception as exc:
        print(f"IRON PREPARATION: local overflow deferred ({exc})")
        return False
