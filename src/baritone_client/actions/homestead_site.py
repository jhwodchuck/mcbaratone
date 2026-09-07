"""Judging when a homestead site is unsuitable, and abandoning it.

Re-homing (see `ANCHOR_UNREACHABLE` in `homestead.py`) covers an anchor the bot
cannot *reach*. This module covers the other half: an anchor it reaches fine,
on ground that cannot support the work.

On 2026-08-08 a bot re-homed onto bare mountain at Y=140 and `micro_farm`
reported "no reachable soil or water found" on every cycle indefinitely. That
step searches 20 blocks for soil and 16 for water; neither existed, and terrain
does not change between attempts, so every retry was guaranteed to fail
identically. Nothing in the homestead ever reconsidered the site itself.

The rule this encodes is the one that generalises: repeated identical failure at
a reachable location is evidence about the location, not about luck. Count the
stalls, then move -- bounded, so a genuinely barren region ends in a visible
failure rather than a bot walking the world forever.
"""

from __future__ import annotations

from typing import Any, Mapping

#: Keys persisted on the homestead dict. Both must be carried through
#: `IncrementalHomestead.load()`, which rebuilds from a fixed key list and
#: silently drops anything it does not name -- the trap that made an earlier
#: re-home fix a no-op, because the counter reset every cycle and could never
#: reach its threshold.
SITE_STEP_FAILURES = "site_step_failures"
SITE_RELOCATIONS = "site_relocations"

#: Consecutive no-progress results for one step at one anchor before the site
#: is judged unsuitable. Deliberately small: these failures are deterministic,
#: so extra retries buy nothing but delay.
MAX_SITE_STEP_FAILURES = 3
#: Whole-base moves allowed per checkpoint.
MAX_SITE_RELOCATIONS = 3
#: Hard ceiling on relocation attempts even after the soft budget is spent. The
#: soft budget (MAX_SITE_RELOCATIONS) is a signal, not a parking brake: a bot on
#: genuinely barren ground must keep moving rather than camp forever on a step
#: its location can never satisfy (Jason: "the bot needs to move"). past the
#: soft budget the bot still relocates, but a runaway walk on world-spanning
#: barren terrain is still capped here so the failure stays visible.
MAX_SITE_RELOCATION_HARD_CAP = 25
#: Relocations before a *waivable* step (e.g. micro_farm) is waived so the run
#: can proceed toward the dragon. Waivable steps are convenience food sources
#: (farming; the bot can eat from hunting/mobs), so gating the whole run on them
#: for the full hard-cap duration would stall the dragon goal for hours on a
#: large barren biome. Load-bearing steps ignore this and are bounded only by
#: MAX_SITE_RELOCATION_HARD_CAP.
MAX_SITE_RELOCATIONS_BEFORE_WAIVE = 6


#: Steps that may never move the base, because moving cannot fix them and
#: relocating re-opens every other step -- so letting them relocate discards a
#: nearly-finished homestead to solve a problem the new site has too.
#:
#: plank_reserve, charcoal_supply and torch_supply need carried items and a
#: workstation, never different terrain.
#:
#: light_perimeter is here for a sharper reason: relocating makes it strictly
#: worse. Its dominant failure is running out of torches -- it already re-opens
#: torch_supply, which is the remedy that works -- and terrain is rarely fatal
#: because _ground_adjusted_ring drops columns it cannot seat. Moving discards
#: the torches, fuel and workstations it was about to use.
#:
#: Both cases are observed, not theoretical. On the A1 server 2026-08-10 the
#: homestead reached 8 of 9 steps verified; light_perimeter stalled three times
#: for want of torches and relocated the base from (-290, 69, 94) to
#: (-352, 52, 93), re-opening all eight and abandoning the farm and
#: infrastructure it had just built.
NEVER_RELOCATE_STEPS = frozenset(
    {"plank_reserve", "charcoal_supply", "torch_supply", "light_perimeter"}
)


