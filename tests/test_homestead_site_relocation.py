"""A homestead must abandon a site its steps can never satisfy.

Re-homing already covered the anchor the bot could not *reach*. This is the
other half: an anchor it reaches fine, on ground that cannot support the work.

On 2026-08-08 the A1 bot re-homed onto bare mountain at Y=140 and micro_farm
reported "no reachable soil or water found" on every cycle forever -- the step
searches 20 blocks for soil and 16 for water, and neither existed. The terrain
does not change between attempts, so retrying was pure cost.
"""

from types import SimpleNamespace

import pytest

from baritone_client.actions import homestead_site
from baritone_client.actions.homestead import (
    ORDERED_HOMESTEAD_STEPS,
    IncrementalHomestead,
)
from baritone_client.actions.homestead_site import (
    MAX_SITE_RELOCATIONS,
    MAX_SITE_RELOCATION_HARD_CAP,
    MAX_SITE_STEP_FAILURES,
    SITE_RELOCATIONS,
    SITE_STEP_FAILURES,
)


class _State:
    def __init__(self):
        self.custom_data = {}
        self.payloads = []

    def record_phase_payload(self, phase, payload):
        self.payloads.append((phase, payload))


def _homestead_helper(position=(10, 70, 10), dry=True):
    helper = IncrementalHomestead.__new__(IncrementalHomestead)
    helper.state = _State()
    helper.client = SimpleNamespace()
    helper.current_position = lambda: position
    helper._dry_ground = lambda _pos: dry
    helper.ordered_steps = lambda: ORDERED_HOMESTEAD_STEPS
    return helper


def _progress():
    return {
        "anchor": (0, 64, 0),
        "steps": {
            name: {"verified": False, "evidence": None} for name in ORDERED_HOMESTEAD_STEPS
        },
    }


def test_one_stall_is_not_enough_to_abandon_a_site():
    """Transient failures must not move the base."""
    helper = _homestead_helper()
    progress = _progress()

    assert homestead_site.note_step_stalled(progress, "micro_farm") is False


def test_repeated_identical_stalls_condemn_the_site():
    """THE live failure: micro_farm cannot find soil that is not there."""
    helper = _homestead_helper()
    progress = _progress()

    results = [
        homestead_site.note_step_stalled(progress, "micro_farm")
        for _ in range(MAX_SITE_STEP_FAILURES)
    ]

    assert results[-1] is True, results
    assert results[:-1] == [False] * (MAX_SITE_STEP_FAILURES - 1)


def test_progress_resets_the_stall_history():
    """A step that eventually works is not evidence against the site."""
    helper = _homestead_helper()
    progress = _progress()

    homestead_site.note_step_stalled(progress, "micro_farm")
    homestead_site.note_step_stalled(progress, "micro_farm")
    homestead_site.clear_step_stall(progress, "micro_farm")

    assert homestead_site.note_step_stalled(progress, "micro_farm") is False


def test_stalls_are_counted_per_step_not_globally():
    """Two different steps each failing once is not a bad site."""
    helper = _homestead_helper()
    progress = _progress()

    for step in ("micro_farm", "wood_reserve", "stone_reserve"):
        assert homestead_site.note_step_stalled(progress, step) is False


def test_relocation_budget_is_bounded():
    """Past the soft budget the bot must keep moving, bounded by the hard cap.

    Jason's directive: the bot needs to get to the dragon from any spawn, so a
    barren site must not park the run. After MAX_SITE_RELOCATIONS the bot keeps
    relocating (note_step_stalled stays True) until MAX_SITE_RELOCATION_HARD_CAP,
    where it finally holds to avoid a genuinely endless walk.
    """
    helper = _homestead_helper()
    progress = _progress()
    progress[SITE_RELOCATIONS] = MAX_SITE_RELOCATIONS

    # The step-failure gate must trip first (each call advances one stall).
    for _ in range(MAX_SITE_STEP_FAILURES - 1):
        assert homestead_site.note_step_stalled(progress, "micro_farm") is False
    # Next call crosses the gate; past the soft budget it stays True (hard cap
    # not reached).
    for _ in range(MAX_SITE_STEP_FAILURES + 2):
        assert homestead_site.note_step_stalled(progress, "micro_farm") is True

    # At the hard cap, finally hold.
    progress[SITE_RELOCATIONS] = homestead_site.MAX_SITE_RELOCATION_HARD_CAP
    assert homestead_site.note_step_stalled(progress, "micro_farm") is False


