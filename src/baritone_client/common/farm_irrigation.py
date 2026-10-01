"""Fresh, read-only recognition of irrigation in an inherited crop plot."""

from collections.abc import Mapping

from .tasks import PlayerDeathDetected


def existing_plot_source(client, x, y, z, *, size=5):
    """Find soil-level source water covering the entire square plot.

    A saved plot origin need not be its water source. Do not trust saved
    irrigation flags, flowing water, or missing block-state observations.
    Unknown observations raise rather than authorizing destructive repair.
    """
    half = max(0, int(size) // 2)
    # Every corner must be within four blocks of the source on both axes.
    reach = min(half, 4 - half)
    for dx in range(-reach, reach + 1):
        for dz in range(-reach, reach + 1):
            if dx == dz == 0:
                continue  # The caller already inspected the saved center.
            try:
                response = client.transport.dispatch(
                    "get_block", {"x": x + dx, "y": y, "z": z + dz}
                )
            except PlayerDeathDetected:
                raise
            except Exception as exc:
                raise ValueError("unavailable farm irrigation block") from exc
            if not isinstance(response, Mapping) or response.get("error"):
                raise ValueError("unavailable farm irrigation block")
            data = response.get("data", response) if isinstance(response, Mapping) else None
            if not isinstance(data, Mapping) or not data.get("id") or data.get("error"):
                raise ValueError("unknown farm irrigation block")
            state = data.get("state")
            if (data.get("id") == "minecraft:water" and
                    isinstance(state, Mapping) and str(state.get("level")) == "0"):
                return x + dx, y, z + dz
    return None