def note_step_stalled(homestead: dict[str, Any], step_name: str) -> bool:
    """Count a no-progress step result; True when the site looks unsuitable.

    The counter resets whenever the step finally progresses (see
    `clear_step_stall`), so an intermittently-blocked step -- mob interference,
    one bad path -- never accumulates its way to a spurious relocation.
    """
    failures = homestead.get(SITE_STEP_FAILURES)
    if not isinstance(failures, dict):
        failures = {}
        homestead[SITE_STEP_FAILURES] = failures
    count = int(failures.get(step_name, 0) or 0) + 1
    failures[step_name] = count
    if step_name in NEVER_RELOCATE_STEPS:
        # Counted for visibility, but never grounds to move: the remedy is
        # more materials, which the step's own re-opening of its supplier
        # already arranges.
        return False
    if count < MAX_SITE_STEP_FAILURES:
        return False
    relocations = int(homestead.get(SITE_RELOCATIONS, 0) or 0)
    if relocations >= MAX_SITE_RELOCATION_HARD_CAP:
        # Hard safety ceiling reached. Deliberately not raising: the caller
        # still records progress and fails the step normally, so an operator
        # sees a stuck phase rather than a bot roaming the world in search of
        # better ground.
        print(
            f"  Site unsuitable for {step_name}; relocation hard cap "
            f"({MAX_SITE_RELOCATION_HARD_CAP}) reached; holding"
        )
        return False
    if relocations >= MAX_SITE_RELOCATIONS:
        # Past the soft budget the bot keeps moving: a barren site must not
        # permanently camp a step it can never satisfy. Print so the move is
        # observable, still bounded by the hard cap above.
        print(
            f"  Site unsuitable for {step_name}; past soft relocation budget "
            f"({relocations}/{MAX_SITE_RELOCATIONS}); continuing to relocate"
        )
    return True


def clear_step_stall(homestead: dict[str, Any], step_name: str) -> None:
    """Forget the stall history for a step that made progress."""
    failures = homestead.get(SITE_STEP_FAILURES)
    if isinstance(failures, dict):
        failures.pop(step_name, None)


#: BOOT steps that may be waived (degraded) once the site has proven it can
#: never satisfy them, so the run can still reach the dragon. Food is the
#: waivable one: a farm is a convenience renewable source, and a bot that
#: cannot farm can still eat from hunting/mobs. Load-bearing steps (wood,
#: stone, charcoal, torches, lighting, a dry anchor) stay mandatory.
WAIVABLE_STEPS = frozenset({"micro_farm"})


def waive_step(homestead: dict[str, Any], helper: Any, step_name: str) -> bool:
    """Mark a waivable step degraded so the run can proceed past it.

    Callers use this only after the site has exhausted relocation attempts
    (the hard cap) for a step it can never satisfy -- ``waive_step`` itself
    re-checks that the relocation hard cap has been reached so it never fires
    prematurely. The step is flagged ``degraded`` (and ``verified``), which
    makes ``next_step`` skip it -- but ``invalidate_stale`` leaves it alone
    because its live check is expected to fail. Returns True when the step was
    actually waived.
    """
    if step_name not in WAIVABLE_STEPS:
        return False
    relocations = int(homestead.get(SITE_RELOCATIONS, 0) or 0)
    if relocations < MAX_SITE_RELOCATIONS_BEFORE_WAIVE:
        return False
    record = helper.step(homestead, step_name)
    if record.get("degraded"):
        return False
    record.update(
        degraded=True,
        verified=True,
        evidence=f"waived_unbuildable_site",
    )
    print(
        f"  Waived BOOT step '{step_name}' (site cannot support it after "
        f"{MAX_SITE_RELOCATIONS_BEFORE_WAIVE} relocations); run proceeds without it"
    )
    return True



def carry_site_state(progress: dict[str, Any], raw: Mapping[str, Any]) -> None:
    """Preserve stall/relocation state across a `load()` rebuild."""
    failures = raw.get(SITE_STEP_FAILURES)
    if isinstance(failures, Mapping):
        carried = {}
        for name, count in failures.items():
            try:
                carried[str(name)] = int(count)
            except (TypeError, ValueError):
                continue
        if carried:
            progress[SITE_STEP_FAILURES] = carried
    relocations = raw.get(SITE_RELOCATIONS)
    if relocations:
        try:
            progress[SITE_RELOCATIONS] = int(relocations)
        except (TypeError, ValueError):
            pass


