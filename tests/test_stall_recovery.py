from types import SimpleNamespace

from baritone_client.automator.objective import (
    ObjStatus,
    ObjectivePlanner,
    default_objectives,
)
from baritone_client.automator.stall_recovery import (
    OBJECTIVE_RUNTIME_REVISION,
    maintain_stalled_survival,
    rearm_abandoned_objectives,
    rearm_recovered_survival_objectives,
)
from baritone_client.automator.state_manager import Phase


def test_runtime_revision_rearms_abandoned_objective_once():
    planner = ObjectivePlanner(default_objectives())
    objective = planner._by_phase[Phase.BOOT_SEQUENCE]
    objective.status = ObjStatus.ABANDONED
    objective.attempts = 3
    objective.interruptions = 4
    objective.no_progress_streak = 2
    objective.last_failure = "phase_failed"
    custom_data = {}

    assert rearm_abandoned_objectives(planner, custom_data) == [
        "BOOT_SEQUENCE"
    ]
    assert objective.status is ObjStatus.PENDING
    assert objective.attempts == 0
    assert objective.interruptions == 0
    assert objective.no_progress_streak == 0
    assert objective.last_failure == ""
    assert custom_data["objective_runtime_revision"] == OBJECTIVE_RUNTIME_REVISION

    objective.status = ObjStatus.ABANDONED
    objective.attempts = 3
    assert rearm_abandoned_objectives(planner, custom_data) == []
    assert objective.status is ObjStatus.ABANDONED
    assert objective.attempts == 3


def test_runtime_revision_refreshes_blocked_objective_budget():
    planner = ObjectivePlanner(default_objectives())
    objective = planner._by_phase[Phase.BOOT_SEQUENCE]
    objective.status = ObjStatus.BLOCKED
    objective.attempts = 2
    objective.interruptions = 3
    objective.no_progress_streak = 1
    objective.last_failure = "phase_failed"
    custom_data = {}

    assert rearm_abandoned_objectives(planner, custom_data) == [
        "BOOT_SEQUENCE"
    ]
    assert objective.status is ObjStatus.PENDING
    assert objective.attempts == 0
    assert objective.interruptions == 0
    assert objective.no_progress_streak == 0
    assert objective.last_failure == ""


def test_crop_site_repair_rearms_revision_33_checkpoint():
    """The dry-farm repair must reopen bots exhausted on revision 33."""
    planner = ObjectivePlanner(default_objectives())
    objective = planner._by_phase[Phase.BOOT_SEQUENCE]
    objective.status = ObjStatus.ABANDONED
    objective.attempts = 3
    custom_data = {"objective_runtime_revision": 33}

    assert rearm_abandoned_objectives(planner, custom_data) == ["BOOT_SEQUENCE"]
    assert objective.status is ObjStatus.PENDING
    assert custom_data["objective_runtime_revision"] == OBJECTIVE_RUNTIME_REVISION


def test_surface_soil_repair_rearms_revision_34_checkpoint():
    """The surface-soil repair must reopen bots exhausted on revision 34."""
    planner = ObjectivePlanner(default_objectives())
    objective = planner._by_phase[Phase.BOOT_SEQUENCE]
    objective.status = ObjStatus.ABANDONED
    custom_data = {"objective_runtime_revision": 34}

    assert rearm_abandoned_objectives(planner, custom_data) == ["BOOT_SEQUENCE"]
    assert custom_data["objective_runtime_revision"] == OBJECTIVE_RUNTIME_REVISION


def test_hoe_crafting_repair_rearms_revision_35_checkpoint():
    """The direct-hoe repair must reopen bots exhausted on revision 35."""
    planner = ObjectivePlanner(default_objectives())
    objective = planner._by_phase[Phase.BOOT_SEQUENCE]
    objective.status = ObjStatus.ABANDONED
    custom_data = {"objective_runtime_revision": 35}

    assert rearm_abandoned_objectives(planner, custom_data) == ["BOOT_SEQUENCE"]
    assert custom_data["objective_runtime_revision"] == OBJECTIVE_RUNTIME_REVISION


