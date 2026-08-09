from types import SimpleNamespace

from baritone_client.common import food_supply


def _state(data):
    return SimpleNamespace(custom_data=data)


def _inventory(monkeypatch, counts):
    monkeypatch.setattr(food_supply, "get_inventory", lambda _client: dict(counts))
    monkeypatch.setattr(food_supply, "_survival_ready", lambda _client: True)
    monkeypatch.setattr(
        food_supply,
        "find_farm_surface_near",
        lambda _client, x, y, z: (x, y, z),
    )


def test_after_existing_plots_are_immature_expand_another_plot(monkeypatch):
    counts = {"minecraft:wheat": 0, "minecraft:wheat_seeds": 12}
    _inventory(monkeypatch, counts)
    monkeypatch.setattr(food_supply, "eat_until_hunger", lambda *_a, **_k: True)
    monkeypatch.setattr(food_supply, "harvest_wheat_farm", lambda *_a, **_k: False)
    created = []
    def establish(_client, x, y, z, **_kwargs):
        created.append((x, y, z)); return (x, y, z)
    monkeypatch.setattr(food_supply, "establish_wheat_farm", establish)
    monkeypatch.setattr(food_supply, "resolve_storage_location", lambda *_a, **_k: None)
    result = food_supply.run_food_cycle(object(), _state({"wheat_farm": {"origin": [0, 64, 0]}}))
    assert result.success and result.plots == 1 and result.crops_replanted == 1
    assert created == [(32, 64, 0)]


def test_no_permanent_terminal_plot_cap(monkeypatch):
    counts = {"minecraft:wheat": 0, "minecraft:wheat_seeds": 64}
    _inventory(monkeypatch, counts)
    monkeypatch.setattr(food_supply, "eat_until_hunger", lambda *_a, **_k: True)
    monkeypatch.setattr(food_supply, "harvest_wheat_farm", lambda *_a, **_k: False)
    monkeypatch.setattr(food_supply, "establish_wheat_farm", lambda _c, x, y, z, **_k: (x, y, z))
    monkeypatch.setattr(food_supply, "resolve_storage_location", lambda *_a, **_k: None)
    state = _state(
        {
            "wheat_farm": {"origin": [0, 64, 0]},
            "food_worker": {
                "farm_plots": [
                    {"origin": [index * 40, 64, 0]} for index in range(20)
                ]
            },
        }
    )
    result = food_supply.run_food_cycle(object(), state)
    assert result.plots == 1
    assert len(state.custom_data["food_worker"]["farm_plots"]) == 21


def test_harvest_and_bank_are_credited_only_by_inventory_delta(monkeypatch):
    counts = {"minecraft:wheat": 1, "minecraft:bread": 10}
    def inventory(_client): return dict(counts)
    monkeypatch.setattr(food_supply, "get_inventory", inventory)
    monkeypatch.setattr(food_supply, "_survival_ready", lambda _client: True)
    monkeypatch.setattr(food_supply, "eat_until_hunger", lambda *_a, **_k: True)
    def harvest(*_a, **_k): counts["minecraft:wheat"] += 4; return True
    monkeypatch.setattr(food_supply, "harvest_wheat_farm", harvest)
    monkeypatch.setattr(food_supply, "craft", lambda *_a, **_k: False)
    monkeypatch.setattr(food_supply, "resolve_storage_location", lambda *_a, **_k: (1, 64, 1))
    def deposit(*_a, **_k): counts["minecraft:bread"] -= 2; return 1
    monkeypatch.setattr(food_supply, "deposit_excess_to_chest", deposit)
    result = food_supply.run_food_cycle(object(), _state({"wheat_farm": {"origin": [0, 64, 0]}}))
    assert (result.wheat_harvested, result.prepared_food_banked) == (4, 2)
    assert result.total_food_banked == 2


def test_known_plot_inspection_is_round_robin_and_bounded(monkeypatch):
    _inventory(monkeypatch, {})
    monkeypatch.setattr(food_supply, "eat_until_hunger", lambda *_a, **_k: True)
    seen = []
    monkeypatch.setattr(
        food_supply,
        "harvest_wheat_farm",
        lambda _client, x, y, z, **_kwargs: seen.append((x, y, z)),
    )
    monkeypatch.setattr(food_supply, "establish_wheat_farm", lambda *_a, **_k: None)
    monkeypatch.setattr(food_supply, "resolve_storage_location", lambda *_a, **_k: None)
    plots = [{"origin": [index * 40, 64, 0]} for index in range(5)]
    state = _state({"food_worker": {"farm_plots": plots}})
    food_supply.run_food_cycle(object(), state)
    food_supply.run_food_cycle(object(), state)
    assert seen == [
        (0, 64, 0),
        (40, 64, 0),
        (80, 64, 0),
        (120, 64, 0),
        (160, 64, 0),
        (0, 64, 0),
    ]


