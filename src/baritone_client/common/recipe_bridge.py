"""Bounded, replay-safe dispatch for the bridge ``place_recipe`` command."""

from ..core.exceptions import BridgeResponseTimeout


# The bridge caps one request so its worst-case confirmation polling remains
# below the normal transport deadline.
PLACE_RECIPE_CHUNK_SIZE = 3
# The dispatcher owns a 30-second command deadline. Wait slightly longer so it
# can return a structured outcome instead of abandoning a sent mutation.
PLACE_RECIPE_TIMEOUT_SECONDS = 32.0


def try_place_recipe(
    client,
    result_id: str,
    placements,
    crafts: int = 1,
    output_per_recipe: int = 1,
) -> bool:
    """Try bounded native crafting without replaying an uncertain mutation."""
    if crafts <= 0:
        return False

    crafts_remaining = crafts
    crafts_completed = 0
    while crafts_remaining > 0:
        chunk = min(PLACE_RECIPE_CHUNK_SIZE, crafts_remaining)
        payload = {
            "placements": [
                {"selector": selector, "grid_slot": slot}
                for selector, slot in placements
            ],
            "expected_output": result_id,
            "expected_count": output_per_recipe,
            "crafts": chunk,
        }
        try:
            response = client.transport.dispatch(
                "place_recipe",
                payload,
                timeout=PLACE_RECIPE_TIMEOUT_SECONDS,
            )
        except BridgeResponseTimeout:
            print(
                f"  [Craft Debug] place_recipe outcome is unknown for {result_id}; "
                "refusing duplicate fallback crafting"
            )
            raise
        except Exception as exc:
            if crafts_completed:
                raise RuntimeError(
                    f"place_recipe stopped after {crafts_completed}/{crafts} "
                    f"confirmed crafts for {result_id}; refusing duplicate fallback"
                ) from exc
            print(f"  [Craft Debug] place_recipe failed for {result_id}: {exc}")
            return False

        data = response.get("data", response) if isinstance(response, dict) else {}
        completed = int(data.get("crafts_completed", 0) or 0)
        if data.get("crafted") and completed == chunk:
            crafts_completed += completed
            crafts_remaining -= completed
            continue

        detail = data.get("error") or (
            response.get("error") if isinstance(response, dict) else None
        )
        if crafts_completed or completed:
            confirmed = crafts_completed + completed
            raise RuntimeError(
                f"place_recipe stopped after {confirmed}/{crafts} confirmed "
                f"crafts for {result_id}: {detail or data or response}; "
                "refusing duplicate fallback"
            )
        print(
            f"  [Craft Debug] place_recipe did not craft {result_id}: "
            f"{detail or data or response}"
        )
        return False

    return True
