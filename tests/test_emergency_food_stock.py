"""Banked emergency food must be reachable, not just edible.

An RCON census of every verified container found 199 rotten flesh in fleet
storage while Bot16 sat at food 0 and health 9.8. Eating rotten flesh was
already supported and already ranked last, so a bot only touches it when
nothing better is carried -- but withdrawal asked only for good food, so the
199 units were invisible to a starving bot standing near them.

An earlier census of mine missed them entirely because it filtered food by an
ad-hoc name list. The lesson is the filter, not the item: count against the
real edible set, or the number is wrong in the safe-looking direction.
"""

from baritone_client.automator.food_recovery_state import _STORED_FOOD_TARGETS
from baritone_client.common.emergency_food import EMERGENCY_FOOD_ITEMS


def test_banked_rotten_flesh_can_be_withdrawn():
    """The fleet had 199 units it could never fetch."""
    assert "minecraft:rotten_flesh" in _STORED_FOOD_TARGETS


def test_rotten_flesh_is_eaten_only_after_every_better_food():
    """It causes Hunger, so it must never displace real food."""
    order = list(EMERGENCY_FOOD_ITEMS)
    assert order[-1] == "minecraft:rotten_flesh", order[-3:]


def test_withdrawable_food_is_actually_edible():
    """A target the eating path cannot use would send a starving bot for nothing."""
    edible = set(EMERGENCY_FOOD_ITEMS)
    for item_id in _STORED_FOOD_TARGETS:
        assert item_id in edible, f"{item_id} is withdrawn but never eaten"


def test_spider_eyes_are_never_a_food_target():
    """They poison. The bot most likely to reach for them is the wounded one."""
    assert "minecraft:spider_eye" not in _STORED_FOOD_TARGETS
    assert "minecraft:spider_eye" not in EMERGENCY_FOOD_ITEMS


def test_good_food_still_outranks_the_last_resort():
    order = list(EMERGENCY_FOOD_ITEMS)
    for staple in ("minecraft:bread", "minecraft:cooked_beef", "minecraft:apple"):
        assert order.index(staple) < order.index("minecraft:rotten_flesh")
