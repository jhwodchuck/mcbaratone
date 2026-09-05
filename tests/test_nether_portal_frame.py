"""A Nether portal costs ten obsidian, and the four gates must agree.

Vanilla's portal shape check never tests the four corner positions -- they sit
diagonal to the interior. The load-bearing frame is two bottom blocks, two top
blocks, and two three-block sides: ten, not fourteen.

`end_readiness` already encoded the real figure ("verified portal or 10
obsidian") while the build path demanded 14, so the bot could be judged
portal-ready and still refuse to build. Live on A1 2026-09-02, carrying 9
obsidian and a diamond pickaxe, NETHER_AND_BLAZE failed "Could not gather
portal materials" on a ~3 minute cycle for hours after wandering 330 blocks
looking for natural obsidian that no longer existed nearby -- while it was
already carrying enough for a legal portal, minus one.

These four gates are COUPLED. Lowering any subset is worse than lowering none:
carrying 10 obsidian into the old placement loops spends 4 on corners and
leaves the upper side blocks unplaced, so the frame never completes.
"""

from __future__ import annotations

import inspect
from types import SimpleNamespace

from baritone_client.automator import resource_manager
from baritone_client.automator.phases import nether_prep
from baritone_client.common import nether


def _goto_stopped(client, *_args, **_kwargs):
    client._last_navigation_cancel = {"cancel_status": "stopped"}
    return True


def test_the_frame_is_ten_blocks_and_excludes_the_corners():
    x, y, z = 100, 64, 100
    frame = set(nether._frame_positions(x, y, z))

    assert len(frame) == 10, sorted(frame)

    corners = {(x, y, z), (x + 3, y, z), (x, y + 4, z), (x + 3, y + 4, z)}
    assert frame.isdisjoint(corners), "vanilla never checks the corners"

    # Everything the portal genuinely needs is still required.
    expected = {(x + 1, y, z), (x + 2, y, z), (x + 1, y + 4, z), (x + 2, y + 4, z)}
    expected |= {(x, y + dy, z) for dy in (1, 2, 3)}
    expected |= {(x + 3, y + dy, z) for dy in (1, 2, 3)}
    assert frame == expected

    # The interior must stay clear of the frame, or the portal cannot light.
    assert frame.isdisjoint(set(nether._portal_interior(x, y, z)))


def test_arrived_but_uncertain_cleanup_never_spends_portal_blocks(monkeypatch):
    from baritone_client.common import portal_construction as construction

    client = SimpleNamespace()
    monkeypatch.setattr(construction, "_block_at", lambda *_args: "minecraft:air")
    monkeypatch.setattr(construction, "_is_solid", lambda *_args: True)
    monkeypatch.setattr(
        construction,
        "get_inventory",
        lambda *_args: {"minecraft:obsidian": 1},
    )
    placed = []
    monkeypatch.setattr(
        construction,
        "place_block",
        lambda *_args: placed.append(True) or True,
    )

    def arrived_with_unknown_cancel(active_client, *_args, **_kwargs):
        active_client._last_navigation_cancel = {"cancel_status": "unknown"}
        return True

    monkeypatch.setattr(construction, "goto", arrived_with_unknown_cancel)
    assert not construction.construct_frame(
        client,
        (0, 64, 0),
        {(1, 64, 0)},
        {(1, 65, 0)},
    )
    assert placed == []