def test_relocating_reopens_every_step(monkeypatch):
    """Old evidence describes the abandoned site, so it cannot stand."""
    helper = _homestead_helper(position=(120, 68, -40))
    progress = _progress()
    for name in ORDERED_HOMESTEAD_STEPS:
        progress["steps"][name] = {"verified": True, "evidence": "live_check"}

    monkeypatch.setattr(
        "baritone_client.common.build_site_recovery.relocate_build_site_search",
        lambda *_a, **_k: True,
    )

    assert homestead_site.relocate_homestead(
        SimpleNamespace(), progress, "micro_farm", helper
    ) is True
    assert progress["anchor"] == (120, 68, -40)
    assert progress[SITE_RELOCATIONS] == 1
    assert progress[SITE_STEP_FAILURES] == {}
    for name in ORDERED_HOMESTEAD_STEPS:
        assert progress["steps"][name]["verified"] is False, name
        assert progress["steps"][name]["evidence"] == "site_relocated"


def test_a_failed_search_keeps_the_old_site(monkeypatch):
    """Finding nowhere better must not blank the anchor."""
    helper = _homestead_helper()
    progress = _progress()
    progress["steps"]["wood_reserve"] = {"verified": True, "evidence": "live"}

    monkeypatch.setattr(
        "baritone_client.common.build_site_recovery.relocate_build_site_search",
        lambda *_a, **_k: False,
    )

    assert homestead_site.relocate_homestead(
        SimpleNamespace(), progress, "micro_farm", helper
    ) is False
    assert progress["anchor"] == (0, 64, 0)
    assert progress["steps"]["wood_reserve"]["verified"] is True
    # The attempt still spends budget -- see the barren-region test below for
    # why an outcome-gated counter never bounds anything.
    assert progress[SITE_RELOCATIONS] == 1


def test_heading_move_fallback_relocates_when_search_finds_nothing(monkeypatch):
    """When the build-site search can't move the bot off a barren site, the
    camp-break heading move relocates it instead of parking."""
    helper = _homestead_helper(position=(120, 68, -40))
    progress = _progress()
    for name in ORDERED_HOMESTEAD_STEPS:
        progress["steps"][name] = {"verified": True, "evidence": "live_check"}

    monkeypatch.setattr(
        "baritone_client.common.build_site_recovery.relocate_build_site_search",
        lambda *_a, **_k: False,  # no better dry build site in range
    )
    # break_camp reports a successful physical move.
    monkeypatch.setattr(
        "baritone_client.automator.camp_breaker.break_camp",
        lambda _client, _state: True,
    )

    assert homestead_site.relocate_homestead(
        SimpleNamespace(), progress, "micro_farm", helper
    ) is True
    assert progress["anchor"] == (120, 68, -40)
    assert progress[SITE_RELOCATIONS] == 1
    assert progress[SITE_STEP_FAILURES] == {}
    for name in ORDERED_HOMESTEAD_STEPS:
        assert progress["steps"][name]["verified"] is False, name
        assert progress["steps"][name]["evidence"] == "site_relocated"


def test_a_barren_region_eventually_stops_retrying(monkeypatch):
    """Live on the A1 server 2026-08-08: a bot stuck on bare mountain at

    Y=140 relocated on "move 1/3" every single cycle forever, because the
    relocation counter only advanced when a search actually found somewhere
    better. Nothing in this test's search radius is ever better, so the
    counter must still climb.

    Jason's directive supersedes the old "stop retrying" intent: the bot must
    keep moving to reach the dragon from any spawn. After the old soft budget
    the bot keeps authorising relocation (bounded by the hard cap) instead of
    parking on a step its ground can never satisfy.
    """
    helper = _homestead_helper()
    progress = _progress()

    monkeypatch.setattr(
        "baritone_client.common.build_site_recovery.relocate_build_site_search",
        lambda *_a, **_k: False,
    )
    # No transport on the test client, so the break_camp heading fallback also
    # fails to move -- relocate_homestead returns False but still spends budget.
    for _ in range(MAX_SITE_RELOCATIONS):
        assert homestead_site.relocate_homestead(
            SimpleNamespace(), progress, "micro_farm", helper
        ) is False
    assert progress[SITE_RELOCATIONS] == MAX_SITE_RELOCATIONS

    # Past the soft budget the bot keeps being authorised to move, up to the
    # hard cap.
    for _ in range(MAX_SITE_STEP_FAILURES):
        result = homestead_site.note_step_stalled(progress, "micro_farm")
    assert result is True
    progress[SITE_RELOCATIONS] = homestead_site.MAX_SITE_RELOCATION_HARD_CAP
    assert homestead_site.note_step_stalled(progress, "micro_farm") is False


