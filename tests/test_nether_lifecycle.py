from types import SimpleNamespace

from baritone_client.automator.phases import nether_prep
from baritone_client.automator.resource_manager import ResourceManager
from baritone_client.automator.state_manager import Phase, StateManager
from baritone_client.common import nether
from baritone_client.common.tasks import TaskResult


class MissionStub:
    def __init__(self):
        self.checkpoints = []

    def macro(self, name, params):
        assert name == "enter_nether"
        return {"result": {"ready": params["obsidian"] >= 14}}

    def checkpoint(self, phase, note=None):
        self.checkpoints.append((phase, note))


class PortalTransport:
    def __init__(self):
        self.dimension = "minecraft:overworld"
        self.blocks = {}
        self.calls = []
        self.position = {"x": 0, "y": 64, "z": 0}
        self.found_blocks = []

    def dispatch(self, route, payload, **_kwargs):
        self.calls.append((route, dict(payload)))
        if route == "get_state":
            return {
                "dimension": self.dimension,
                "block_position": dict(self.position),
            }
        if route == "get_block":
            key = (payload["x"], payload["y"], payload["z"])
            return {"id": self.blocks.get(key, "minecraft:air")}
        if route == "place_fire":
            x, y, z = payload["x"], payload["y"], payload["z"]
            for dx in (0, 1):
                for dy in (0, 1, 2):
                    self.blocks[(x + dx, y + dy, z)] = "minecraft:nether_portal"
                    # A lit portal is visible to a find_blocks sweep, which is
                    # how the client locates the interior it should stand in.
                    self.found_blocks.append({"x": x + dx, "y": y + dy, "z": z})
            return {"ignited": True}
        if route == "goto":
            self.position = {
                "x": payload["x"],
                "y": payload["y"],
                "z": payload["z"],
            }
            # You only change dimension by standing in a portal block. The
            # stub used to flip on every goto, which hid the difference
            # between an approach and an actual entry.
            destination = (payload["x"], payload["y"], payload["z"])
            if self.blocks.get(destination) == "minecraft:nether_portal":
                self.dimension = (
                    "minecraft:the_nether"
                    if "nether" not in self.dimension
                    else "minecraft:overworld"
                )
            return {"started": True}
        if route == "find_blocks":
            return {"found": list(self.found_blocks)}
        return {}


def test_build_ignite_verify_and_enter_portal(monkeypatch):
    transport = PortalTransport()
    client = SimpleNamespace(transport=transport, mission=MissionStub())
    portal = (3, 64, 0)

    def place(_client, x, y, z, item_id):
        assert item_id == "minecraft:obsidian"
        transport.blocks[(x, y, z)] = item_id
        return True

    monkeypatch.setattr(nether, "place_block", place)
    monkeypatch.setattr(nether.time, "sleep", lambda _seconds: None)

    assert nether.build_nether_portal(client, *portal)
    assert nether.verify_portal(client, portal, require_active=False)
    assert not nether.verify_portal(client, portal, require_active=True)
    assert nether.ignite_portal(client, portal)
    assert nether.verify_portal(client, portal, require_active=True)
    assert nether.enter_portal(
        client, portal, target_dimension="minecraft:the_nether", timeout=1
    )
    goto = [payload for route, payload in transport.calls if route == "goto"][-1]
    # The lowest lit interior block, found by scanning rather than guessed.
    # The old (x+1, y+1, z) offset happened to match this geometry, which is
    # why it survived; see the frame-block test below for one it does not.
    assert goto == {"x": 4, "y": 65, "z": 0, "radius": 0}


def test_find_nearest_portal_uses_supported_unwrapped_find_blocks_route():
    class FindTransport(PortalTransport):
        def dispatch(self, route, payload, **kwargs):
            if route == "find_blocks":
                assert payload["blocks"] == ["minecraft:nether_portal"]
                return {
                    "found": [
                        {"x": 20, "y": 70, "z": 20, "distance": 30.0},
                        {"x": 3, "y": 65, "z": 4, "distance": 5.0},
                    ]
                }
            return super().dispatch(route, payload, **kwargs)

    transport = FindTransport()
    client = SimpleNamespace(transport=transport)
    assert nether.find_nearest_portal(client) == (3, 65, 4)


def test_fortress_detection_uses_find_blocks_and_unwrapped_response(monkeypatch):
    class FortressTransport(PortalTransport):
        def dispatch(self, route, payload, **kwargs):
            if route == "find_blocks":
                assert "minecraft:nether_bricks" in payload["blocks"]
                return {
                    "found": [
                        {
                            "x": 100 + index,
                            "y": 64,
                            "z": 200,
                            "block": "minecraft:nether_bricks",
                        }
                        for index in range(40)
                    ]
                }
            return super().dispatch(route, payload, **kwargs)

    transport = FortressTransport()
    transport.dimension = "minecraft:the_nether"
    client = SimpleNamespace(transport=transport)
    monkeypatch.setattr(nether.time, "sleep", lambda _seconds: None)
    assert nether.find_nether_fortress(client, timeout=1) == (119, 64, 200)
    assert not any(route == "scan_blocks" for route, _ in transport.calls)


