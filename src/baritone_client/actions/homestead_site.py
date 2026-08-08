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
    if count < MAX_SITE_STEP_FAILURES:
        return False
    relocations = int(homestead.get(SITE_RELOCATIONS, 0) or 0)
    if relocations >= MAX_SITE_RELOCATIONS:
        # Deliberately not raising. The caller still records progress and fails
        # the step normally, so an operator sees a stuck phase rather than a bot
        # roaming in search of better ground.
        print(
            f"  Site unsuitable for {step_name} but relocation budget is spent "
            f"({relocations}/{MAX_SITE_RELOCATIONS}); staying put"
        )
        return False
    return True


def clear_step_stall(homestead: dict[str, Any], step_name: str) -> None:
    """Forget the stall history for a step that made progress."""
    failures = homestead.get(SITE_STEP_FAILURES)
    if isinstance(failures, dict):
        failures.pop(step_name, None)


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
        moved = relocate_build_site_search(client, attempt=relocations)
    except Exception as error:  # search is best-effort; never kill the run
        print(f"  Homestead relocation search failed ({error})")
        return False
    if not moved:
        print("  Homestead relocation found no better site in range")
        return False

    new_anchor = helper.current_position()
    if not helper._dry_ground(new_anchor):
        print(f"  Relocation landed on non-dry ground at {new_anchor}; keeping old site")
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
    print(f"  Homestead relocated to {new_anchor}")
    return True
