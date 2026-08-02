from types import SimpleNamespace

from baritone_client.common import husbandry


def test_breeding_food_maps_animals_to_correct_item():
    assert husbandry.breeding_food_for("minecraft:cow") == "minecraft:wheat"
    assert husbandry.breeding_food_for("minecraft:sheep") == "minecraft:wheat"
    assert husbandry.breeding_food_for("minecraft:pig") == "minecraft:carrot"
    assert husbandry.breeding_food_for("minecraft:chicken") == "minecraft:wheat_seeds"
    assert husbandry.breeding_food_for("minecraft:zombie") is None


def _cow(entity_id, x=-320, y=72, z=-202, baby=False):
    return {
        "id": entity_id,
        "type": "minecraft:cow",
        "is_baby": baby,
        "position": {"x": x, "y": y, "z": z},
        "distance": 2.0,
    }


def test_feed_animal_selects_food_and_interacts_by_entity_id(monkeypatch):
    calls = []
    wheat = {"count": 3}

    class Transport:
        def dispatch(self, route, payload=None):
            calls.append((route, payload))
            if route == "entity_interact":
                wheat["count"] -= 1
                return {"accepted": True}
            return {}

    client = SimpleNamespace(transport=Transport())
    monkeypatch.setattr(husbandry, "select_item", lambda *_a, **_k: True)
    # count_item returns current wheat, and drops after entity interaction.
    def count_item(_client, item):
        return wheat["count"] if item == "minecraft:wheat" else 0
    monkeypatch.setattr(husbandry, "count_item", count_item)
    monkeypatch.setattr(husbandry.time, "sleep", lambda _s: None)

    assert husbandry.feed_animal(client, _cow(1), "minecraft:wheat") is True
    assert calls == [
        (
            "entity_interact",
            {"action": "feed", "entity_id": 1, "max_distance": 6.0},
        )
    ]


def test_feed_animal_falls_back_only_for_old_bridge(monkeypatch):
    calls = []
    wheat = {"count": 3}

    class Transport:
        def dispatch(self, route, payload=None):
            calls.append((route, payload))
            if route == "entity_interact":
                raise RuntimeError("Unknown interaction action: feed")
            if route == "use_item":
                wheat["count"] -= 1
            return {}

    client = SimpleNamespace(transport=Transport())
    monkeypatch.setattr(husbandry, "select_item", lambda *_a, **_k: True)
    monkeypatch.setattr(
        husbandry,
        "count_item",
        lambda _client, item: wheat["count"] if item == "minecraft:wheat" else 0,
    )
    monkeypatch.setattr(husbandry.time, "sleep", lambda _s: None)

    assert husbandry.feed_animal(client, _cow(2), "minecraft:wheat") is True
    assert [route for route, _ in calls] == ["entity_interact", "look_at", "use_item"]


def test_feed_animal_fails_without_food(monkeypatch):
    client = SimpleNamespace(transport=SimpleNamespace(dispatch=lambda *_a, **_k: {}))
    monkeypatch.setattr(husbandry, "count_item", lambda *_a: 0)
    assert husbandry.feed_animal(client, _cow(1), "minecraft:wheat") is False


def test_breed_pair_requires_two_adults(monkeypatch):
    client = SimpleNamespace(transport=SimpleNamespace(dispatch=lambda *_a, **_k: {}))
    monkeypatch.setattr(husbandry, "count_item", lambda _c, _i: 8)
    # Only one adult present.
    monkeypatch.setattr(
        husbandry, "get_nearby_entities", lambda *_a, **_k: [_cow(1)]
    )
    assert husbandry.breed_pair(client, "cow") is False


def test_breed_pair_confirms_via_herd_growth(monkeypatch):
    client = SimpleNamespace(transport=SimpleNamespace(dispatch=lambda *_a, **_k: {}))
    monkeypatch.setattr(husbandry, "count_item", lambda _c, _i: 8)
    monkeypatch.setattr(husbandry, "select_item", lambda *_a, **_k: True)
    monkeypatch.setattr(husbandry, "feed_animal", lambda *_a, **_k: True)
    monkeypatch.setattr(husbandry.time, "sleep", lambda _s: None)
    monkeypatch.setattr(husbandry.time, "monotonic", lambda: 0.0)

    # Two adults before; a calf appears after breeding (herd 2 -> 3).
    herd = {"n": 2}
    seq = iter([
        [_cow(1), _cow(2)],           # adults check
        [_cow(1), _cow(2)],           # herd_before count
        [_cow(1), _cow(2), _cow(3, baby=True)],  # after: grew
    ])
    def entities(*_a, **_k):
        try:
            return next(seq)
        except StopIteration:
            return [_cow(1), _cow(2), _cow(3, baby=True)]
    monkeypatch.setattr(husbandry, "get_nearby_entities", entities)

    assert husbandry.breed_pair(client, "cow") is True