def test_nether_handler_persists_portal_pair_and_fortress(monkeypatch, tmp_path):
    transport = PortalTransport()
    client = SimpleNamespace(transport=transport, mission=MissionStub())
    resources = ResourceManager(client)
    state = StateManager(checkpoint_dir=tmp_path)
    handler = nether_prep.NetherAndBlazeHandler()
    rods = {"count": 0}

    monkeypatch.setattr(nether_prep, "_ensure_raw_planks", lambda *_args: True)
    monkeypatch.setattr(
        nether_prep,
        "ensure_supplies",
        lambda *_args, **_kwargs: TaskResult.ok(),
    )
    monkeypatch.setattr(
        nether_prep,
        "_read_state_with_retry",
        lambda *_args, **_kwargs: (
            {"block_position": {"x": 0, "y": 64, "z": 0}},
            None,
        ),
    )
    monkeypatch.setattr(nether_prep, "build_nether_portal", lambda *_args: True)
    monkeypatch.setattr(nether_prep, "ignite_portal", lambda *_args: True)
    monkeypatch.setattr(
        nether_prep,
        "verify_portal",
        lambda *_args, **_kwargs: True,
    )

    def enter(_client, _portal, *, target_dimension, timeout):
        transport.dimension = target_dimension
        return True

    monkeypatch.setattr(nether_prep, "enter_portal", enter)
    monkeypatch.setattr(
        nether_prep,
        "find_nearest_portal",
        lambda *_args: (100, 65, 100),
    )
    monkeypatch.setattr(
        nether_prep,
        "find_nether_fortress",
        lambda *_args: (240, 70, -80),
    )

    def hunt(_client, target_count):
        assert target_count == 6
        rods["count"] = 6
        return 6

    monkeypatch.setattr(nether_prep, "hunt_blazes", hunt)
    monkeypatch.setattr(
        nether_prep,
        "count_item",
        lambda *_args: rods["count"],
    )

    result = handler.execute(client, resources, state)
    assert result.success
    assert transport.dimension == "minecraft:overworld"
    portals = state.get_locations("nether_portal")["nether_portal"]
    assert {entry["dimension"] for entry in portals} == {
        "overworld",
        "the_nether",
    }
    fortress = state.get_locations("nether_fortress")["nether_fortress"]
    assert (fortress[0]["x"], fortress[0]["y"], fortress[0]["z"]) == (
        240,
        70,
        -80,
    )
    assert state.get_phase_payload(Phase.NETHER_AND_BLAZE)["blaze_rods"] == 6


def _portal_reuse_handler(tmp_path):
    transport = PortalTransport()
    client = SimpleNamespace(transport=transport, mission=MissionStub())
    state = StateManager(checkpoint_dir=tmp_path)
    return nether_prep.NetherAndBlazeHandler(), client, state


def test_prepare_portal_reuses_an_existing_overworld_portal(monkeypatch, tmp_path):
    """A lit portal already standing must be adopted, not rebuilt.

    find_nearest_portal was imported here but only ever called from inside
    the Nether, so an Overworld portal the bot could see -- one it built on
    an earlier run, or one an operator placed -- was invisible to this phase.
    Live 2026-08-02: Bot16 abandoned NETHER_AND_BLAZE after three failed
    builds while a usable portal stood by the settlement.
    """
    handler, client, state = _portal_reuse_handler(tmp_path)
    built = []

    monkeypatch.setattr(nether_prep, "find_nearest_portal", lambda *_a: (12, 65, -34))
    monkeypatch.setattr(nether_prep, "verify_portal", lambda *_a, **_k: True)
    monkeypatch.setattr(
        nether_prep, "build_nether_portal",
        lambda *_a: built.append("built") or True,
    )
    # Stubbed so the pre-fix code path fails its assertion immediately rather
    # than dropping into real gathering against a stub transport and hanging.
    monkeypatch.setattr(nether_prep, "_ensure_raw_planks", lambda *_a: True)
    monkeypatch.setattr(
        nether_prep, "ensure_supplies",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("must not gather obsidian when a portal already exists")
        ),
    )

    assert handler._prepare_portal(client, state) is True
    assert built == [], "an existing portal must not trigger a rebuild"

    recorded = state.get_locations("nether_portal").get("nether_portal", [])
    assert any(
        (entry.get("x"), entry.get("y"), entry.get("z")) == (12, 65, -34)
        for entry in recorded
    ), f"portal not persisted: {recorded}"