def test_the_builder_places_exactly_the_blocks_the_verifier_demands():
    """The coupling that makes a partial change worse than no change."""
    placed = []
    # The site needs a floor for the bottom pair to anchor against.
    blocks = {(fx, 63, z): "minecraft:stone" for fx in range(0, 4) for z in (-1, 0, 1)}

    class Transport:
        def dispatch(self, route, payload):
                if route == "get_block":
                    key = (payload["x"], payload["y"], payload["z"])
                    return {"id": blocks.get(key, "minecraft:air")}
                if route == "get_inventory":
                    return {"inventory": [
                        {"id": "minecraft:obsidian", "count": 10},
                        {"id": "minecraft:cobblestone", "count": 4},
                    ], "armor": [], "offhand": []}
                return {}

    client = SimpleNamespace(
        transport=Transport(),
        mission=SimpleNamespace(
            macro=lambda _n, params: {"result": {"ready": params["obsidian"] >= 10}},
            checkpoint=lambda *_a, **_k: None,
        ),
    )

    import baritone_client.common.nether as mod

    from baritone_client.common import portal_construction as construction
    original_place, original_goto, original_sleep = construction.place_block, construction.goto, construction.time.sleep
    try:
        def place(_client, px, py, pz, item_id):
            placed.append((px, py, pz))
            blocks[(px, py, pz)] = item_id
            return True

        construction.place_block = place
        construction.goto = _goto_stopped
        construction.time.sleep = lambda _s: None
        assert mod.build_nether_portal(client, 0, 64, 0)
    finally:
        construction.place_block, construction.goto, construction.time.sleep = original_place, original_goto, original_sleep

    assert len(placed) == 14, placed
    assert sum(blocks[p] == "minecraft:obsidian" for p in placed) == 10
    assert sum(blocks[p] == "minecraft:cobblestone" for p in placed) == 4
    assert set(placed) == set(nether._frame_positions(0, 64, 0)) | {
        (0, 64, 0), (3, 64, 0), (0, 68, 0), (3, 68, 0)
    }


def test_every_obsidian_gate_agrees_on_ten():
    """Four independent numbers guard the portal; all must say ten."""
    assert nether_prep.PORTAL_FRAME_OBSIDIAN == 10

    # 1. the build default, forwarded to the Java macro's `requiredObsidian`
    default = inspect.signature(nether.build_nether_portal).parameters["obsidian"].default
    assert default == 10

    # 2. the ensure_supplies gate that actually fired on A1
    source = inspect.getsource(nether_prep)
    assert '"minecraft:obsidian": 14' not in source
    assert '"minecraft:obsidian": max(0, PORTAL_FRAME_OBSIDIAN - installed)' in source

    # 3. the phase requirement table
    from baritone_client.automator.resource_manager import Phase

    table = resource_manager.ResourceManager.PHASE_REQUIREMENTS
    assert table[Phase.NETHER_AND_BLAZE]["minecraft:obsidian"] == 10

    # 4. the readiness criterion, which was right all along
    from baritone_client.automator import end_readiness

    assert 'supplies["obsidian"] >= 10' in inspect.getsource(end_readiness)


def test_an_obstructed_site_places_nothing_at_all():
    """The leak: a doomed build used to spend obsidian before discovering it.

    Live on A1 2026-09-02 at y=-32 in deepslate, the bot reached the build
    carrying exactly 10 obsidian -- the first time the supply gate had ever let
    it through. Each candidate offset placed a block or two, then raised
    "Target position is already occupied" on the first solid position. The
    handler caught it and returned False, but the blocks already placed were
    gone from the inventory. Two attempts later it held 8, fell under its own
    requirement, and every further offset failed on `hasObsidian: False`.

    So a site that cannot be cleared must cost nothing.
    """
    placed = []
    solid = {}

    class Transport:
        def dispatch(self, route, payload):
            key = (payload.get("x"), payload.get("y"), payload.get("z"))
            if route == "get_block":
                return {"id": solid.get(key, "minecraft:air")}
            if route == "get_inventory":
                return {"inventory": [
                    {"id": "minecraft:obsidian", "count": 10},
                    {"id": "minecraft:cobblestone", "count": 4},
                ], "armor": [], "offhand": []}
            if route == "dig_block":
                return {}  # refuses to break: the block stays put
            return {}

    client = SimpleNamespace(
        transport=Transport(),
        mission=SimpleNamespace(
            macro=lambda _n, params: {"result": {"ready": params["obsidian"] >= 10}},
            checkpoint=lambda *_a, **_k: None,
        ),
    )

    for fx in range(0, 4):
        solid[(fx, 63, 0)] = "minecraft:stone"
    # One unbreakable block anywhere in the volume is enough.
    solid[(1, 64, 0)] = "minecraft:deepslate"

    import baritone_client.common.nether as mod
    import baritone_client.common.automation_utils as utils

    orig_place, orig_sleep = mod.place_block, mod.time.sleep
    orig_usleep, orig_clock = utils.time.sleep, utils.time.monotonic
    clock = iter(range(0, 10_000))
    try:
        mod.place_block = lambda *_a, **_k: placed.append(_a) or True
        mod.time.sleep = lambda _s: None
        utils.time.sleep = lambda _s: None
        # Let the dig deadline expire immediately instead of burning 8s of wall
        # clock waiting for a block the stub will never break.
        utils.time.monotonic = lambda: next(clock)
        assert not mod.build_nether_portal(client, 0, 64, 0)
    finally:
        mod.place_block, mod.time.sleep = orig_place, orig_sleep
        utils.time.sleep, utils.time.monotonic = orig_usleep, orig_clock

    assert placed == [], f"leaked {len(placed)} obsidian into a doomed site"


