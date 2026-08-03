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