def test_farm_return_repair_rearms_revision_36_checkpoint():
    """The post-craft return repair must reopen bots exhausted on revision 36."""
    planner = ObjectivePlanner(default_objectives())
    objective = planner._by_phase[Phase.BOOT_SEQUENCE]
    objective.status = ObjStatus.ABANDONED
    custom_data = {"objective_runtime_revision": 36}

    assert rearm_abandoned_objectives(planner, custom_data) == ["BOOT_SEQUENCE"]
    assert custom_data["objective_runtime_revision"] == OBJECTIVE_RUNTIME_REVISION


def test_crop_hotbar_repair_rearms_revision_37_checkpoint():
    """The hotbar-swap repair must reopen bots exhausted on revision 37."""
    planner = ObjectivePlanner(default_objectives())
    objective = planner._by_phase[Phase.BOOT_SEQUENCE]
    objective.status = ObjStatus.ABANDONED
    custom_data = {"objective_runtime_revision": 37}

    assert rearm_abandoned_objectives(planner, custom_data) == ["BOOT_SEQUENCE"]
    assert custom_data["objective_runtime_revision"] == OBJECTIVE_RUNTIME_REVISION


def test_stalled_survival_actively_recovers_low_food(monkeypatch):
    client = SimpleNamespace(
        transport=SimpleNamespace(
            dispatch=lambda route, _payload: {
                "health": 20,
                "food_level": 5,
                "world_time": 1000,
            }
        )
    )
    attempts = []
    monkeypatch.setattr(
        "baritone_client.common.combat.ensure_alive",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        "baritone_client.common.combat.scan_for_threats",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        "baritone_client.common.combat.acquire_emergency_food",
        lambda _client, **kwargs: attempts.append(kwargs) or True,
    )

    assert maintain_stalled_survival(client) == "food_recovery"
    assert attempts == [
        {
            "minimum_health": 12.0,
            "minimum_food": 14,
            "timeout": 120.0,
        }
    ]


def test_safe_survival_recovery_reopens_abandoned_objective():
    planner = ObjectivePlanner(default_objectives())
    objective = planner._by_phase[Phase.BOOT_SEQUENCE]
    objective.status = ObjStatus.ABANDONED
    objective.attempts = 2
    objective.interruptions = 4
    objective.no_progress_streak = 3
    objective.last_failure = "survival_recovery"
    client = SimpleNamespace(
        transport=SimpleNamespace(
            dispatch=lambda _route, _payload: {
                "health": 13.0,
                "food_level": 17,
            }
        )
    )

    assert rearm_recovered_survival_objectives(planner, client) == [
        "BOOT_SEQUENCE"
    ]
    assert objective.status is ObjStatus.PENDING
    assert objective.interruptions == 0
    assert objective.no_progress_streak == 0


def test_unsafe_or_phase_failed_objective_is_not_reopened():
    planner = ObjectivePlanner(default_objectives())
    objective = planner._by_phase[Phase.BOOT_SEQUENCE]
    objective.status = ObjStatus.ABANDONED
    objective.last_failure = "survival_recovery"
    unsafe = SimpleNamespace(
        transport=SimpleNamespace(
            dispatch=lambda _route, _payload: {
                "health": 20.0,
                "food_level": 9,
            }
        )
    )
    assert rearm_recovered_survival_objectives(planner, unsafe) == []

    objective.last_failure = "phase_failed"
    safe = SimpleNamespace(
        transport=SimpleNamespace(
            dispatch=lambda _route, _payload: {
                "health": 20.0,
                "food_level": 20,
            }
        )
    )
    assert rearm_recovered_survival_objectives(planner, safe) == []


def _abandoned_boot_planner(last_failure="phase_failed"):
    """Reproduces the live checkpoint: the three bootstrap objectives DONE,
    BOOT_SEQUENCE abandoned, everything downstream still PENDING."""
    planner = ObjectivePlanner(default_objectives())
    for done in (Phase.BRIDGE_CHECK, Phase.SPAWN_BOOTSTRAP, Phase.INITIAL_GATHERING):
        planner._by_phase[done].status = ObjStatus.DONE
    objective = planner._by_phase[Phase.BOOT_SEQUENCE]
    objective.status = ObjStatus.ABANDONED
    objective.attempts = 3
    objective.last_failure = last_failure
    return planner, objective


