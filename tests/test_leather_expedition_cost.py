"""Cost and safety bounds on the leather expedition.

Bot17 reached a barren streak of 1804 on 2026-08-06: 1804 consecutive leather
attempts that gained nothing, in a world where a live survey found no
leather-dropping animal at all. The existing cost control correctly skipped
most passes cheaply, but its fixed every-10th forced retry still sent the bot
on roughly 180 real hunts into that empty world, and it was hunting at 1/4
armor while accumulating 17 deaths in six hours.
"""

from types import SimpleNamespace

import pytest

from baritone_client.automator.phases import leather_supply


class StateStub:
    def __init__(self, streak=0):
        self.custom_data = {leather_supply.NO_GAIN_KEY: streak}


class ClientStub:
    def __init__(self, *, health=20.0, difficulty="easy"):
        self.health = health
        self.transport = SimpleNamespace(
            dispatch=lambda *_a, **_k: {
                "health": self.health,
                "difficulty": difficulty,
            }
        )


def test_retry_cadence_backs_off_as_the_streak_grows():
    """A fixed cadence never stops costing; Bot17 paid it 180 times."""
    early = leather_supply._retry_interval(10)
    mid = leather_supply._retry_interval(200)
    late = leather_supply._retry_interval(1804)

    assert early == leather_supply.FULL_RETRY_EVERY
    assert mid > early, "cadence never widened for a long-barren world"
    assert late > mid


def test_backoff_is_capped_so_rediscovery_never_stops():
    """A world that regains animals must still be found eventually."""
    assert leather_supply._retry_interval(10**6) == leather_supply.MAX_RETRY_INTERVAL


def test_early_stalls_still_probe_promptly():
    """The repair must not blind a bot whose herd is only briefly empty."""
    assert leather_supply._retry_interval(3) == leather_supply.FULL_RETRY_EVERY


def test_long_barren_streak_costs_far_fewer_full_expeditions():
    """Quantify the live failure: 1804 attempts must not mean ~180 hunts."""
    forced = sum(
        1
        for streak in range(1, 1805)
        if streak % leather_supply._retry_interval(streak) == 0
    )
    naive = 1804 // leather_supply.FULL_RETRY_EVERY

    assert forced < naive / 5, (
        f"still forcing {forced} expeditions into an empty world (was {naive})"
    )


def test_naked_bot_defers_the_expedition(monkeypatch):
    """An open hunt at 1/4 armor is a death, not a gathering trip."""
    monkeypatch.setattr(
        "baritone_client.common.inventory.get_equipped_armor",
        lambda _c: {"boots": "minecraft:iron_boots"},
    )

    assert leather_supply.expedition_is_too_dangerous(ClientStub()) is True


def test_peaceful_hunt_does_not_require_combat_armor(monkeypatch):
    monkeypatch.setattr(
        "baritone_client.common.inventory.get_equipped_armor",
        lambda _c: {},
    )

    assert (
        leather_supply.expedition_is_too_dangerous(
            ClientStub(difficulty="peaceful")
        )
        is False
    )


def test_wounded_bot_defers_the_expedition(monkeypatch):
    monkeypatch.setattr(
        "baritone_client.common.inventory.get_equipped_armor",
        lambda _c: {
            "helmet": "minecraft:iron_helmet",
            "chestplate": "minecraft:iron_chestplate",
            "leggings": "minecraft:iron_leggings",
            "boots": "minecraft:iron_boots",
        },
    )

    assert leather_supply.expedition_is_too_dangerous(ClientStub(health=4.0)) is True


def test_equipped_healthy_bot_may_hunt(monkeypatch):
    """The gate must not block a bot that can actually do the job."""
    monkeypatch.setattr(
        "baritone_client.common.inventory.get_equipped_armor",
        lambda _c: {
            "helmet": "minecraft:iron_helmet",
            "chestplate": "minecraft:iron_chestplate",
            "leggings": "minecraft:iron_leggings",
            "boots": "minecraft:iron_boots",
        },
    )

    assert leather_supply.expedition_is_too_dangerous(ClientStub()) is False


def test_missing_telemetry_does_not_block_the_hunt(monkeypatch):
    """Unknown equipment must fail open, not strand the objective."""

    def boom(_c):
        raise RuntimeError("bridge unavailable")

    monkeypatch.setattr(
        "baritone_client.common.inventory.get_equipped_armor", boom
    )

    assert leather_supply.expedition_is_too_dangerous(ClientStub()) is False


def test_local_exhaustion_expands_the_search_ring():
    """1804 attempts inside one ~300-block box is not evidence of an empty world.

    Bot17 stood in a savanna -- prime cow biome -- with 22 hostiles and zero
    passive mobs within 128 blocks. Passive mobs spawn at chunk generation and
    do not repopulate, so the fleet's long-inhabited region is permanently
    barren while the wider world is not.
    """
    state = StateStub(streak=leather_supply.RING_EXHAUSTED_STREAK)
    before = leather_supply.expedition_distance(state)

    assert leather_supply.escalate_ring_if_exhausted(state) is True
    assert leather_supply.expedition_distance(state) > before


