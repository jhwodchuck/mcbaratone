"""Building primitives for deterministic test arenas."""

from typing import Dict, Tuple, Union

from .world import fill, build_floor
from .waits import assert_block


def build_gap_course(ctx, anchor: Tuple[int, int, int], gap_len: int = 2,
                     platform_len_a: int = 5, platform_len_b: int = 5,
                     width: int = 5, verify: bool = True) -> Dict[str, Tuple[int, int, int]]:
    ax, ay, az = anchor
    floor_y = ay - 1
    half_width = width // 2
    platform_a = (ax, ax + platform_len_a - 1)
    platform_b_start = platform_a[1] + 1 + gap_len
    platform_b = (platform_b_start, platform_b_start + platform_len_b - 1)
    build_floor(ctx, platform_a[0], floor_y, az - half_width, platform_a[1], az + half_width)
    build_floor(ctx, platform_b[0], floor_y, az - half_width, platform_b[1], az + half_width)
    if verify:
        assert_block(ctx, platform_a[0], floor_y, az, "minecraft:stone")
        assert_block(ctx, platform_b[0], floor_y, az, "minecraft:stone")
    return {
        "floor_y": floor_y,
        "takeoff": (platform_a[1], ay, az),
        "landing": (platform_b[0] + 1, ay, az),
        "gap_start": (platform_a[1] + 1, floor_y, az),
        "gap_end": (platform_b[0] - 1, floor_y, az),
    }


def build_ladder_wall(ctx, anchor: Tuple[int, int, int], height: int = 6,
                      facing: str = "west", verify: bool = True) -> Dict[str, Tuple[int, int, int]]:
    ax, ay, az = anchor
    wall_x = ax + 2
    ladder_x = ax + 1
    for y in range(ay - 1, ay + height + 1):
        fill(ctx, wall_x, y, az, wall_x, y, az, "minecraft:stone")
    for y in range(ay, ay + height):
        fill(ctx, ladder_x, y, az, ladder_x, y, az, f"minecraft:ladder[facing={facing}]")
    build_floor(ctx, ax + 1, ay + height, az - 1, ax + 3, az + 1)
    if verify:
        assert_block(ctx, ladder_x, ay, az, "minecraft:ladder")
        assert_block(ctx, ladder_x, ay + height - 1, az, "minecraft:ladder")
    return {
        "ladder_base": (ladder_x, ay, az),
        "top": (ladder_x, ay + height, az),
    }


def build_water_tunnel(ctx, anchor: Tuple[int, int, int], length: int = 20,
                       width: int = 3, height: int = 3,
                       add_exit_air: bool = True,
                       verify: bool = True) -> Dict[str, Tuple[int, int, int]]:
    ax, ay, az = anchor
    half_w = width // 2
    half_h = height // 2
    shell_min = (ax - 2, ay - 2, az - half_w - 1)
    shell_max = (ax + length + 2, ay + height + 1, az + half_w + 1)
    fill(ctx, shell_min[0], shell_min[1], shell_min[2], shell_max[0], shell_max[1], shell_max[2], "minecraft:stone")
    fill(ctx, ax, ay - 1, az - half_w, ax + length, ay + height - 2, az + half_w, "air")
    fill(ctx, ax, ay - 1, az - half_w, ax + length, ay + height - 2, az + half_w, "minecraft:water")
    if add_exit_air:
        fill(ctx, ax + length - 2, ay - 1, az - half_w, ax + length, ay + height - 2, az + half_w, "air")
    if verify:
        assert_block(ctx, ax + 1, ay, az, "minecraft:water")
    return {
        "start": (ax + 1, ay, az),
        "end": (ax + length - 2, ay, az),
    }


def build_narrow_ledge(ctx, anchor: Tuple[int, int, int], length: int = 6,
                       drop: int = 20, verify: bool = True) -> Dict[str, Tuple[int, int, int]]:
    ax, ay, az = anchor
    floor_y = ay - 1
    fill(ctx, ax, floor_y, az, ax + length, floor_y, az, "minecraft:stone")
    fill(ctx, ax, floor_y - 1, az - 1, ax + length, floor_y - drop, az + 1, "air")
    if verify:
        assert_block(ctx, ax, floor_y, az, "minecraft:stone")
    return {
        "start": (ax + 1, ay, az),
        "end": (ax + length - 1, ay, az),
        "floor_y": floor_y,
    }


def build_flat_pad(ctx, anchor: Tuple[int, int, int], size: int = 20, block: str = "minecraft:stone") -> Dict[str, Union[int, Tuple[int, int, int]]]:
    ax, ay, az = anchor
    floor_y = ay - 1
    half = size // 2
    build_floor(ctx, ax - half, floor_y, az - half, ax + half, az + half, block)
    return {
        "floor_y": floor_y,
        "center": (ax, ay, az),
        "bounds_min": (ax - half, floor_y, az - half),
        "bounds_max": (ax + half, floor_y, az + half),
    }

def build_corridor(ctx, anchor: Tuple[int, int, int], length: int = 20,
                   width: int = 3, height: int = 3, wall_block: str = "minecraft:stone") -> Dict[str, Tuple[int, int, int]]:
    ax, ay, az = anchor
    half_w = width // 2
    
    # Fill solid box
    fill(ctx, ax - 1, ay - 1, az - half_w - 1, ax + length + 1, ay + height, az + half_w + 1, wall_block)
    # Hollow out center
    fill(ctx, ax, ay, az - half_w, ax + length, ay + height - 1, az + half_w, "minecraft:air")
    
    return {
        "start": (ax + 1, ay, az),
        "end": (ax + length - 1, ay, az),
    }
