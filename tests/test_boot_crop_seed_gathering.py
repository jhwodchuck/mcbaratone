"""Regression coverage for Minecraft 26.2 crop-bootstrap block IDs."""

from types import SimpleNamespace

from baritone_client.automator.phases import boot_sequence
from baritone_client.automator.phases.boot_sequence import BootSequenceHandler


def test_crop_bootstrap_mines_current_short_grass_id(monkeypatch):
    """Seed gathering must target the grass block that exists in 26.2."""
    mine_payloads = []

    class Transport:
        def dispatch(self, route, payload):
            if route == "get_block":
                y = payload["y"]
                return {
                    "id": "minecraft:grass_block"
                    if y == 64
                    else "minecraft:air"
                }
            if route == "mine":
                mine_payloads.append(payload)
            return {}

    handler = BootSequenceHandler()
    handler.state = SimpleNamespace(custom_data={})
    client = SimpleNamespace(transport=Transport())

    monkeypatch.setattr(
        "baritone_client.common.navigation.find_nearby_block",
        lambda _client, block_ids, **_kwargs: (
            (0, 64, 0) if "minecraft:grass_block" in block_ids else None
        ),
    )
    monkeypatch.setattr(
        "baritone_client.common.navigation.goto",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(boot_sequence, "count_item", lambda *_args: 0)
    monkeypatch.setattr(boot_sequence.time, "sleep", lambda _seconds: None)

    assert not handler._plant_crops(client)
    assert mine_payloads == [
        {
            "blocks": ["minecraft:short_grass", "minecraft:tall_grass"],
            "quantity": 9,
        }
    ]