def relocate_homestead(
    client: Any,
    homestead: dict[str, Any],
    step_name: str,
    helper: Any,
) -> bool:
    """Abandon an unsuitable site and re-anchor where the step can work.

    Reuses the bounded surface search BASE_CONSTRUCTION already uses to escape a
    dead build site rather than inventing a second relocation strategy.
    """
    from ..common.build_site_recovery import relocate_build_site_search
    from ..common.home_site import suitable_home_site

    relocations = int(homestead.get(SITE_RELOCATIONS, 0) or 0) + 1
    # Persisted up front, against the attempt rather than the outcome. A
    # search that finds nowhere better still spends budget -- otherwise a
    # genuinely barren region (nothing better within the search radius, not a
    # transient failure) retries every stall forever, exactly the unbounded
    # walk this module exists to prevent. Live on the A1 server 2026-08-08:
    # a bot stuck at Y=140 on bare mountain relocated on "move 1/3" every
    # single cycle because the counter only advanced on success.
    homestead[SITE_RELOCATIONS] = relocations
    print(
        f"  Site unsuitable for {step_name} after {MAX_SITE_STEP_FAILURES} "
        f"stalled attempts; relocating the homestead "
        f"(move {relocations}/{MAX_SITE_RELOCATIONS})"
    )
    try:
        moved = relocate_build_site_search(
            client, attempt=relocations, candidate_validator=suitable_home_site,
        )
    except Exception as error:  # search is best-effort; never kill the run
        moved = False
        print(f"  Homestead relocation search failed ({error})")
    if not moved:
        # The build-site search found no better *dry buildable* ground in
        # range (e.g. on a bare mountain). That is not permission to park: the
        # bot must keep moving off the barren spot (Jason: "the bot needs to
        # move"). Fall back to the camp-break heading move, which physically
        # relocates by a fixed heading + distance using recovery navigation.
        print("  Homestead relocation: no better build site in range; taking a heading move")
        from ..automator import camp_breaker

        try:
            moved = camp_breaker.break_camp(client, helper.state)
        except Exception as error:  # best-effort; never kill the run
            moved = False
            print(f"  Homestead heading move failed ({error})")
        if not moved:
            print("  Homestead relocation could not move the bot; holding this cycle")
            return False

    new_anchor = helper.current_position()
    if not helper._dry_ground(new_anchor) or not suitable_home_site(client, new_anchor):
        print(f"  Exploration at {new_anchor} did not prove a suitable home; keeping old site")
        return False

    homestead["anchor"] = new_anchor
    homestead[SITE_STEP_FAILURES] = {}
    homestead.pop("anchor_unreachable", None)
    homestead.pop("anchor_unreachable_at", None)
    helper.state.custom_data["homestead_anchor"] = new_anchor
    # Site-specific evidence describes the abandoned location, so re-open every
    # step. Supplies already gathered stay in the inventory and make the repeat
    # cheap; leaving steps verified would claim a dry anchor and a lit perimeter
    # that exist somewhere the bot no longer lives.
    for name in helper.ordered_steps():
        record = helper.step(homestead, name)
        record["verified"] = False
        record["evidence"] = "site_relocated"
        # Coordinates describe the abandoned site, so they must go too.
        # light_perimeter reuses a stored `intended` ring and only rebases its
        # Y, keeping the old X/Z -- after a move that lights empty air around
        # the *previous* base while the new one stays dark. Live 2026-08-10:
        # torch targets near (-334, 79, 111) against an anchor at
        # (-352, 52, 93), rejected one by one as "no solid support face".
        record.pop("intended", None)
        record.pop("verified_positions", None)
    print(f"  Homestead relocated to {new_anchor}")
    return True


def read_block_counting_unloaded(client: Any, position: Any) -> tuple[str, bool]:
    """Read one block id, and report whether the read proved anything.

    ``void_air`` means the chunk was never loaded and an empty id means the
    read itself failed. Neither shows the block is gone, but every homestead
    check is two-valued, so an unread coordinate scores exactly like a
    demolished one -- which sent bots to rebuild houses, farms and torch rings
    that were intact and merely out of render distance.
    """
    from ..common.terraform_verify import classify_block

    try:
        value = str(
            client.transport.dispatch(
                "get_block",
                {"x": int(position[0]), "y": int(position[1]), "z": int(position[2])},
            ).get("id", "")
        )
    except Exception:
        value = ""
    return value, classify_block(value) == "unknown"


def screen_rehome(helper: Any, homestead: dict[str, Any], current: Any) -> Any:
    """Keep emergency shelter separate from committing a replacement home."""
    from ..common.home_site import suitable_home_site
    from ..common.tasks import ProgressRecoveryRequired

    if not suitable_home_site(helper.client, current):
        attempts = int(homestead.get(SITE_RELOCATIONS, 0) or 0)
        if attempts >= MAX_SITE_RELOCATION_HARD_CAP or not relocate_homestead(
            helper.client, homestead, "dry_anchor", helper,
        ):
            helper.record(homestead)
            raise ProgressRecoveryRequired(
                "no surveyed replacement home; retained existing anchor and infrastructure"
            )
        return helper.current_position()
    # Old site evidence cannot be carried to a newly selected anchor.
    for name in helper.ordered_steps():
        record = helper.step(homestead, name)
        record.update(verified=False, evidence="site_relocated")
        record.pop("intended", None)
        record.pop("verified_positions", None)
    return current
