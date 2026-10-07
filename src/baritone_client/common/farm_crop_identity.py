"""Preserve a wheat plot when Baritone replants a different carried crop."""

from __future__ import annotations

import time
from collections.abc import Mapping


_WHEAT = "minecraft:wheat"
_OTHER_CROPS = {
    "minecraft:carrots", "minecraft:potatoes", "minecraft:beetroots",
}
_AIR = {"minecraft:air", "minecraft:cave_air"}


def _read_block(client, position):
    response = client.transport.dispatch(
        "get_block", {"x": position[0], "y": position[1], "z": position[2]}
    )
    data = response.get("data", response) if isinstance(response, Mapping) else None
    return data if isinstance(data, Mapping) and isinstance(data.get("id"), str) else None


def _age(block):
    state = block.get("state")
    try:
        return int(state["age"]) if isinstance(state, Mapping) else None
    except (KeyError, TypeError, ValueError):
        return None


def _capture_mature_wheat(client, x, y, z, size=5):
    """Capture mature wheat cells only after every plot cell reads cleanly."""
    half = max(1, int(size) // 2)
    mature = []
    for dx in range(-half, half + 1):
        for dz in range(-half, half + 1):
            if not (dx or dz):
                continue
            position = (x + dx, y + 1, z + dz)
            try:
                block = _read_block(client, position)
            except Exception:
                return None
            if block is None:
                return None
            if block["id"] == _WHEAT:
                age = _age(block)
                if age is None:
                    return None
                if age >= 7:
                    mature.append(position)
    return tuple(mature)


def _stopped_after_cancel(client, timeout=2.0):
    deadline = time.monotonic() + timeout
    clear_observations = 0
    while time.monotonic() < deadline:
        try:
            live = client.transport.dispatch("get_state", {})
        except Exception:
            live = None
        if isinstance(live, Mapping) and live.get("is_pathing") is False:
            clear_observations += 1
            if clear_observations >= 2:
                return True
        else:
            clear_observations = 0
        time.sleep(0.1)
    return False


def _restore_converted_wheat(client, positions):
    """Restore only mature wheat cells now holding a fresh wrong crop."""
    from . import farming
    from .farm_planting import _within_block_reach
    from .tasks import PlayerDeathDetected, SurvivalRecoveryRequired

    restored = 0
    for position in positions:
        x, crop_y, z = position
        ground = (x, crop_y - 1, z)
        try:
            if not farming.farm_surface_safe(client):
                return restored
            current = _read_block(client, position)
            soil = _read_block(client, ground)
            if (
                current is None or current["id"] not in _OTHER_CROPS
                or _age(current) not in {0, 1}
                or soil is None or soil["id"] != "minecraft:farmland"
                or not _within_block_reach(client, *ground)
            ):
                continue

            try:
                client.transport.dispatch(
                    "dig_block", {"x": x, "y": crop_y, "z": z,
                                  "face": "UP", "max_ticks": 80}
                )
            except Exception:
                # Reconcile an uncertain start using the exact crop block.
                pass
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                if not farming.farm_surface_safe(client):
                    return restored
                after_dig = _read_block(client, position)
                if after_dig is None:
                    break
                if after_dig["id"] in _AIR:
                    break
                if after_dig["id"] not in _OTHER_CROPS:
                    break
                time.sleep(0.1)
            else:
                continue

            if after_dig is None or after_dig["id"] not in _AIR:
                continue
            soil = _read_block(client, ground)
            if (
                soil is None or soil["id"] != "minecraft:farmland"
                or not _within_block_reach(client, *ground)
                or not farming._till_and_plant_tile(client, *ground)
            ):
                continue
            planted = _read_block(client, position)
            if planted is not None and planted["id"] == _WHEAT:
                restored += 1
        except (PlayerDeathDetected, SurvivalRecoveryRequired):
            raise
        except Exception as exc:
            print(f"  Wheat identity repair skipped {position}: {exc}")
    return restored


def run_wheat_farm_harvest(client, x, y, z, range_):
    """Run the bounded native harvest, then reconcile proven crop substitutions."""
    from . import farming

    if not farming.goto(client, x, y + 1, z, timeout=120, tolerance=4, radius=2):
        return False
    original_wheat = _capture_mature_wheat(client, x, y, z)
    before = farming.count_item(client, _WHEAT)
    try:
        client.transport.dispatch("farm", {"range": range_})
    except Exception as exc:
        print(f"  Farm harvest dispatch failed: {exc}")
        return False

    safe = True
    harvested = False
    try:
        deadline = time.monotonic() + 60.0
        while time.monotonic() < deadline:
            time.sleep(2)
            if not farming.farm_surface_safe(client):
                safe = False
                break
            if farming.count_item(client, _WHEAT) > before:
                harvested = True
                break
    finally:
        cancelled = client.transport.dispatch("cancel", {})

    if (
        not isinstance(cancelled, Mapping)
        or cancelled.get("cancelled") is not True
        or not _stopped_after_cancel(client)
    ):
        return False
    if not safe:
        return False
    if original_wheat is not None:
        _restore_converted_wheat(client, original_wheat)
    farming.replant_empty_wheat_tiles(client, x, y, z)
    return harvested
