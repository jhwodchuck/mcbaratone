"""Screen and crafting helpers."""

import time
from typing import Optional, Tuple

from .common import safe_dispatch
from .interaction import robust_interact
from .waits import wait_for_gui_open


def get_screen(ctx) -> dict:
    return safe_dispatch(ctx, "get_screen", {})


def has_crafting_screen(ctx) -> bool:
    screen = get_screen(ctx)
    screen_type = screen.get("type", "")
    return "Crafting" in screen_type


def wait_for_crafting_screen(ctx, timeout: float = 3.0) -> bool:
    start = time.time()
    while time.time() - start < timeout:
        if has_crafting_screen(ctx):
            return True
        state = ctx.get_state()
        if "crafting" in state.get("screen", "").lower():
            return True
        time.sleep(0.1)
    return False


def wait_for_screen(ctx, screen_name: str, timeout: float = 3.0) -> bool:
    start = time.time()
    while time.time() - start < timeout:
        screen = get_screen(ctx)
        if screen_name.lower() in screen.get("type", "").lower():
            return True
        state = ctx.get_state()
        if screen_name.lower() in state.get("screen", "").lower():
            return True
        time.sleep(0.1)
    return False



def find_nearby_block(ctx, block_id: str, radius: int = 4, limit: int = 1) -> Optional[Tuple[int, int, int]]:
    result = safe_dispatch(ctx, "find_blocks", {"blocks": [block_id], "radius": radius, "limit": limit})
    found = result.get("found", result.get("data", {}).get("found", []))
    if not found:
        return None
    pos = found[0]
    if isinstance(pos, dict) and {"x", "y", "z"}.issubset(pos.keys()):
        return (pos["x"], pos["y"], pos["z"])
    return None


def open_crafting_table(ctx) -> bool:
    safe_dispatch(ctx, "close_screen", {})
    for _ in range(3):
        x, y, z = ctx.get_position()
        nearby = find_nearby_block(ctx, "minecraft:crafting_table", radius=4, limit=1)
        if nearby:
            table_pos = nearby
        else:
            table_pos = (int(x) + 1, int(y), int(z))
            safe_dispatch(ctx, "chat", {
                "message": f"/setblock {table_pos[0]} {table_pos[1]} {table_pos[2]} minecraft:crafting_table"
            })
            start = time.time()
            while time.time() - start < 1.5:
                block = safe_dispatch(ctx, "get_block", {
                    "x": table_pos[0],
                    "y": table_pos[1],
                    "z": table_pos[2],
                })
                data = block.get("data", block)
                if data.get("id") == "minecraft:crafting_table":
                    break
                time.sleep(0.2)
        safe_dispatch(ctx, "chat", {
            "message": f"/setblock {table_pos[0]} {table_pos[1] + 1} {table_pos[2]} minecraft:air"
        })
        
        def _check_open():
             return wait_for_crafting_screen(ctx, timeout=0.1)

        # Look and Interact
        safe_dispatch(ctx, "look_at", {
            "x": table_pos[0] + 0.5,
            "y": table_pos[1] + 0.5,
            "z": table_pos[2] + 0.5
        })
        time.sleep(0.1)

        if robust_interact(
            ctx,
            "interact_block", 
            {"x": table_pos[0], "y": table_pos[1], "z": table_pos[2]}, 
            _check_open,
            retries=5,
            interval=0.5
        ):
            return True
        time.sleep(0.2)
    return False