def test_prepare_portal_ignores_an_unlit_nearby_portal_frame(monkeypatch, tmp_path):
    """A frame that fails verification is not usable; fall through to building."""
    handler, client, state = _portal_reuse_handler(tmp_path)

    monkeypatch.setattr(nether_prep, "find_nearest_portal", lambda *_a: (12, 65, -34))
    monkeypatch.setattr(nether_prep, "verify_portal", lambda *_a, **_k: False)
    monkeypatch.setattr(nether_prep, "_ensure_raw_planks", lambda *_a: True)
    monkeypatch.setattr(
        nether_prep, "ensure_supplies", lambda *_a, **_k: TaskResult.fail("no obsidian")
    )

    # Falls through to the build path, which fails here for want of materials.
    assert handler._prepare_portal(client, state) is False


def test_prepare_portal_still_builds_when_nothing_is_nearby(monkeypatch, tmp_path):
    handler, client, state = _portal_reuse_handler(tmp_path)
    built = []

    monkeypatch.setattr(nether_prep, "find_nearest_portal", lambda *_a: None)
    monkeypatch.setattr(nether_prep, "verify_portal", lambda *_a, **_k: True)
    monkeypatch.setattr(nether_prep, "_ensure_raw_planks", lambda *_a: True)
    monkeypatch.setattr(nether_prep, "ensure_supplies", lambda *_a, **_k: TaskResult.ok())
    monkeypatch.setattr(
        nether_prep, "_read_state_with_retry",
        lambda *_a, **_k: ({"block_position": {"x": 0, "y": 64, "z": 0}}, None),
    )
    monkeypatch.setattr(
        nether_prep, "build_nether_portal",
        lambda *_a: built.append("built") or True,
    )
    monkeypatch.setattr(nether_prep, "ignite_portal", lambda *_a: True)

    assert handler._prepare_portal(client, state) is True
    assert built, "with no portal nearby the bot must still build one"


def _lit_portal_transport(base_y=64, height=3):
    """Transport whose portal interior spans base_y .. base_y+height-1."""
    t = PortalTransport()
    for dy in range(height):
        for dx in (0, 1):
            t.blocks[(-156, base_y + dy, -278 + dx)] = "minecraft:nether_portal"
    return t


def test_enter_portal_targets_the_portal_base_not_its_middle(monkeypatch):
    """radius 0 is a Baritone GoalBlock: the bot must stand exactly there.

    A portal interior is 3 blocks tall and find_nearest_portal returns
    whichever block the scan hit first, so targeting the middle or top asks
    the bot to stand in mid-air -- unreachable, so it never arrives. Live
    2026-08-02: Bot07 aimed at y=65 and Bot17 at y=66 of a y=64..66 portal
    and both burned all three retries 8-14 blocks short.
    """
    transport = _lit_portal_transport(base_y=64, height=3)
    client = SimpleNamespace(transport=transport, mission=MissionStub())

    # Handed the MIDDLE block, as find_nearest_portal would return.
    assert nether.enter_portal(
        client, (-156, 65, -278), target_dimension="minecraft:the_nether", timeout=1
    )
    goto = [p for route, p in transport.calls if route == "goto"][-1]
    assert goto["y"] == 64, f"expected the portal base, got y={goto['y']}"
    assert (goto["x"], goto["z"]) == (-156, -278)


def test_enter_portal_targets_the_base_when_handed_the_top_block(monkeypatch):
    transport = _lit_portal_transport(base_y=64, height=3)
    client = SimpleNamespace(transport=transport, mission=MissionStub())

    assert nether.enter_portal(
        client, (-156, 66, -278), target_dimension="minecraft:the_nether", timeout=1
    )
    goto = [p for route, p in transport.calls if route == "goto"][-1]
    assert goto["y"] == 64, f"expected the portal base, got y={goto['y']}"


def test_enter_portal_leaves_an_already_basal_target_alone(monkeypatch):
    transport = _lit_portal_transport(base_y=64, height=3)
    client = SimpleNamespace(transport=transport, mission=MissionStub())

    assert nether.enter_portal(
        client, (-156, 64, -278), target_dimension="minecraft:the_nether", timeout=1
    )
    goto = [p for route, p in transport.calls if route == "goto"][-1]
    assert goto["y"] == 64


def test_portal_standing_level_stops_at_the_floor():
    """Must not walk below the portal into whatever is underneath it."""
    transport = _lit_portal_transport(base_y=70, height=3)
    transport.blocks[(-156, 69, -278)] = "minecraft:obsidian"
    client = SimpleNamespace(transport=transport)
    assert nether._portal_standing_level(client, (-156, 72, -278)) == 70


def _portal_state(tmp_path, locations):
    state = StateManager(checkpoint_dir=tmp_path)
    for loc in locations:
        state.add_location("nether_portal", loc["x"], loc["y"], loc["z"],
                           dimension=loc["dimension"], tags=loc.get("tags"))
    return state


