"""An unloaded chunk must never revoke work the bot already finished.

`get_block` answers "minecraft:void_air" for any coordinate the client has not
loaded. Every world-evidence predicate in the automator is two-valued -- the
block is either what we expect or it is not -- so an unread coordinate scores
exactly like a demolished one.

Two places acted on that:

* `EndGameAutomator._revalidate_completed_objectives` runs on every checkpoint
  restore and calls `PhaseVerifier.verify` for each completed objective. A
  failure dropped the phase from the planner, zeroed its progress and rewrote
  `completed_objectives`. A bot that logged out in a mine, in the Nether, or
  simply 500 blocks from base therefore came back and un-completed its own
  finished objectives, then rebuilt work that was already standing.

* `IncrementalHomestead.invalidate_stale` re-opened verified homestead steps
  the same way, sending the bot to rebuild a house, farm or torch ring that was
  intact but out of render distance.

The rule is that "unknown" leaves prior state untouched. It must NOT be
softened into "unknown means present": for a verifier, wrongly crediting an
objective is worse than wrongly dropping one. These tests pin both directions.
"""

from types import SimpleNamespace

import pytest

from baritone_client.actions.homestead import IncrementalHomestead
from baritone_client.automator.phase_verifier import VerificationResult


UNLOADED = "minecraft:void_air"


# ---------------------------------------------------------------------------
# the verifier's report
# ---------------------------------------------------------------------------
def test_a_verification_result_defaults_to_readable_evidence():
    """Existing constructions must keep meaning "we actually looked"."""
    result = VerificationResult(False, "nope")

    assert result.evidence_unreadable is False


def test_evidence_unreadable_is_carried_on_the_result():
    result = VerificationResult(False, "nope", ("GATE",), evidence_unreadable=True)

    assert result.evidence_unreadable is True


# ---------------------------------------------------------------------------
# the amplifier: revalidation on checkpoint restore
# ---------------------------------------------------------------------------
class _Planner:
    def __init__(self, objectives, completed):
        self.objectives = objectives
        self._completed = set(completed)
        self.restored = None

    def completed_phases(self):
        return set(self._completed)

    def restore(self, valid, runtime=None):
        self.restored = set(valid)

    def runnable(self):
        return []

    def select(self, _runnable):
        return None


def _run_revalidation(verification):
    """Drive _revalidate_completed_objectives with one completed objective."""
    from baritone_client.automator.automator import EndGameAutomator
    from baritone_client.automator.state_manager import Phase

    phase = Phase.BASE_CONSTRUCTION
    objective = SimpleNamespace(phase=phase, requires=())
    planner = _Planner([objective], {phase})
    progress_calls = []

    state = SimpleNamespace(
        # No "verified_objective_completions" entry: this is an unattested
        # legacy completion, which is exactly the case that still reaches the
        # live verifier and could be dropped.
        custom_data={"completed_objectives": [phase.name]},
        has_durable_inventory_observations=True,
        update_progress=lambda value, phase=None: progress_calls.append((phase, value)),
        set_phase=lambda _phase: None,
    )
    automator = SimpleNamespace(
        planner=planner,
        state=state,
        phase_verifier=SimpleNamespace(verify=lambda *_a, **_k: verification),
    )

    EndGameAutomator._revalidate_completed_objectives(automator)
    return planner, progress_calls


def test_an_unreadable_failure_keeps_the_completed_objective():
    """THE bug: a bot away from base wiped its own progression graph."""
    planner, progress_calls = _run_revalidation(
        VerificationResult(
            False, "house blocks missing", ("BASE",), evidence_unreadable=True
        )
    )

    assert progress_calls == [], (
        f"zeroed progress {progress_calls} on coordinates nobody could read"
    )
    assert planner.restored is None or planner.restored, (
        "dropped a completed objective on unreadable evidence"
    )


def test_a_readable_failure_still_revokes_the_objective():
    """The audit must keep working: a genuinely demolished base is not credited.

    This is the direction that must not be softened -- crediting an objective
    that no longer holds is worse than dropping one that does.
    """
    from baritone_client.automator.state_manager import Phase

    planner, progress_calls = _run_revalidation(
        VerificationResult(
            False, "house blocks missing", ("BASE",), evidence_unreadable=False
        )
    )

    assert planner.restored == set(), "kept an objective whose evidence was read and absent"
    assert [phase for phase, _value in progress_calls] == [Phase.BASE_CONSTRUCTION]


# ---------------------------------------------------------------------------
# homestead step invalidation
# ---------------------------------------------------------------------------
def _homestead_helper(block_id):
    client = SimpleNamespace(
        transport=SimpleNamespace(
            dispatch=lambda route, _payload=None: (
                {"id": block_id}
                if route == "get_block"
                else {"block_position": {"x": 0, "y": 64, "z": 0}}
            )
        )
    )
    return IncrementalHomestead(client, SimpleNamespace(custom_data={}), lambda _c: True)


def _verified_homestead():
    return {
        "anchor": [0, 64, 0],
        "steps": {
            name: {"verified": True, "intended": None, "verified_positions": None}
            for name in (
                "dry_anchor", "infrastructure", "micro_farm", "light_perimeter"
            )
        },
    }


def test_unloaded_reads_do_not_reopen_finished_homestead_steps():
    """THE bug: an intact homestead out of render distance was rebuilt."""
    helper = _homestead_helper(UNLOADED)
    homestead = _verified_homestead()

    helper.invalidate_stale(homestead)

    still_verified = [
        name for name, record in homestead["steps"].items() if record["verified"]
    ]
    assert len(still_verified) == 4, (
        f"only {still_verified} survived; an unloaded chunk re-opened finished work"
    )


def test_a_genuinely_destroyed_homestead_step_is_still_reopened():
    """Fail-closed behaviour must survive: real demolition re-opens the step."""
    helper = _homestead_helper("minecraft:air")
    homestead = _verified_homestead()

    helper.invalidate_stale(homestead)

    assert not homestead["steps"]["infrastructure"]["verified"], (
        "kept a verified step whose blocks were read and genuinely gone"
    )