def test_abandoned_mid_chain_objective_bricks_the_whole_graph():
    """Documents the failure this repair exists for: the graph is a strict
    chain, so abandoning BOOT_SEQUENCE leaves every downstream objective
    unreachable forever. Measured live: ~9 days, ~4,800 controller restarts,
    zero phase completions, with BOOT_SEQUENCE abandoned and all ten
    downstream objectives PENDING on a prerequisite that can never be met."""
    from baritone_client.automator.stall_recovery import (
        rearm_any_abandoned_objectives,
    )

    planner, _ = _abandoned_boot_planner()
    assert not planner.runnable(), "nothing should be runnable once boot is abandoned"

    reopened = rearm_any_abandoned_objectives(planner, {}, now=1000.0)

    assert "BOOT_SEQUENCE" in reopened
    assert planner.runnable(), "the graph must be recoverable"


def test_full_rearm_covers_failures_the_survival_repair_ignores():
    """rearm_recovered_survival_objectives only matches last_failure ==
    'survival_recovery'. BOOT_SEQUENCE died with 'player_death' live, so it
    was never re-opened by anything."""
    from baritone_client.automator.stall_recovery import (
        rearm_any_abandoned_objectives,
    )

    planner, objective = _abandoned_boot_planner(last_failure="player_death")
    client = SimpleNamespace(
        transport=SimpleNamespace(
            dispatch=lambda *_a, **_k: {"health": 20.0, "food_level": 20}
        )
    )

    assert rearm_recovered_survival_objectives(planner, client) == []
    assert rearm_any_abandoned_objectives(planner, {}, now=1000.0) == ["BOOT_SEQUENCE"]
    assert objective.status is ObjStatus.PENDING


def test_full_rearm_is_rate_limited_so_it_cannot_hot_loop():
    """A phase that fails instantly must not ping-pong between abandonment
    and re-arm."""
    from baritone_client.automator.stall_recovery import (
        rearm_any_abandoned_objectives,
    )

    planner, objective = _abandoned_boot_planner()
    custom_data = {}

    assert rearm_any_abandoned_objectives(
        planner, custom_data, now=1000.0, cooldown_seconds=300.0
    ) == ["BOOT_SEQUENCE"]

    objective.status = ObjStatus.ABANDONED  # fails again immediately
    assert rearm_any_abandoned_objectives(
        planner, custom_data, now=1100.0, cooldown_seconds=300.0
    ) == [], "must respect the cooldown"

    assert rearm_any_abandoned_objectives(
        planner, custom_data, now=1400.0, cooldown_seconds=300.0
    ) == ["BOOT_SEQUENCE"], "and retry once the cooldown expires"


def test_default_rearm_cooldown_keeps_idle_time_bounded():
    """The default is what production uses -- Automator omits the argument.

    A 300s default dominated fleet wall-clock: the stall branch sleeps 5s per
    pass, so one abandonment cost ~60 idle passes. Measured 2026-07-31, Bot07
    logged 9,296 stall holds (~12.9h asleep) against 1,002 placements. Guard
    the default directly so it cannot drift back up unnoticed.
    """
    import inspect

    from baritone_client.automator.stall_recovery import (
        rearm_any_abandoned_objectives,
    )

    default = inspect.signature(
        rearm_any_abandoned_objectives
    ).parameters["cooldown_seconds"].default
    assert default <= 60.0, "idle tax per abandonment must stay under a minute"

    planner, objective = _abandoned_boot_planner()
    custom_data = {}

    # Called exactly as Automator calls it -- no explicit cooldown.
    assert rearm_any_abandoned_objectives(
        planner, custom_data, now=1000.0
    ) == ["BOOT_SEQUENCE"]

    # Still blocks the hot-loop this guard exists to prevent.
    objective.status = ObjStatus.ABANDONED
    assert rearm_any_abandoned_objectives(
        planner, custom_data, now=1001.0
    ) == [], "an instantly-failing phase must not ping-pong"

    assert rearm_any_abandoned_objectives(
        planner, custom_data, now=1000.0 + default + 1.0
    ) == ["BOOT_SEQUENCE"], "and must retry once the default cooldown expires"