def test_enter_nether_tries_every_persisted_portal(monkeypatch, tmp_path):
    """A dead coordinate at the head of the list must not fail the objective.

    Fleet-observed sightings are stored with an "active" tag but are never
    verified. Live 2026-08-03: Bot07 logged "Refusing to enter inactive
    portal" 120 times against (-144, 69, -278) -- not a portal at all --
    while the real portal at (-156, 64, -278) sat third in its own list.
    """
    handler = nether_prep.NetherAndBlazeHandler()
    transport = PortalTransport()
    client = SimpleNamespace(transport=transport, mission=MissionStub())
    state = _portal_state(tmp_path, [
        {"x": -144, "y": 69, "z": -278, "dimension": "overworld",
         "tags": ["active", "entry", "shared", "observed"]},   # phantom, first
        {"x": -156, "y": 64, "z": -278, "dimension": "overworld",
         "tags": ["active", "entry"]},                          # the real one
    ])

    tried = []

    def fake_enter(_client, portal, *, target_dimension, timeout):
        tried.append(portal)
        return portal == (-156, 64, -278)      # only the real portal works

    monkeypatch.setattr(nether_prep, "enter_portal", fake_enter)
    monkeypatch.setattr(nether_prep, "find_nearest_portal", lambda *_a: (-19, 85, -34))
    monkeypatch.setattr(handler, "_current_dimension", lambda _c: "minecraft:overworld")

    assert handler._enter_nether(client, state) is True
    assert (-156, 64, -278) in tried, f"never tried the live portal: {tried}"
    assert len(tried) >= 2, "must fall through the dead coordinate"


def test_enter_nether_fails_only_when_every_portal_is_dead(monkeypatch, tmp_path):
    handler = nether_prep.NetherAndBlazeHandler()
    client = SimpleNamespace(transport=PortalTransport(), mission=MissionStub())
    state = _portal_state(tmp_path, [
        {"x": -144, "y": 69, "z": -278, "dimension": "overworld", "tags": ["active"]},
        {"x": -141, "y": 68, "z": -279, "dimension": "overworld", "tags": ["shared"]},
    ])
    tried = []
    monkeypatch.setattr(
        nether_prep, "enter_portal",
        lambda _c, portal, **_k: tried.append(portal) or False,
    )
    monkeypatch.setattr(handler, "_current_dimension", lambda _c: "minecraft:overworld")

    assert handler._enter_nether(client, state) is False
    assert len(tried) == 2, "must exhaust every candidate before failing"


def test_verified_active_portals_are_tried_before_second_hand_sightings(tmp_path):
    state = _portal_state(tmp_path, [
        {"x": -144, "y": 69, "z": -278, "dimension": "overworld",
         "tags": ["shared", "observed"]},
        {"x": -156, "y": 64, "z": -278, "dimension": "overworld",
         "tags": ["active", "entry"]},
    ])
    order = nether_prep.NetherAndBlazeHandler._persisted_portal_candidates(
        state, "overworld"
    )
    assert order[0] == (-156, 64, -278), f"active portal must sort first: {order}"


def test_all_fleet_observed_portals_are_recorded_not_just_the_first(monkeypatch, tmp_path):
    """Shared sightings are unverified; one dead entry must not hide a live one.

    periodic_visible_scan keeps landmarks for portals that no longer exist.
    Returning on the first sighting persisted a phantom and left the real
    portal unknown to the entry step. Live 2026-08-03: Bot07 and Bot16 each
    logged 96 "Refusing to enter inactive portal" against (-144, 69, -278)
    while (-156, 64, -278) sat in the same shared catalog.
    """
    handler = nether_prep.NetherAndBlazeHandler()
    client = SimpleNamespace(transport=PortalTransport(), mission=MissionStub())
    state = StateManager(checkpoint_dir=tmp_path)

    monkeypatch.setattr(nether_prep, "verify_portal", lambda *_a, **_k: False)
    monkeypatch.setattr(nether_prep, "find_nearest_portal", lambda *_a: None)
    monkeypatch.setattr(
        nether_prep, "catalog_for",
        lambda _c, _s: SimpleNamespace(list_landmarks=lambda _cat: [
            {"x": -144, "y": 69, "z": -278, "dimension": "minecraft:overworld"},
            {"x": -156, "y": 64, "z": -278, "dimension": "minecraft:overworld"},
            {"x": -19, "y": 85, "z": -35, "dimension": "minecraft:the_nether"},
        ]),
    )

    assert handler._prepare_portal(client, state) is True
    persisted = handler._persisted_portal_candidates(state, "overworld")
    assert (-156, 64, -278) in persisted, f"live portal not persisted: {persisted}"
    assert (-144, 69, -278) in persisted, "phantom should still be a candidate to try"
    assert len(persisted) == 2, persisted


