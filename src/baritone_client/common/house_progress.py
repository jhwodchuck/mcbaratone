"""World-verified starter-house progress accounting."""

from __future__ import annotations

from typing import Any, Callable


def summarize_house_progress(
    client: Any,
    x: int,
    y: int,
    z: int,
    wait_for_chunk: Callable[..., Any],
    house_plan: Callable[..., Any],
    block_id: Callable[..., str],
    matches_role: Callable[[str, str], bool],
    door_aligned: Callable[..., bool],
) -> dict[str, int | bool]:
    """Count complete starter-house blocks by their structural role."""
    wait_for_chunk(client, x, y, z)
    totals = {"floor": 0, "shell": 0, "roof": 0}
    complete = {"floor": 0, "shell": 0, "roof": 0}
    for tx, ty, tz, role in house_plan(x, y, z):
        totals[role] += 1
        complete[role] += int(matches_role(block_id(client, tx, ty, tz), role))
    return {
        "floor": complete["floor"],
        "shell": complete["shell"],
        "roof": complete["roof"],
        "floor_total": totals["floor"],
        "shell_total": totals["shell"],
        "roof_total": totals["roof"],
        "door_present": door_aligned(client, x + 3, y + 1, z),
    }