def test_survival_recovery_failure_holds_before_farm_travel(monkeypatch):
    _inventory(monkeypatch, {})
    monkeypatch.setattr(food_supply, "eat_until_hunger", lambda *_a, **_k: False)
    monkeypatch.setattr(
        food_supply,
        "harvest_wheat_farm",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("must not travel")),
    )
    state = _state({"wheat_farm": {"origin": [0, 64, 0]}})
    result = food_supply.run_food_cycle(object(), state)
    assert not result.success
    assert state.custom_data["food_worker"]["attempts"] == 1
    assert "survival recovery" in result.detail


def test_failed_expansion_site_is_retired_before_next_attempt(monkeypatch):
    _inventory(monkeypatch, {"minecraft:wheat_seeds": 20})
    monkeypatch.setattr(food_supply, "eat_until_hunger", lambda *_a, **_k: True)
    monkeypatch.setattr(food_supply, "harvest_wheat_farm", lambda *_a, **_k: False)
    attempted = []
    def fail(_client, x, y, z, **_kwargs):
        attempted.append((x, y, z)); return None
    monkeypatch.setattr(food_supply, "establish_wheat_farm", fail)
    monkeypatch.setattr(food_supply, "resolve_storage_location", lambda *_a, **_k: None)
    state = _state({"wheat_farm": {"origin": [0, 64, 0]}})
    food_supply.run_food_cycle(object(), state)
    food_supply.run_food_cycle(object(), state)
    assert attempted == [(32, 64, 0), (0, 64, 32)]
    assert state.custom_data["food_worker"]["failed_plot_sites"] == [
        [32, 64, 0],
        [0, 64, 32],
    ]


def test_retired_legacy_plot_is_not_resurrected_next_cycle():
    state = _state(
        {
            "base_location": [500, 70, 500],
            "wheat_farm": {"origin": [0, 64, 0]},
            "food_worker": {"farm_plots": [{"origin": [0, 64, 0]}]},
        }
    )

    retired = food_supply._retire_distant_plots(
        state,
        state.custom_data["food_worker"],
        (500, 70, 500),
    )

    assert retired == [[0, 64, 0]]
    assert "wheat_farm" not in state.custom_data
    assert food_supply._plots(state) == []


def test_distant_plot_is_retired_even_when_worker_reached_it(monkeypatch):
    _inventory(monkeypatch, {"minecraft:wheat_seeds": 10})
    monkeypatch.setattr(food_supply, "eat_until_hunger", lambda *_a, **_k: True)
    monkeypatch.setattr(
        food_supply,
        "harvest_wheat_farm",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("distant plot must retire before harvest")
        ),
    )
    returned = []
    monkeypatch.setattr(
        food_supply,
        "_return_to_anchor",
        lambda _client, anchor: returned.append(anchor) or True,
    )
    state = _state(
        {
            "base_location": [500, 70, 500],
            "wheat_farm": {"origin": [0, 64, 0]},
            "food_worker": {"farm_plots": [{"origin": [0, 64, 0]}]},
        }
    )

    result = food_supply.run_food_cycle(object(), state)

    assert not result.success
    assert "retired 1 farm plot" in result.detail
    assert "returned to base" in result.detail
    assert returned == [(500, 70, 500)]
    assert "wheat_farm" not in state.custom_data


def test_frontier_keeps_expanding_past_four_verified_plots(monkeypatch):
    _inventory(monkeypatch, {"minecraft:wheat_seeds": 64})
    monkeypatch.setattr(food_supply, "eat_until_hunger", lambda *_a, **_k: True)
    monkeypatch.setattr(food_supply, "harvest_wheat_farm", lambda *_a, **_k: False)
    monkeypatch.setattr(
        food_supply,
        "establish_wheat_farm",
        lambda _client, x, y, z, **_kwargs: (x, y, z),
    )
    monkeypatch.setattr(food_supply, "resolve_storage_location", lambda *_a, **_k: None)
    state = _state({"wheat_farm": {"origin": [0, 64, 0]}})
    results = [food_supply.run_food_cycle(object(), state) for _ in range(6)]
    assert all(result.plots == 1 for result in results)
    assert len(state.custom_data["food_worker"]["farm_plots"]) == 7