def test_an_unreadable_block_counts_as_occupied():
    """A transport blip must not be guessed as air -- that is how stacks leak."""
    from baritone_client.common.automation_utils import clear_placement_volume

    client = SimpleNamespace(
        transport=SimpleNamespace(dispatch=lambda _route, _payload: {})
    )
    assert not clear_placement_volume(client, [(0, 64, 0)])


def _world(blocks, placed, fail_at=None):
    """A block world whose place_block refuses positions in `fail_at`."""
    state = dict(blocks)
    floor_x = {x for x, y, z in state if y == 63 and z == 0}
    for x in floor_x:
        state.setdefault((x, 63, -1), "minecraft:stone")
        state.setdefault((x, 63, 1), "minecraft:stone")

    class Transport:
        def dispatch(self, route, payload):
            key = (payload.get("x"), payload.get("y"), payload.get("z"))
            if route == "get_block":
                return {"id": state.get(key, "minecraft:air")}
            if route == "get_inventory":
                return {"inventory": [
                    {"id": "minecraft:obsidian", "count": 10},
                    {"id": "minecraft:cobblestone", "count": 4},
                ], "armor": [], "offhand": []}
            if route == "dig_block":
                state[key] = "minecraft:air"
                if key in placed:
                    placed.remove(key)
                return {}
            return {}

    return state, Transport()


def test_an_unanchored_site_is_refused_before_any_obsidian_is_spent():
    """A cleared site is not automatically a buildable one.

    Live on A1 2026-09-02 at y=-48, immediately after site-clearing shipped:
    three attempts in six seconds, each into an air pocket that passed every
    "is it clear?" test and then failed on placement.

        Building Nether portal frame at (-74, -48, 99)
        ERROR Portal construction failed: No solid block found to place
              against at BlockPos{x=-73, y=-48, z=99}

    Obsidian went 10 -> 8 across those attempts.
    """
    placed = []
    # Pure air: clear everywhere, anchored nowhere.
    state, transport = _world({}, placed)
    client = SimpleNamespace(
        transport=transport,
        mission=SimpleNamespace(
            macro=lambda _n, params: {"result": {"ready": params["obsidian"] >= 10}},
            checkpoint=lambda *_a, **_k: None,
        ),
    )

    import baritone_client.common.nether as mod

    from baritone_client.common import portal_construction as construction
    orig_place, orig_goto, orig_sleep = construction.place_block, construction.goto, construction.time.sleep
    try:
        construction.place_block = lambda *_a, **_k: placed.append(_a) or True
        construction.goto = _goto_stopped
        construction.time.sleep = lambda _s: None
        assert not mod.build_nether_portal(client, 0, 64, 0)
    finally:
        construction.place_block, construction.goto, construction.time.sleep = orig_place, orig_goto, orig_sleep

    assert placed == [], "an air pocket must cost nothing"