def test_escalation_resets_the_streak_so_the_wider_search_actually_runs():
    """Carrying the old ring's barren evidence forward would suppress the new
    search before it ever ran -- the backoff would gate it to 1-in-320."""
    state = StateStub(streak=1804)

    leather_supply.escalate_ring_if_exhausted(state)

    assert state.custom_data[leather_supply.NO_GAIN_KEY] == 0
    assert leather_supply._retry_interval(0) == leather_supply.FULL_RETRY_EVERY


def test_sector_rotation_follows_the_ring_outward():
    """Raising only the distance cap still re-sweeps the same offsets."""
    local = StateStub()
    assert leather_supply.sector_scale(local) == 1.0

    escalated = StateStub(streak=leather_supply.RING_EXHAUSTED_STREAK)
    leather_supply.escalate_ring_if_exhausted(escalated)

    assert leather_supply.sector_scale(escalated) > 1.0


def test_no_escalation_before_the_local_ring_is_proven_barren():
    """A brief empty patch must not fling the bot across the world."""
    state = StateStub(streak=1)

    assert leather_supply.escalate_ring_if_exhausted(state) is False
    assert leather_supply.expedition_distance(state) == leather_supply.EXPEDITION_RINGS[0]


def test_escalation_is_capped():
    """Expansion must stop somewhere; unbounded treks are their own failure."""
    state = StateStub()
    for _ in range(len(leather_supply.EXPEDITION_RINGS) + 3):
        state.custom_data[leather_supply.NO_GAIN_KEY] = (
            leather_supply.RING_EXHAUSTED_STREAK
        )
        leather_supply.escalate_ring_if_exhausted(state)

    assert leather_supply.expedition_distance(state) == leather_supply.EXPEDITION_RINGS[-1]


def test_wool_hunt_escalates_on_its_own_exhaustion():
    """Sheep are passive mobs too; the bed hunt carried the same 160-block cap.

    It also had no barren-streak tracking at all, so it could not even detect
    that its region was hunted out.
    """
    state = StateStub()
    for _ in range(leather_supply.RING_EXHAUSTED_STREAK):
        leather_supply.record_attempt(
            state, gained=False, streak_key=leather_supply.WOOL_NO_GAIN_KEY
        )

    promoted = leather_supply.escalate_ring_if_exhausted(
        state,
        ring_key=leather_supply.WOOL_RING_KEY,
        streak_key=leather_supply.WOOL_NO_GAIN_KEY,
        label="wool",
    )

    assert promoted is True
    assert (
        leather_supply.expedition_distance(
            state, ring_key=leather_supply.WOOL_RING_KEY
        )
        > leather_supply.EXPEDITION_RINGS[0]
    )


def test_wool_and_leather_rings_are_independent():
    """Exhausting one objective must not fling the other across the world."""
    state = StateStub()
    for _ in range(leather_supply.RING_EXHAUSTED_STREAK):
        leather_supply.record_attempt(
            state, gained=False, streak_key=leather_supply.WOOL_NO_GAIN_KEY
        )
    leather_supply.escalate_ring_if_exhausted(
        state,
        ring_key=leather_supply.WOOL_RING_KEY,
        streak_key=leather_supply.WOOL_NO_GAIN_KEY,
        label="wool",
    )

    assert (
        leather_supply.expedition_distance(state)
        == leather_supply.EXPEDITION_RINGS[0]
    ), "leather ring moved when only wool was exhausted"


def test_wool_gain_resets_its_own_streak():
    state = StateStub()
    leather_supply.record_attempt(
        state, gained=False, streak_key=leather_supply.WOOL_NO_GAIN_KEY
    )
    leather_supply.record_attempt(
        state, gained=True, streak_key=leather_supply.WOOL_NO_GAIN_KEY
    )

    assert state.custom_data[leather_supply.WOOL_NO_GAIN_KEY] == 0


def test_refusing_the_hunt_also_triggers_a_rearm(monkeypatch):
    """A gate that only refuses is a livelock, not a safety feature.

    Bot17 stopped dying the moment the equipment gate landed -- and stopped
    progressing with it, holding 10 unspent iron ingots because nothing in
    ENCHANTING_PIPELINE ever equipped it. The refusal must drive the remedy.
    """
    from baritone_client.automator.phases import enchanting

    handler = enchanting.EnchantingPipelineHandler()
    state = SimpleNamespace(custom_data={})
    client = ClientStub()

    monkeypatch.setattr(enchanting, "count_item", lambda *_a, **_k: 0)
    monkeypatch.setattr(handler, "_withdraw_at_home", lambda *_a, **_k: 0)
    monkeypatch.setattr(
        enchanting.leather_supply, "search_is_futile", lambda *_a, **_k: False
    )
    monkeypatch.setattr(
        enchanting.leather_supply,
        "escalate_ring_if_exhausted",
        lambda *_a, **_k: False,
    )
    monkeypatch.setattr(
        enchanting.leather_supply,
        "expedition_is_too_dangerous",
        lambda *_a, **_k: True,
    )

    rearmed = []
    monkeypatch.setattr(
        enchanting.combat_readiness,
        "ensure_combat_readiness",
        lambda *_a, **_k: rearmed.append(True) or False,
    )

    assert handler._gather_leather(client, state) is False
    assert rearmed, "gate refused the hunt without ever trying to rearm"
