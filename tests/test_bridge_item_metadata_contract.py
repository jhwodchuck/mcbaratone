from baritone_client.automator.phases.trading import (
    _verified_inventory_enchantments,
)


def test_bridge_stored_enchantment_schema_is_accepted_by_trading_evidence():
    items = [
        {
            "slot": 4,
            "id": "minecraft:enchanted_book",
            "count": 1,
            "stored_enchantments": [
                {"id": "minecraft:mending", "level": 1},
            ],
            "components": {
                "minecraft:stored_enchantments": [
                    {"id": "minecraft:mending", "level": 1},
                ],
            },
        }
    ]

    assert _verified_inventory_enchantments(items) == {"mending"}


def test_bridge_merchant_result_schema_carries_truthful_book_identity():
    merchant_offer = {
        "index": 2,
        "payment_a_slot": 0,
        "payment_b_slot": 1,
        "result_slot": 2,
        "cost_a": {"id": "minecraft:emerald", "count": 10},
        "cost_b": {"id": "minecraft:book", "count": 1},
        "result": {
            "id": "minecraft:enchanted_book",
            "count": 1,
            "stored_enchantments": [
                {"id": "minecraft:mending", "level": 1},
            ],
        },
        "uses": 0,
        "max_uses": 12,
        "available": True,
    }

    assert merchant_offer["result_slot"] == 2
    assert merchant_offer["available"]
    assert _verified_inventory_enchantments([merchant_offer["result"]]) == {"mending"}
