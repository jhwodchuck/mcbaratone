from types import SimpleNamespace

from baritone_client.common import emergency_food


class DryAnchorTransport:
    def __init__(self, dry_state):
        self.calls = []
        self.dry_state = dry_state

    def dispatch(self, route, payload):
        self.calls.append((route, payload))
        if route == "get_state":
            return dict(self.dry_state)
        return {}


def test_submerged_food_search_returns_to_recent_dry_anchor(monkeypatch):
    """Bot15 drowned after forgetting dry ground beside its home chest."""
    dry_position = (-174, 67, -331)
    dry_state = {
        "health": 19.0,
        "is_pathing": False,
        "block_position": {
            "x": dry_position[0],
            "y": dry_position[1],
            "z": dry_position[2],
        },
    }
    client = SimpleNamespace(transport=DryAnchorTransport(dry_state))
    wet_state = {
        "health": 19.0,
        "is_pathing": False,
        "block_position": {"x": -175, "y": 59, "z": -309},
    }

    monkeypatch.setattr(
        emergency_food,
        "player_is_in_water",
        lambda _client, state: state is wet_state,
    )
    monkeypatch.setattr(
        emergency_food,
        "head_block_is_water",
        lambda _client, state: state is wet_state,
    )
    monkeypatch.setattr(
        emergency_food,
        "_expected_food_search_y",
        lambda _client, position: 63 if position[1] < 63 else 67,
    )
    monkeypatch.setattr(
        emergency_food,
        "_is_dry_food_search_surface",
        lambda _client, position: tuple(position) == dry_position,
    )
    monkeypatch.setattr(
        "baritone_client.common.surface_recovery.reach_dry_surface",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "baritone_client.common.build_site_recovery.excavate_surface_egress",
        lambda *_args, **_kwargs: None,
    )

    assert emergency_food.reach_food_search_surface(client, dry_state)
    assert emergency_food.reach_food_search_surface(client, wet_state)
    assert (
        "goto",
        {"x": -174, "y": 67, "z": -331},
    ) in client.transport.calls