def test_breed_pair_fails_when_herd_does_not_grow(monkeypatch):
    client = SimpleNamespace(transport=SimpleNamespace(dispatch=lambda *_a, **_k: {}))
    monkeypatch.setattr(husbandry, "count_item", lambda _c, _i: 8)
    monkeypatch.setattr(husbandry, "feed_animal", lambda *_a, **_k: True)
    monkeypatch.setattr(husbandry.time, "sleep", lambda _s: None)
    t = {"v": 0.0}
    def monotonic():
        t["v"] += 1.0
        return t["v"]
    monkeypatch.setattr(husbandry.time, "monotonic", monotonic)
    # Herd stays at 2 the whole time (no calf).
    monkeypatch.setattr(
        husbandry, "get_nearby_entities", lambda *_a, **_k: [_cow(1), _cow(2)]
    )
    assert husbandry.breed_pair(client, "cow") is False


def test_breed_herd_travels_to_location_then_breeds(monkeypatch):
    client = SimpleNamespace(transport=SimpleNamespace(dispatch=lambda *_a, **_k: {}))
    went = []
    monkeypatch.setattr(
        husbandry, "goto",
        lambda _c, x, y, z, **_k: went.append((x, y, z)) or True,
    )
    # herd grows 2 -> 4 across two successful breeds, then stops.
    counts = iter([2, 3, 4, 4])
    monkeypatch.setattr(husbandry, "count_herd", lambda *_a, **_k: next(counts, 4))
    bred = {"n": 0}
    def breed_pair(_c, _t, **_k):
        bred["n"] += 1
        return bred["n"] <= 2
    monkeypatch.setattr(husbandry, "breed_pair", breed_pair)

    assert husbandry.breed_herd(client, (-320, 72, -202), "cow", target_size=6) is True
    assert went == [(-320, 72, -202)]


def test_breed_herd_fails_if_cannot_reach(monkeypatch):
    client = SimpleNamespace(transport=SimpleNamespace(dispatch=lambda *_a, **_k: {}))
    monkeypatch.setattr(husbandry, "goto", lambda *_a, **_k: False)
    monkeypatch.setattr(
        husbandry, "breed_pair",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("must not breed if the herd was never reached")
        ),
    )
    assert husbandry.breed_herd(client, (-320, 72, -202), "cow") is False


def test_discover_herd_returns_verified_observed_centroid(monkeypatch):
    class Transport:
        def dispatch(self, route, _payload):
            if route == "get_state":
                return {"block_position": {"x": 0, "y": 64, "z": 0}}
            return {}

    client = SimpleNamespace(transport=Transport())
    monkeypatch.setattr(
        husbandry,
        "_animals_of_type",
        lambda *_a, **_k: [
            {"position": {"x": 10, "y": 65, "z": 20}},
            {"position": {"x": 12, "y": 65, "z": 22}},
        ],
    )
    went = []
    monkeypatch.setattr(
        husbandry,
        "goto",
        lambda _c, x, y, z, **_k: went.append((x, y, z)) or True,
    )
    monkeypatch.setattr(husbandry, "count_herd", lambda *_a, **_k: 2)

    assert husbandry.discover_herd(client, "cow") == (11, 65, 21)
    assert went == [(11, 65, 21)]


def test_discover_herd_stops_pathing_without_displacement(monkeypatch):
    calls = []

    class Transport:
        def dispatch(self, route, payload):
            calls.append((route, payload))
            if route == "get_state":
                return {
                    "block_position": {"x": 0, "y": 64, "z": 0},
                    "is_pathing": True,
                }
            return {}

    clock = iter([0.0, 0.0, 0.0, 0.0, 4.0, 4.0])
    monkeypatch.setattr(husbandry.time, "monotonic", lambda: next(clock, 4.0))
    monkeypatch.setattr(husbandry.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(husbandry, "_animals_of_type", lambda *_a, **_k: [])

    client = SimpleNamespace(transport=Transport())
    assert husbandry.discover_herd(
        client,
        "cow",
        timeout=30.0,
        stall_timeout=3.0,
    ) is None
    assert ("explore", {"x": 0, "z": 0}) in calls
    assert ("cancel", {}) in calls
    assert ("chat", {"message": "#stop"}) in calls


def test_visit_known_herd_returns_true_immediately_if_already_satisfied(monkeypatch):
    """If required_loot is already banked, must not travel anywhere."""
    client = SimpleNamespace(transport=SimpleNamespace(dispatch=lambda *_a, **_k: {}))
    monkeypatch.setattr(husbandry, "count_item", lambda _c, item: 46 if item == "minecraft:leather" else 0)
    monkeypatch.setattr(
        husbandry, "goto",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("must not travel when loot is already satisfied")
        ),
    )
    assert husbandry.visit_known_herd_for_loot(
        client, {"minecraft:leather": 5}, "cow"
    ) is True


