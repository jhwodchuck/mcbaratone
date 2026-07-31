"""Regression coverage for survival recovery after a bot is relocated."""

from types import SimpleNamespace

from baritone_client.automator.food_recovery_state import (
    get_food_search_anchor,
)


def test_food_search_anchor_follows_reanchored_spawn_home():
    """A pre-placement recovery anchor must not pull a relocated bot backward."""
    state = SimpleNamespace(
        custom_data={
            "phase_payloads": {
                "SPAWN_BOOTSTRAP": {
                    "return_home": {"origin": [-317, 64, -356]}
                }
            },
            "survival_recovery": {
                "food_search_anchor": [-122.0, 65.0, -91.0],
            },
        }
    )

    anchor = get_food_search_anchor(
        None,
        state,
        lambda *_args: {
            "block_position": {"x": -380, "y": 65, "z": -369}
        },
    )

    assert anchor == (-317.0, 64.0, -356.0)
    assert state.custom_data["survival_recovery"]["food_search_anchor"] == [
        -317.0,
        64.0,
        -356.0,
    ]