class _LazyChunkTransport(PortalTransport):
    """A portal the bot cannot see until it walks into render distance.

    Models the live failure exactly: get_block over an unloaded chunk answers
    void_air, and find_blocks -- which only scans loaded chunks -- returns
    nothing, so nothing about the portal is knowable from a distance.
    """

    def __init__(self, portal_base):
        super().__init__()
        self.portal_base = portal_base
        self.position = {"x": portal_base[0] + 98, "y": 70, "z": portal_base[2]}

    @property
    def _loaded(self):
        return abs(self.position["x"] - self.portal_base[0]) <= 16

    def dispatch(self, route, payload, **_kwargs):
        if route in ("get_block", "find_blocks") and self._loaded:
            x, y, z = self.portal_base
            for dy in (0, 1, 2):
                self.blocks[(x, y + dy, z)] = "minecraft:nether_portal"
            self.found_blocks = [{"x": x, "y": y + dy, "z": z} for dy in (0, 1, 2)]
        elif route == "get_block" and not self._loaded:
            self.calls.append((route, dict(payload)))
            return {"id": "minecraft:void_air"}
        return super().dispatch(route, payload, **_kwargs)


def test_enter_portal_approaches_a_portal_in_an_unloaded_chunk():
    """void_air means 'chunk not loaded', not 'portal is gone'.

    Refusing on void_air was self-defeating: a bot cannot load the chunk
    without walking there, so a portal it had never stood near could never
    be entered. Live 2026-08-03: Bot07 sat 98 blocks from a verified-live
    portal, read all three blocks as void_air, and refused 96 times.
    """
    transport = _LazyChunkTransport(portal_base=(-156, 64, -278))
    client = SimpleNamespace(transport=transport, mission=MissionStub())

    assert nether.enter_portal(
        client, (-156, 64, -278), target_dimension="minecraft:the_nether", timeout=1
    )
    goto = [p for route, p in transport.calls if route == "goto"]
    assert goto, "must path toward an unloaded portal instead of refusing"
    # First move is an approach (GoalNear); only once the chunk is loaded and
    # the interior is visible does it commit to a radius-0 GoalBlock.
    assert goto[0]["radius"] > 0, f"first move must be an approach: {goto[0]}"
    assert (goto[-1]["x"], goto[-1]["y"], goto[-1]["z"]) == (-156, 64, -278)
    assert goto[-1]["radius"] == 0


def test_enter_portal_targets_the_interior_not_the_named_frame_block():
    """The persisted coordinate can name a frame corner, not the purple middle.

    The old code guessed the interior as (x+1, y+1, z), which assumes an axis
    and a corner convention. On a Z-axis portal that lands in obsidian, and a
    radius-0 GoalBlock there is unsatisfiable -- Baritone drops it without
    moving. Live 2026-08-03: Bot17 sat 13 blocks from a live portal for 150s
    at zero velocity, burning every retry.
    """
    transport = PortalTransport()
    named = (-156, 66, -278)          # a frame block the catalog knows about
    transport.blocks[named] = "minecraft:obsidian"
    for y in (64, 65, 66):            # the real interior, one column over
        transport.blocks[(-156, y, -277)] = "minecraft:nether_portal"
    transport.found_blocks = [
        {"x": -156, "y": y, "z": -277} for y in (64, 65, 66)
    ]
    client = SimpleNamespace(transport=transport, mission=MissionStub())

    assert nether.enter_portal(
        client, named, target_dimension="minecraft:the_nether", timeout=1
    )
    goto = [p for route, p in transport.calls if route == "goto"]
    assert (goto[-1]["x"], goto[-1]["y"], goto[-1]["z"]) == (-156, 64, -277), (
        f"must stand in the portal's lowest interior block, got {goto[-1]}"
    )


def test_enter_portal_still_refuses_a_genuinely_absent_portal():
    """A loaded chunk showing plain air is real evidence the portal is gone."""
    transport = PortalTransport()
    transport.blocks[(-144, 69, -278)] = "minecraft:stone"   # loaded, not a portal
    client = SimpleNamespace(transport=transport, mission=MissionStub())

    assert nether.enter_portal(
        client, (-144, 69, -278), target_dimension="minecraft:the_nether", timeout=1
    ) is False
    assert not [p for route, p in transport.calls if route == "goto"], \
        "must not walk to a coordinate proven not to hold a portal"