def test_relocation_refuses_non_dry_ground(monkeypatch):
    """Landing in water is not an improvement on bare mountain."""
    helper = _homestead_helper(position=(50, 62, 50), dry=False)
    progress = _progress()

    monkeypatch.setattr(
        "baritone_client.common.build_site_recovery.relocate_build_site_search",
        lambda *_a, **_k: True,
    )

    assert homestead_site.relocate_homestead(
        SimpleNamespace(), progress, "micro_farm", helper
    ) is False
    assert progress["anchor"] == (0, 64, 0)


def test_a_search_that_raises_does_not_break_the_phase(monkeypatch):
    """Relocation is best-effort; its failure must not kill the run."""
    helper = _homestead_helper()
    progress = _progress()

    def boom(*_a, **_k):
        raise RuntimeError("bridge stalled mid-search")

    monkeypatch.setattr(
        "baritone_client.common.build_site_recovery.relocate_build_site_search", boom
    )

    assert homestead_site.relocate_homestead(
        SimpleNamespace(), progress, "micro_farm", helper
    ) is False


def test_stall_state_survives_the_load_rebuild():
    """load() drops unknown keys -- the trap that made the first re-home fix a no-op.

    Without carry-through the counter resets every cycle and can never reach
    its threshold, so the site is never condemned no matter how long it stalls.
    """
    helper = IncrementalHomestead.__new__(IncrementalHomestead)
    helper.state = _State()
    helper.state.custom_data["homestead"] = {
        "anchor": [5, 70, 5],
        "steps": {},
        SITE_STEP_FAILURES: {"micro_farm": 2},
        SITE_RELOCATIONS: 1,
    }

    reloaded = helper.load()

    assert reloaded[SITE_STEP_FAILURES] == {"micro_farm": 2}
    assert reloaded[SITE_RELOCATIONS] == 1


def test_waive_step_only_fires_at_waive_threshold():
    """A waivable step is not waived until enough relocations have been tried
    that the site has proven it cannot support it.

    Below the threshold the site may still be relocated to better ground, so
    waiving early would forfeit an actually-buildable site.
    """
    helper = _homestead_helper()
    progress = _progress()

    # Below the waive threshold: not waived.
    progress[SITE_RELOCATIONS] = homestead_site.MAX_SITE_RELOCATIONS  # 3
    assert homestead_site.waive_step(progress, helper, "micro_farm") is False
    assert not progress["steps"]["micro_farm"].get("degraded")

    # At the waive threshold: waived.
    progress[SITE_RELOCATIONS] = homestead_site.MAX_SITE_RELOCATIONS_BEFORE_WAIVE
    assert homestead_site.waive_step(progress, helper, "micro_farm") is True
    record = progress["steps"]["micro_farm"]
    assert record.get("degraded") is True
    assert record.get("verified") is True
    assert record.get("evidence") == "waived_unbuildable_site"
    # Idempotent: already waived.
    assert homestead_site.waive_step(progress, helper, "micro_farm") is False


def test_non_waivable_steps_never_waive():
    """Load-bearing steps (e.g. wood_reserve) are never waived."""
    helper = _homestead_helper()
    progress = _progress()
    progress[SITE_RELOCATIONS] = homestead_site.MAX_SITE_RELOCATION_HARD_CAP

    assert homestead_site.waive_step(progress, helper, "wood_reserve") is False
    assert "degraded" not in progress["steps"]["wood_reserve"]


def test_waived_step_is_skipped_by_next_step():
    """A degraded step no longer blocks onward progression."""
    helper = _homestead_helper()
    progress = _progress()
    # micro_farm is the only unverified step left; waive it.
    for name in ORDERED_HOMESTEAD_STEPS:
        progress["steps"][name]["verified"] = True
    progress["steps"]["micro_farm"]["verified"] = False
    progress["steps"]["micro_farm"]["degraded"] = True

    assert helper.next_step(progress) is None  # nothing blocks the run


def test_invalidate_stale_does_not_reopen_a_degraded_step():
    """invalidate_stale must leave a waived step alone or it deadlocks again."""
    helper = _homestead_helper()
    progress = _progress()
    progress["steps"]["micro_farm"]["degraded"] = True
    progress["steps"]["micro_farm"]["verified"] = True
    # Live farm check would fail (no farm), but degraded steps are exempt.
    helper._live_farm = lambda: False

    helper.invalidate_stale(progress)
    assert progress["steps"]["micro_farm"]["verified"] is True