def test_expansion_moves_beyond_four_rejected_cardinal_sites(monkeypatch):
    _inventory(monkeypatch, {"minecraft:wheat_seeds": 64})
    monkeypatch.setattr(food_supply, "eat_until_hunger", lambda *_a, **_k: True)
    monkeypatch.setattr(food_supply, "harvest_wheat_farm", lambda *_a, **_k: False)
    attempted = []

    def reject(_client, x, y, z, **_kwargs):
        attempted.append((x, y, z))
        return None

    monkeypatch.setattr(food_supply, "establish_wheat_farm", reject)
    monkeypatch.setattr(
        food_supply, "resolve_storage_location", lambda *_a, **_k: None
    )
    state = _state({"wheat_farm": {"origin": [0, 64, 0]}})

    for _ in range(7):
        food_supply.run_food_cycle(object(), state)

    # Intent: rejected cardinals must not stall siting forever. The original
    # assertion also required moving beyond 32 blocks, which is the behaviour
    # that let plots march outward until the worker could no longer reach them
    # (Bot18 ended 461 blocks from its own farm). Expansion must still explore
    # many distinct sites, but all of them within one short walk of base.
    from math import hypot
    assert len(set(attempted)) > 4
    assert all(
        hypot(x, z) <= food_supply.MAX_ANCHOR_RADIUS + 1
        for x, _y, z in attempted
    ), attempted


def test_exhausted_empty_frontier_is_reopened_once_after_setup_repair():
    anchor = (546, 79, -304)
    rejected = [
        [578, 79, -304],
        [546, 79, -272],
        [514, 79, -304],
        [546, 79, -336],
    ]
    worker = {
        "failed_plot_sites": rejected,
        "expansion_cursor": 114,
    }

    candidate = food_supply._next_plot_candidate(
        anchor, [], worker, 32, 3
    )

    assert candidate == (578, 79, -304)
    assert worker["failed_plot_sites"] == []
    assert worker["failed_site_reset_anchor"] == list(anchor)


def test_first_checkpointed_plot_prefers_observed_natural_soil(monkeypatch):
    state = _state({"food_worker": {"farm_plots": []}})
    state.checkpoint_dir = "checkpoint"
    monkeypatch.setattr(
        food_supply,
        "find_natural_crop_center",
        lambda *_args: ((559, 74, -284), False),
    )
    established = []
    monkeypatch.setattr(
        food_supply,
        "establish_wheat_farm",
        lambda _client, *position, **_kwargs: established.append(position)
        or position,
    )

    result = food_supply._establish_candidate(
        object(), state, (546, 79, -272), 5
    )

    assert result == (559, 74, -284)
    assert established == [(559, 74, -284)]


def test_first_plot_probes_current_column_inside_base_radius(monkeypatch):
    state = _state(
        {"base_location": [546, 79, -304], "food_worker": {"farm_plots": []}}
    )
    state.checkpoint_dir = "checkpoint"
    client = SimpleNamespace(
        transport=SimpleNamespace(
            dispatch=lambda *_args, **_kwargs: {
                "block_position": {"x": 559, "y": 75, "z": -284}
            }
        )
    )
    monkeypatch.setattr(
        food_supply,
        "find_farm_surface_near",
        lambda _client, x, y, z, **_kwargs: (x, 73, z),
    )
    monkeypatch.setattr(
        food_supply,
        "find_natural_crop_center",
        lambda *_args: (None, False),
    )
    monkeypatch.setattr(
        food_supply,
        "establish_wheat_farm",
        lambda _client, *position, **_kwargs: position,
    )

    assert food_supply._establish_candidate(
        client, state, None, 5
    ) == (559, 73, -284)


def test_dead_state_fails_closed_before_self_feed_or_farm_travel(monkeypatch):
    _inventory(monkeypatch, {})
    monkeypatch.setattr(food_supply, "_survival_ready", lambda _client: False)
    monkeypatch.setattr(
        food_supply,
        "eat_until_hunger",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("must not eat")),
    )
    result = food_supply.run_food_cycle(object(), _state({}))
    assert not result.success
    assert "unsafe" in result.detail


def test_missing_live_state_fails_closed():
    client = SimpleNamespace(
        transport=SimpleNamespace(dispatch=lambda *_args, **_kwargs: {})
    )

    assert not food_supply._survival_ready(client)