def test_fortress_search_pushes_outward_when_the_bot_stops_moving(monkeypatch):
    """Scanning is not searching.

    find_nether_fortress dispatched explore once and then rescanned on a
    timer. Baritone ends explore on its own, and nothing noticed, so the bot
    stood still re-reading the same 32-block sphere for the rest of the
    600s timeout. Live 2026-08-03: Bot07, Bot16 and Bot18 each held exactly
    one coordinate for 210s inside this loop.
    """

    class StuckTransport(PortalTransport):
        """A bot whose explore has quietly ended: it never moves itself."""

        def __init__(self):
            super().__init__()
            self.dimension = "minecraft:the_nether"
            self.position = {"x": 0, "y": 70, "z": 0}
            self.explores = []
            self.gotos = []

        def dispatch(self, route, payload, **kwargs):
            if route == "explore":
                self.explores.append(dict(payload))
                return {"started": True}
            if route == "goto":
                self.gotos.append(dict(payload))
                return {"started": True}   # deliberately does NOT move
            if route == "find_blocks":
                return {"found": []}
            return super().dispatch(route, payload, **kwargs)

    transport = StuckTransport()
    client = SimpleNamespace(transport=transport, mission=MissionStub())
    monkeypatch.setattr(nether.time, "sleep", lambda _seconds: None)
    # Drive the scan clock rather than waiting out a real 60s timeout.
    clock = {"now": 0.0}

    def fake_time():
        clock["now"] += 3.0
        return clock["now"]

    monkeypatch.setattr(nether.time, "time", fake_time)

    assert nether.find_nether_fortress(client, timeout=60) is None

    assert len(transport.explores) > 1, (
        "a stalled search must re-issue explore, not rescan one spot forever"
    )
    assert transport.gotos, "must commit to a waypoint to leave the dead area"
    headings = {
        (
            (g["x"] > 0) - (g["x"] < 0),
            (g["z"] > 0) - (g["z"] < 0),
        )
        for g in transport.gotos
    }
    assert len(headings) > 1, f"must try more than one direction: {transport.gotos}"


def test_marooned_bot_escapes_instead_of_cycling_headings_forever(monkeypatch):
    """No bearing is walkable off a ledge; rotating them is not a search.

    Being stuck is a property of the terrain, not the heading. Live
    2026-08-03: Bot16 and Bot18 sat on the same glowstone blob at
    (-132, 78, -7) in a basalt delta -- air underneath, air on every side --
    and cycled all eight compass headings every 20 seconds for over an hour
    without moving a block.
    """

    class LedgeTransport(PortalTransport):
        def __init__(self):
            super().__init__()
            self.dimension = "minecraft:the_nether"
            self.position = {"x": -132, "y": 78, "z": -7}

        def dispatch(self, route, payload, **kwargs):
            if route in ("goto", "explore"):
                return {"started": True}      # nothing is walkable
            if route == "find_blocks":
                return {"found": []}
            return super().dispatch(route, payload, **kwargs)

    transport = LedgeTransport()
    client = SimpleNamespace(transport=transport, mission=MissionStub())
    monkeypatch.setattr(nether.time, "sleep", lambda _seconds: None)
    clock = {"now": 0.0}

    def fake_time():
        clock["now"] += 3.0
        return clock["now"]

    monkeypatch.setattr(nether.time, "time", fake_time)

    escapes = []

    def fake_egress(_client, state, **kwargs):
        escapes.append(kwargs)
        return None      # no progress -> must not spin on it forever

    monkeypatch.setattr(nether, "try_lower_surface_egress", fake_egress)

    nether.find_nether_fortress(client, timeout=600)

    assert escapes, (
        "a bot that refuses every heading must be treated as marooned, "
        "not handed a ninth heading"
    )
    assert escapes[0].get("minimum_altitude") == 0, (
        "the Overworld high-shelf altitude guard would refuse to help at y=78"
    )


def test_marooned_descent_is_pressed_until_it_stops_making_progress(monkeypatch):
    """One escape call per eight-heading circuit is one block per few minutes.

    Live 2026-08-03: Bot16's descent bought a single block, then control
    returned to the heading rotation and the counter reset, so it inched one
    block in five minutes while still marooned.
    """

    class LedgeTransport(PortalTransport):
        def __init__(self):
            super().__init__()
            self.dimension = "minecraft:the_nether"
            self.position = {"x": -132, "y": 78, "z": -7}

        def dispatch(self, route, payload, **kwargs):
            if route in ("goto", "explore"):
                return {"started": True}
            if route == "find_blocks":
                return {"found": []}
            return super().dispatch(route, payload, **kwargs)

    transport = LedgeTransport()
    client = SimpleNamespace(transport=transport, mission=MissionStub())
    monkeypatch.setattr(nether.time, "sleep", lambda _seconds: None)
    clock = {"now": 0.0}

    def fake_time():
        clock["now"] += 3.0
        return clock["now"]

    monkeypatch.setattr(nether.time, "time", fake_time)

    calls = {"n": 0}

    def descending_egress(_client, _state, **_kwargs):
        calls["n"] += 1
        if calls["n"] > 4:
            return None                    # ran out of room to descend
        transport.position = {
            **transport.position,
            "y": transport.position["y"] - 1,
        }
        return (-132, transport.position["y"], -7)

    monkeypatch.setattr(nether, "try_lower_surface_egress", descending_egress)

    nether.find_nether_fortress(client, timeout=600)

    assert calls["n"] > 1, (
        "a descent that is still making progress must be pressed again "
        "immediately, not after another full heading circuit"
    )


