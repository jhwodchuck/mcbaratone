"""Whether a naked rebuild can survive the window it is about to start in.

``_bootstrap_starter_pickaxe`` re-crafts a wooden pickaxe by calling
``ensure_supplies(..., timeout=300)``, which walks the bot outdoors for up to
five minutes. It ran two seconds after a respawn, wearing nothing and carrying
nothing, and killed dragon-a and dragon-b six times across 2026-08-14/15.
"""

from __future__ import annotations


def survivable_bootstrap_window(client) -> bool:
    """Ensure the naked rebuild is not attempted into a night it cannot survive.

    ``ensure_supplies`` walks the bot outdoors for up to 300 seconds to chop
    wood. Two seconds after a respawn it owns nothing and wears nothing, so
    starting that at night is how dragon-a and dragon-b died six times across
    2026-08-14/15, each run ending on the supervisor's terminal safety circuit.

    Hoisting ``wait_for_safe_daylight`` earlier is *not* the fix: the gather
    loop already calls it from ``_ensure_outdoor_daylight`` and it is the
    second line of every one of those death traces. Its only protection is
    ``build_compact_night_shelter``, which returns False immediately without 10
    cobblestone or dirt, leaving it to run ``defend_or_flee`` at 0/4 armour
    every five seconds until something kills the bot.

    So dig in first. ``dig_and_seal_night_hole`` needs no inventory, and once
    the bot is sealed underground ``_has_existing_enclosure`` reports True,
    which is what steers ``wait_for_safe_daylight`` onto its "waiting inside"
    branch instead of the brawling one.
    """
    from ..common.base import _has_existing_enclosure, wait_for_safe_daylight
    from ..common.combat import scan_for_threats
    from ..common.night_shelter import dig_and_seal_night_hole

    try:
        state = client.transport.dispatch("get_state", {})
    except Exception as exc:
        print(f"RECOVERY: could not read state before bootstrap ({exc})")
        return True  # never let a telemetry blip block the only rebuild path

    if state.get("is_dead", False):
        return False

    day_time = int(state.get("world_time", 0)) % 24000
    threats = []
    try:
        threats = scan_for_threats(client, radius=16) or []
    except Exception:
        threats = []
    close = [t for t in threats if float(t.get("distance", 999)) <= 12]

    if day_time < 12000 and not close:
        return True  # daylight and clear: gather exactly as before

    reason = "night" if day_time >= 12000 else "hostiles nearby"
    print(f"RECOVERY: {reason} before a naked rebuild; securing shelter first")
    if not _has_existing_enclosure(client, state) and not dig_and_seal_night_hole(
        client
    ):
        # No hand-mineable ground (stone, water, a platform). Falling through to
        # the gather would repeat the death that stops the run for good, so
        # report a deferral and let the caller retry later instead.
        print("RECOVERY: could not secure a shelter for the rebuild")
        return False
    return wait_for_safe_daylight(client)
