"""Resumable ten-obsidian portal construction with physical corner supports."""

import time

from .automation_utils import _block_at, _is_solid, place_block
from .inventory import get_inventory
from .navigation import goto
from ..observability import emit_event

AIR = {"minecraft:air", "minecraft:cave_air"}
SUPPORTS = ("minecraft:cobblestone", "minecraft:stone", "minecraft:dirt",
            "minecraft:cobbled_deepslate")


def prepare_ignition_pose(client, origin):
    """Stand outside fire's safety radius while retaining ordinary reach."""
    x, y, z = origin
    for dz in (-2, 2):
        pose = (x + 1, y, z + dz)
        if not (_is_solid(client, pose[0], y - 1, pose[2])
                and _block_at(client, *pose) in AIR
                and _block_at(client, pose[0], y + 1, pose[2]) in AIR):
            continue
        if goto(client, *pose, timeout=10, tolerance=0.8):
            return getattr(client, "_last_navigation_cancel", {}).get("cancel_status") == "stopped"
        return False
    return False


def construct_frame(client, origin, frame, interior):
    """Keep correct partial blocks. Never excavate or roll back a failed frame.

    Corners need no obsidian, but four ordinary blocks give each side and top
    a placement face. A failed/unknown operation ends the attempt at this site.
    Every later attempt re-reads the entire site before spending another item.
    """
    x, y, z = origin
    corners = {(x + dx, y + dy, z) for dx in (0, 3) for dy in (0, 4)}
    observed = {p: _block_at(client, *p) for p in set(frame) | set(interior) | corners}
    if any(observed[p] not in AIR | {"minecraft:obsidian"} for p in frame):
        return False
    if any(observed[p] not in AIR for p in interior):
        return False
    if any(observed[p] not in AIR and not _is_solid(client, *p) for p in corners):
        return False
    # All four foundation cells must be real, loaded support before any spend.
    if not all(_is_solid(client, x + dx, y - 1, z) for dx in range(4)):
        return False
    inventory = get_inventory(client)
    missing = sum(observed[p] != "minecraft:obsidian" for p in frame)
    if inventory.get("minecraft:obsidian", 0) < missing:
        return False
    needed_supports = sum(observed[p] in AIR for p in corners)
    if sum(inventory.get(item, 0) for item in SUPPORTS) < needed_supports:
        return False
    # Stand beside the frame on a verified floor. From here the top remains
    # within ordinary Survival reach; never ask navigation to stand in midair.
    poses = [(x + 1, y, z - 1), (x + 1, y, z + 1)]
    pose = next((p for p in poses if _is_solid(client, p[0], p[1] - 1, p[2])
                 and _block_at(client, *p) in AIR
                 and _block_at(client, p[0], p[1] + 1, p[2]) in AIR), None)
    if pose is None or not goto(client, *pose, timeout=30, tolerance=0.8):
        return False
    # A true navigation result proves arrival, while construction also needs
    # the one cleanup cancel to be observed stopped before spending blocks.
    if getattr(client, "_last_navigation_cancel", {}).get("cancel_status") != "stopped":
        return False
    plan = sorted(set(frame) | corners, key=lambda p: (p[1], p[0]))
    emit_event("portal_build_started", origin=list(origin), missing_obsidian=missing,
               missing_supports=needed_supports, position=list(pose))
    for p in plan:
        if p in corners and observed[p] not in AIR:
            continue
        if p in frame and observed[p] == "minecraft:obsidian":
            continue
        item = "minecraft:obsidian"
        if p in corners:
            item = next((i for i in SUPPORTS if inventory.get(i, 0) > 0), None)
        if item is None or not place_block(client, *p, item):
            emit_event("portal_build_interrupted", origin=list(origin), target=list(p),
                       expected_block=item, action_status="unverified", retry="reconcile_same_site")
            return False
        # A direct read remains mandatory even if a legacy bridge says placed.
        if _block_at(client, *p) != item:
            return False
        inventory[item] = inventory.get(item, 0) - 1
        emit_event("portal_block_verified", origin=list(origin), target=list(p),
                   block=item, postcondition_verified=True)
        time.sleep(0.1)
    return all(_block_at(client, *p) == "minecraft:obsidian" for p in frame)