def test_visit_known_herd_verifies_preserved_breeding_pair(monkeypatch):
    client = SimpleNamespace(transport=SimpleNamespace(dispatch=lambda *_a, **_k: {}))
    monkeypatch.setattr(husbandry, "count_item", lambda *_a, **_k: 16)
    went = []
    monkeypatch.setattr(
        husbandry,
        "goto",
        lambda _c, x, y, z, **_k: went.append((x, y, z)) or True,
    )
    monkeypatch.setattr(husbandry, "breed_pair", lambda *_a, **_k: True)
    monkeypatch.setattr(husbandry, "count_herd", lambda *_a, **_k: 2)

    assert husbandry.visit_known_herd_for_loot(
        client,
        {"minecraft:beef": 16},
        "cow",
        preserve_breeding_pair=True,
    ) is True
    assert went == [(-320, 72, -202), (-320, 72, -202)]


def test_visit_known_herd_rejects_nonrenewable_survivors(monkeypatch):
    client = SimpleNamespace(transport=SimpleNamespace(dispatch=lambda *_a, **_k: {}))
    monkeypatch.setattr(husbandry, "count_item", lambda *_a, **_k: 16)
    monkeypatch.setattr(husbandry, "goto", lambda *_a, **_k: True)
    monkeypatch.setattr(husbandry, "breed_pair", lambda *_a, **_k: False)
    monkeypatch.setattr(husbandry, "count_herd", lambda *_a, **_k: 1)

    assert husbandry.visit_known_herd_for_loot(
        client,
        {"minecraft:beef": 16},
        "cow",
        preserve_breeding_pair=True,
    ) is False


def test_visit_known_herd_fails_for_unknown_animal_type():
    client = SimpleNamespace(transport=SimpleNamespace(dispatch=lambda *_a, **_k: {}))
    assert husbandry.visit_known_herd_for_loot(
        client, {"minecraft:leather": 5}, "penguin"
    ) is False


def test_visit_known_herd_travels_breeds_then_hunts(monkeypatch):
    """The known-herd fallback must travel to the operator-provided waypoint,
    attempt to breed the herd (best-effort), then hunt for the absolute
    deficit (target minus what's already carried)."""
    client = SimpleNamespace(transport=SimpleNamespace(dispatch=lambda *_a, **_k: {}))
    leather = {"n": 2}
    monkeypatch.setattr(husbandry, "count_item", lambda _c, item: leather["n"] if item == "minecraft:leather" else 0)

    went = []
    monkeypatch.setattr(husbandry, "goto", lambda _c, x, y, z, **_k: went.append((x, y, z)) or True)

    bred = []
    monkeypatch.setattr(husbandry, "breed_pair", lambda *_a, **_k: bred.append(True) or True)

    hunted = []
    def fake_hunt_mobs(_client, mob_types, required_loot, **_kwargs):
        hunted.append((tuple(mob_types), dict(required_loot)))
        leather["n"] += required_loot["minecraft:leather"]
        return SimpleNamespace(success=True)
    monkeypatch.setattr(husbandry, "hunt_mobs", fake_hunt_mobs)

    # Have 2, need 5 total -> hunt_mobs should be asked for the 3-item deficit.
    assert husbandry.visit_known_herd_for_loot(
        client, {"minecraft:leather": 5}, "cow"
    ) is True
    assert went == [(-320, 72, -202)]
    assert bred == [True]
    assert hunted == [(("cow",), {"minecraft:leather": 3})]


def test_visit_known_herd_fails_if_cannot_reach(monkeypatch):
    client = SimpleNamespace(transport=SimpleNamespace(dispatch=lambda *_a, **_k: {}))
    monkeypatch.setattr(husbandry, "count_item", lambda *_a: 0)
    monkeypatch.setattr(husbandry, "goto", lambda *_a, **_k: False)
    monkeypatch.setattr(
        husbandry, "breed_pair",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("must not breed if the herd was never reached")
        ),
    )
    assert husbandry.visit_known_herd_for_loot(
        client, {"minecraft:leather": 5}, "cow"
    ) is False


def test_visit_known_herd_returns_true_when_breeding_alone_satisfies_loot(monkeypatch):
    """If the required loot resolves during travel/breeding (e.g. another
    system already banked it), hunting must be skipped entirely."""
    client = SimpleNamespace(transport=SimpleNamespace(dispatch=lambda *_a, **_k: {}))
    monkeypatch.setattr(husbandry, "goto", lambda *_a, **_k: True)

    def breed_and_satisfy(*_a, **_k):
        counts["n"] = 10
        return True
    counts = {"n": 0}
    monkeypatch.setattr(husbandry, "count_item", lambda _c, _item: counts["n"])
    monkeypatch.setattr(husbandry, "breed_pair", breed_and_satisfy)
    monkeypatch.setattr(
        husbandry, "hunt_mobs",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("must not hunt once loot is already satisfied")
        ),
    )

    assert husbandry.visit_known_herd_for_loot(
        client, {"minecraft:leather": 5}, "cow"
    ) is True