def test_cooldown_stall_is_not_reported_as_terminal(monkeypatch, advancing_clock):
    """scripts/monitor/autonomous_run.py treats the exact string "Automation
    stalled: no runnable objective remains." as a terminal safety stop and
    refuses to relaunch. rearm_any_abandoned_objectives is rate limited, so
    during its cooldown it returns nothing even though the graph recovers on
    the next pass -- printing the terminal string then permanently kills a bot
    for a condition that self-heals. Live 2026-07-31: Bot18 and Bot19 were
    both stopped with "objective graph has no runnable objective; manual
    repair required"."""
    import io, contextlib
    from baritone_client.automator.automator import EndGameAutomator

    planner, _ = _abandoned_boot_planner()
    auto = EndGameAutomator.__new__(EndGameAutomator)
    auto.planner = planner
    auto._stall_reported = False
    auto.client = SimpleNamespace(
        transport=SimpleNamespace(
            dispatch=lambda *_a, **_k: {"health": 20.0, "food_level": 20}
        )
    )
    # Cooldown already consumed, so the forced re-arm declines this pass.
    # Derive the clock offset from the real default: hardcoding a gap couples
    # this fixture to whatever the cooldown happens to be, and it silently
    # stopped exercising the decline path when the default dropped to 45s.
    import inspect

    from baritone_client.automator import stall_recovery as _stall_recovery

    cooldown = inspect.signature(
        _stall_recovery.rearm_any_abandoned_objectives
    ).parameters["cooldown_seconds"].default
    auto.state = SimpleNamespace(custom_data={"last_full_objective_rearm": 1_000_000.0})
    auto._persist_objective_progress = lambda: None
    auto._save_checkpoint = lambda: None
    monkeypatch.setattr(
        "baritone_client.automator.stall_recovery.maintain_stalled_survival",
        lambda _c: "holding",
    )
    monkeypatch.setattr(
        "baritone_client.automator.automator.time",
        advancing_clock(start=1_000_000.0 + cooldown / 2.0),
    )

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        auto._maintain_stalled_objective_graph()
    out = buf.getvalue()

    assert "no runnable objective remains" not in out, (
        "must not emit the terminal string while the graph is still recoverable"
    )
    assert "recoverable" in out


def test_truly_dead_graph_still_reports_terminal_stall(monkeypatch, advancing_clock):
    """A graph with nothing left to re-open is genuinely stuck and must still
    surface the terminal string so the supervisor stops relaunching."""
    import io, contextlib
    from baritone_client.automator.automator import EndGameAutomator

    planner = ObjectivePlanner(default_objectives())
    for objective in planner.objectives:
        objective.status = ObjStatus.DONE
    # Nothing abandoned/blocked, nothing runnable, not complete.
    planner._by_phase[Phase.MEGABASE_INIT].status = ObjStatus.PENDING
    planner._by_phase[Phase.WORLD_UNLOCK].status = ObjStatus.PENDING

    auto = EndGameAutomator.__new__(EndGameAutomator)
    auto.planner = planner
    auto._stall_reported = False
    auto.client = SimpleNamespace(
        transport=SimpleNamespace(
            dispatch=lambda *_a, **_k: {"health": 20.0, "food_level": 20}
        )
    )
    auto.state = SimpleNamespace(custom_data={})
    auto._persist_objective_progress = lambda: None
    auto._save_checkpoint = lambda: None
    monkeypatch.setattr(
        "baritone_client.automator.stall_recovery.maintain_stalled_survival",
        lambda _c: "holding",
    )
    monkeypatch.setattr(
        "baritone_client.automator.automator.time", advancing_clock()
    )

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        auto._maintain_stalled_objective_graph()

    assert "no runnable objective remains" in buf.getvalue()