def test_nether_egress_looks_for_blocks_that_exist_in_the_nether():
    """Overworld ground blocks do not occur in the Nether.

    try_lower_surface_egress searched for grass/dirt/stone, found nothing,
    and silently no-op'd for a genuinely marooned bot.
    """
    from baritone_client.common import surface_egress

    nether_blocks = surface_egress._surface_blocks_for(
        {"dimension": "minecraft:the_nether"}
    )
    assert "minecraft:netherrack" in nether_blocks
    assert "minecraft:basalt" in nether_blocks
    assert not any("grass" in block for block in nether_blocks)

    overworld = surface_egress._surface_blocks_for({"dimension": "minecraft:overworld"})
    assert "minecraft:grass_block" in overworld


def test_column_descent_helps_a_marooned_bot_below_the_overworld_shelf_height(
    monkeypatch,
):
    """The y<96 gate refused the one escape that fits the situation.

    supported_column_descent exists precisely for "void underfoot, blocks in
    inventory": it places a block beneath the floor, then removes the floor,
    stepping down safely. Live 2026-08-03: Bot16 stood on a single glowstone
    block at y=78 in the Nether over 28 blocks of pure air, carrying 82
    cobblestone, and the altitude gate returned None before looking at
    anything. Whether this is the right escape depends on the void, not on
    the absolute height.
    """
    from baritone_client.common import shelf_escape

    class VoidLedgeTransport(PortalTransport):
        def __init__(self):
            super().__init__()
            self.dimension = "minecraft:the_nether"
            self.position = {"x": -132, "y": 78, "z": -7}
            self.broke = []

        def dispatch(self, route, payload, **kwargs):
            if route == "get_block":
                key = (payload["x"], payload["y"], payload["z"])
                if key == (-132, 77, -7):
                    return {"id": "minecraft:glowstone"}   # the one floor block
                return {"id": "minecraft:air"}             # void everywhere else
            if route == "break_block":
                self.broke.append(dict(payload))
                self.position = {**self.position, "y": self.position["y"] - 1}
                return {"broken": True}
            if route == "cancel":
                return {}
            if route == "get_inventory":
                return {"inventory": [{"slot": 0, "id": "minecraft:cobblestone", "count": 82}]}
            return super().dispatch(route, payload, **kwargs)

    transport = VoidLedgeTransport()
    client = SimpleNamespace(transport=transport, mission=MissionStub())
    from baritone_client.common import automation_utils

    placed = []
    monkeypatch.setattr(
        automation_utils,
        "place_block",
        lambda _c, x, y, z, item: (placed.append((x, y, z, item)), True)[1],
    )

    # The default gate refuses outright at y=78 ...
    assert (
        shelf_escape.supported_column_descent(
            client, {"block_position": dict(transport.position)}
        )
        is None
    )
    # ... but a caller that has proven the bot is marooned gets a descent.
    transport.position = {"x": -132, "y": 78, "z": -7}
    result = shelf_escape.supported_column_descent(
        client,
        {"block_position": dict(transport.position)},
        minimum_altitude=0,
        target_y=70,
        max_steps=12,
    )
    assert result is not None, "a marooned bot over a void must be able to descend"
    assert transport.broke, "descent must actually remove the floor it stands on"
    assert placed, "each step must place a support block before removing the floor"


def test_blaze_hunt_widens_when_local_fortress_cells_are_exhausted(monkeypatch):
    class ExhaustedFortressTransport(PortalTransport):
        def __init__(self):
            super().__init__()
            self.dimension = "minecraft:the_nether"
            self.position = {"x": 0, "y": 70, "z": 0}

        def dispatch(self, route, payload, **kwargs):
            if route == "find_blocks":
                return {
                    "found": [
                        {
                            "x": 1,
                            "y": 70,
                            "z": 1,
                            "block": "minecraft:nether_bricks",
                        }
                    ]
                }
            if route in ("goto", "explore"):
                self.calls.append((route, dict(payload)))
                return {"started": True}
            return super().dispatch(route, payload, **kwargs)

    transport = ExhaustedFortressTransport()
    client = SimpleNamespace(transport=transport)
    monkeypatch.setattr(nether, "count_item", lambda *_args: 0)
    monkeypatch.setattr(nether.time, "sleep", lambda _seconds: None)
    clock = {"now": 0.0}

    def fake_time():
        clock["now"] += 1.0
        return clock["now"]

    monkeypatch.setattr(nether.time, "time", fake_time)

    assert nether.hunt_blazes(client, target_count=6, timeout=20) == 0

    widened = [
        payload
        for route, payload in transport.calls
        if route == "goto" and payload.get("radius") == 12
    ]
    assert len(widened) > 1
    assert len({(item["x"], item["z"]) for item in widened}) > 1


