"""Entity/combat helpers for Minecraft integration tests."""

import time
from typing import Dict, List, Optional

from .common import safe_dispatch


def get_entities(ctx, radius: int = 10) -> List[Dict]:
    result = safe_dispatch(ctx, "get_entities", {"radius": radius})
    return result.get("entities", result.get("data", {}).get("entities", []))


def wait_for_entity(ctx, entity_type: str, radius: int = 16, timeout: float = 3.0) -> Optional[Dict]:
    start = time.time()
    while time.time() - start < timeout:
        ents = get_entities(ctx, radius=radius)
        target = next((ent for ent in ents if ent.get("type") == entity_type), None)
        if target:
            return target
        time.sleep(0.2)
    return None


def get_entity_by_id(ctx, entity_id: int, radius: int = 16) -> Optional[Dict]:
    ents = get_entities(ctx, radius=radius)
    return next((ent for ent in ents if ent.get("id") == entity_id), None)


def summon_near(ctx, entity_id: str, dx: int = 2, dy: int = 1, dz: int = 0, nbt: str = "") -> None:
    x, y, z = ctx.get_position()
    sx = int(x) + dx
    sy = int(y) + dy
    sz = int(z) + dz
    suffix = f" {nbt}" if nbt else ""
    safe_dispatch(ctx, "chat", {"message": f"/summon {entity_id} {sx} {sy} {sz}{suffix}"})


def distance_to_entity(ctx, ent: Dict) -> float:
    pos = ent.get("position", {})
    x, y, z = ctx.get_position()
    dx = pos.get("x", 0) + 0.5 - x
    dy = pos.get("y", 0) + 0.5 - y
    dz = pos.get("z", 0) + 0.5 - z
    return (dx * dx + dy * dy + dz * dz) ** 0.5


def arena_bounds(center: tuple, size: int = 30):
    cx, _, cz = center
    half = size // 2
    min_x = cx - half
    max_x = cx + half - 1
    min_z = cz - half
    max_z = cz + half - 1
    return min_x, max_x, min_z, max_z


def build_arena(ctx, center: tuple, size: int = 30, wall_height: int = 3) -> Dict[str, int]:
    cx, cy, cz = center
    floor_y = int(cy) - 1
    min_x, max_x, min_z, max_z = arena_bounds(center, size=size)
    safe_dispatch(ctx, "chat", {"message": "/gamerule doMobSpawning false"})
    safe_dispatch(ctx, "chat", {"message": "/gamerule doDaylightCycle false"})
    safe_dispatch(ctx, "chat", {"message": "/time set day"})
    safe_dispatch(ctx, "chat", {
        "message": f"/fill {min_x} {floor_y} {min_z} {max_x} {floor_y + wall_height + 2} {max_z} air"
    })
    safe_dispatch(ctx, "chat", {
        "message": f"/fill {min_x} {floor_y} {min_z} {max_x} {floor_y} {max_z} minecraft:stone"
    })
    safe_dispatch(ctx, "chat", {
        "message": f"/fill {min_x} {floor_y + 1} {min_z} {min_x} {floor_y + wall_height} {max_z} minecraft:stone_bricks"
    })
    safe_dispatch(ctx, "chat", {
        "message": f"/fill {max_x} {floor_y + 1} {min_z} {max_x} {floor_y + wall_height} {max_z} minecraft:stone_bricks"
    })
    safe_dispatch(ctx, "chat", {
        "message": f"/fill {min_x} {floor_y + 1} {min_z} {max_x} {floor_y + wall_height} {min_z} minecraft:stone_bricks"
    })
    safe_dispatch(ctx, "chat", {
        "message": f"/fill {min_x} {floor_y + 1} {max_z} {max_x} {floor_y + wall_height} {max_z} minecraft:stone_bricks"
    })
    safe_dispatch(ctx, "chat", {
        "message": f"/setblock {min_x} {floor_y + wall_height} {min_z} minecraft:glowstone"
    })
    safe_dispatch(ctx, "chat", {
        "message": f"/setblock {min_x} {floor_y + wall_height} {max_z} minecraft:glowstone"
    })
    safe_dispatch(ctx, "chat", {
        "message": f"/setblock {max_x} {floor_y + wall_height} {min_z} minecraft:glowstone"
    })
    safe_dispatch(ctx, "chat", {
        "message": f"/setblock {max_x} {floor_y + wall_height} {max_z} minecraft:glowstone"
    })
    return {
        "center": (cx, int(cy), cz),
        "floor_y": floor_y,
        "min_x": min_x,
        "max_x": max_x,
        "min_z": min_z,
        "max_z": max_z,
        "size": size,
        "wall_height": wall_height,
    }


def within_bounds(ctx, bounds: Dict[str, int]) -> bool:
    x, _, z = ctx.get_position()
    return bounds["min_x"] <= x <= bounds["max_x"] and bounds["min_z"] <= z <= bounds["max_z"]


def spawn_wave(ctx, bounds: Dict[str, int], enemy_type: str, count: int) -> None:
    cx, cy, cz = bounds["center"]
    radius = min(10, (bounds["size"] // 2) - 2)
    positions = [
        (cx, cy, cz + radius),
        (cx + radius, cy, cz),
        (cx, cy, cz - radius),
        (cx - radius, cy, cz),
        (cx + radius, cy, cz + radius),
        (cx + radius, cy, cz - radius),
        (cx - radius, cy, cz + radius),
        (cx - radius, cy, cz - radius),
    ]
    for i in range(count):
        sx, sy, sz = positions[i]
        safe_dispatch(ctx, "chat", {"message": f"/summon {enemy_type} {int(sx)} {int(sy)} {int(sz)}"})


def nearest_entity(ctx, entity_type: str, radius: int = 20) -> Optional[Dict]:
    ents = get_entities(ctx, radius=radius)
    targets = [ent for ent in ents if ent.get("type") == entity_type]
    if not targets:
        return None
    return min(targets, key=lambda ent: distance_to_entity(ctx, ent))
