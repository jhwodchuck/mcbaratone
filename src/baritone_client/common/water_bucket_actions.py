"""Capability-gated water use; callers still verify inventory and world state."""

import time
from collections.abc import Mapping


def use_water_bucket(client, x, y, z, operation, *, legacy_aim):
    """Prefer the exact, observed bridge operation over a crosshair key pulse.

    Preserve older bridges' existing path only when they do not advertise the
    verified capability. A query error or failed verified operation is not
    permission to retry a mutation through a less precise route.
    """
    if operation not in ("pickup", "place"):
        return False
    try:
        version = client.transport.dispatch("get_version", {})
        if not isinstance(version, Mapping) or version.get("error"):
            return False
        data = version.get("data", version)
        if not isinstance(data, Mapping) or data.get("error"):
            return False
        capabilities = data.get("capabilities", {})
        if not isinstance(capabilities, Mapping):
            return False
        verified = capabilities.get("water_bucket_postconditions")
        if verified is True:
            client.transport.dispatch("use_bucket", {
                "x": int(x), "y": int(y), "z": int(z), "operation": operation,
            })
            return True
        if verified is not None and verified is not False:
            return False
        ax, ay, az = legacy_aim
        client.transport.dispatch("look_at", {"x": ax, "y": ay, "z": az})
        time.sleep(0.2)
        client.transport.dispatch("use_item", {"duration_ms": 0})
        time.sleep(0.5)
        return True
    except Exception as exc:
        print(f"  Water bucket operation failed: {exc}")
        return False