def test_blaze_hunt_widens_when_local_fortress_scan_is_empty(monkeypatch):
    class EmptyFortressTransport(PortalTransport):
        def __init__(self):
            super().__init__()
            self.dimension = "minecraft:the_nether"
            self.position = {"x": -205, "y": 87, "z": 82}

        def dispatch(self, route, payload, **kwargs):
            if route == "find_blocks":
                return {"found": []}
            if route in ("goto", "explore"):
                self.calls.append((route, dict(payload)))
                return {"started": True}
            return super().dispatch(route, payload, **kwargs)

    transport = EmptyFortressTransport()
    client = SimpleNamespace(transport=transport)
    monkeypatch.setattr(nether, "count_item", lambda *_args: 0)
    monkeypatch.setattr(nether.time, "sleep", lambda _seconds: None)
    clock = {"now": 0.0}

    def fake_time():
        clock["now"] += 1.0
        return clock["now"]

    monkeypatch.setattr(nether.time, "time", fake_time)

    assert nether.hunt_blazes(client, target_count=6, timeout=20) == 0

    widened = [
        payload
        for route, payload in transport.calls
        if route == "goto" and payload.get("radius") == 12
    ]
    assert widened
    assert all(payload["y"] == 87 for payload in widened)


class _BlazeSearchTransport(PortalTransport):
    """A Nether bot that walks toward its goal at a realistic pace."""

    def __init__(self, fortress_blocks):
        super().__init__()
        self.dimension = "minecraft:the_nether"
        self.position = {"x": 0, "y": 64, "z": 0}
        self.fortress_blocks = fortress_blocks
        self.gotos = []
        self.goal = None

    def dispatch(self, route, payload, **kwargs):
        if route == "goto":
            self.gotos.append(dict(payload))
            self.goal = (payload["x"], payload["y"], payload["z"])
            return {"started": True}
        if route == "explore":
            return {"started": True}
        if route == "get_state":
            # Creep one block per poll toward the active goal.
            if self.goal:
                for axis, index in (("x", 0), ("z", 2)):
                    delta = self.goal[index] - self.position[axis]
                    if delta:
                        self.position[axis] += 1 if delta > 0 else -1
            return {
                "dimension": self.dimension,
                "block_position": dict(self.position),
            }
        if route == "get_inventory":
            return {"inventory": []}
        if route == "find_blocks":
            return {
                "found": [
                    {"x": x, "y": y, "z": z, "block": "minecraft:nether_bricks"}
                    for x, y, z in self.fortress_blocks
                ]
            }
        return super().dispatch(route, payload, **kwargs)


def test_blaze_frontier_targets_the_nearest_unvisited_cell(monkeypatch):
    """Targeting the farthest frontier block guarantees it is never reached.

    The cell is only marked explored once the bot stands in it, so a target
    it cannot reach inside one scan interval is re-chosen forever. Live
    2026-08-03: Bot17 re-issued a goto to (-260, 47, 131) every 10 seconds
    and covered 10 blocks in three minutes.
    """
    transport = _BlazeSearchTransport([(8, 64, 0), (400, 64, 0)])
    client = SimpleNamespace(transport=transport, mission=MissionStub())
    monkeypatch.setattr(nether.time, "sleep", lambda _seconds: None)

    nether._advance_blaze_frontier(
        client,
        transport.fortress_blocks,
        set(),
        [],
        current=(0, 64, 0),
        center=(0, 64, 0),
        frontier_index=0,
    )

    assert transport.gotos, "must issue a goto"
    assert (transport.gotos[0]["x"], transport.gotos[0]["z"]) == (8, 0), (
        f"must head for the nearest unvisited cell, got {transport.gotos[0]}"
    )


def test_blaze_frontier_waits_for_arrival_before_retargeting(monkeypatch):
    """A goto followed by sleep(3) is replaced before the bot has walked.

    Live 2026-08-03: Bot07 issued a new frontier goto every five seconds and
    covered 30 blocks in three minutes; Bot18 raced from ring 14 to 15 through
    six coordinates in 30 seconds without arriving at any of them.
    """
    transport = _BlazeSearchTransport([(20, 64, 0)])
    client = SimpleNamespace(transport=transport, mission=MissionStub())
    monkeypatch.setattr(nether.time, "sleep", lambda _seconds: None)

    nether._advance_blaze_frontier(
        client,
        transport.fortress_blocks,
        set(),
        [],
        current=(0, 64, 0),
        center=(0, 64, 0),
        frontier_index=0,
    )

    assert len(transport.gotos) == 1, "one destination per advance, not a stream"
    # It must actually be there when it hands control back, otherwise the next
    # scan re-reads the same starting area.
    assert abs(transport.position["x"] - 20) <= 6, (
        f"must arrive before advancing, stopped at {transport.position}"
    )