def test_the_frame_is_placed_lowest_first_so_each_block_has_an_anchor():
    """Placing the top row before its own side supports is unbuildable."""
    placed = []
    state, transport = _world(
        {(fx, 63, 0): "minecraft:stone" for fx in range(0, 4)}, placed
    )
    client = SimpleNamespace(
        transport=transport,
        mission=SimpleNamespace(
            macro=lambda _n, params: {"result": {"ready": True}},
            checkpoint=lambda *_a, **_k: None,
        ),
    )

    import baritone_client.common.nether as mod

    order = []
    from baritone_client.common import portal_construction as construction
    orig_place, orig_goto, orig_sleep = construction.place_block, construction.goto, construction.time.sleep
    try:
        def place(_c, px, py, pz, _item):
            order.append((px, py, pz))
            state[(px, py, pz)] = _item
            return True

        construction.place_block = place
        construction.goto = _goto_stopped
        construction.time.sleep = lambda _s: None
        mod.build_nether_portal(client, 0, 64, 0)
    finally:
        construction.place_block, construction.goto, construction.time.sleep = orig_place, orig_goto, orig_sleep

    heights = [y for _x, y, _z in order]
    assert heights == sorted(heights), f"must build upward, got {order}"
    assert heights[0] == 64, "the bottom pair anchors to the floor first"
    assert heights[-1] == 68, "the top closes last, against the finished sides"


def test_a_stalled_placement_gives_the_obsidian_back():
    """A partial frame is a permanent loss unless it is dug back."""
    placed = []
    state, transport = _world(
        {(fx, 63, 0): "minecraft:stone" for fx in range(0, 4)}, placed
    )
    client = SimpleNamespace(
        transport=transport,
        mission=SimpleNamespace(
            macro=lambda _n, params: {"result": {"ready": True}},
            checkpoint=lambda *_a, **_k: None,
        ),
    )

    import baritone_client.common.nether as mod
    import baritone_client.common.automation_utils as utils

    from baritone_client.common import portal_construction as construction
    orig_place, orig_goto, orig_sleep = construction.place_block, construction.goto, construction.time.sleep
    orig_usleep = utils.time.sleep
    try:
        def place(_c, px, py, pz, item):
            if len(placed) >= 3:      # unknown fourth placement; preserve prior blocks
                return False
            placed.append((px, py, pz))
            state[(px, py, pz)] = item
            return True

        construction.place_block = place
        construction.goto = _goto_stopped
        construction.time.sleep = lambda _s: None
        assert not mod.build_nether_portal(client, 0, 64, 0)
    finally:
        construction.place_block, construction.goto, construction.time.sleep = orig_place, orig_goto, orig_sleep
        utils.time.sleep = orig_usleep

    assert len(placed) == 3, "verified partial blocks must remain for same-site reconciliation"
    assert all(state[position] in {"minecraft:obsidian", "minecraft:cobblestone"} for position in placed)


def test_resumed_partial_frame_spends_only_missing_obsidian(monkeypatch):
    """A durable two-block partial frame resumes in place without replay."""
    from baritone_client.common import portal_construction as construction

    origin = (0, 64, 0)
    frame = nether._frame_positions(*origin)
    blocks = {(x, 63, z): "minecraft:stone" for x in range(4) for z in (-1, 0, 1)}
    blocks[frame[0]] = "minecraft:obsidian"
    blocks[frame[1]] = "minecraft:obsidian"
    placed = []

    class Transport:
        def dispatch(self, route, payload):
            key = (payload.get("x"), payload.get("y"), payload.get("z"))
            if route == "get_block":
                return {"id": blocks.get(key, "minecraft:air")}
            if route == "get_inventory":
                return {"inventory": [
                    {"id": "minecraft:obsidian", "count": 8},
                    {"id": "minecraft:cobblestone", "count": 4},
                ], "armor": [], "offhand": []}
            return {}

    client = SimpleNamespace(
        transport=Transport(),
        mission=SimpleNamespace(checkpoint=lambda *_a, **_k: None),
    )

    def place(_client, x, y, z, item):
        placed.append((x, y, z, item))
        blocks[(x, y, z)] = item
        return True

    monkeypatch.setattr(construction, "place_block", place)
    monkeypatch.setattr(construction, "goto", _goto_stopped)
    monkeypatch.setattr(construction.time, "sleep", lambda _seconds: None)
    assert nether.build_nether_portal(client, *origin)
    assert sum(item == "minecraft:obsidian" for *_p, item in placed) == 8
    assert sum(item == "minecraft:cobblestone" for *_p, item in placed) == 4
    assert set(frame) <= {position for position in blocks if blocks[position] == "minecraft:obsidian"}
