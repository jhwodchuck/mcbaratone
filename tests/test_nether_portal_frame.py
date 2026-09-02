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


def test_the_builder_places_exactly_the_blocks_the_verifier_demands():
    """The coupling that makes a partial change worse than no change."""
    placed = []
    blocks = {}

    class Transport:
        def dispatch(self, route, payload):
            if route == "get_block":
                key = (payload["x"], payload["y"], payload["z"])
                return {"id": blocks.get(key, "minecraft:air")}
            return {}

    client = SimpleNamespace(
        transport=Transport(),
        mission=SimpleNamespace(
            macro=lambda _n, params: {"result": {"ready": params["obsidian"] >= 10}},
            checkpoint=lambda *_a, **_k: None,
        ),
    )

    import baritone_client.common.nether as mod

    original_place, original_sleep = mod.place_block, mod.time.sleep
    try:
        def place(_client, px, py, pz, item_id):
            placed.append((px, py, pz))
            blocks[(px, py, pz)] = item_id
            return True

        mod.place_block = place
        mod.time.sleep = lambda _s: None
        assert mod.build_nether_portal(client, 0, 64, 0)
    finally:
        mod.place_block, mod.time.sleep = original_place, original_sleep

    assert len(placed) == 10, placed
    # Exactly the verifier's set -- no wasted block, none missing.
    assert set(placed) == set(nether._frame_positions(0, 64, 0))


def test_every_obsidian_gate_agrees_on_ten():
    """Four independent numbers guard the portal; all must say ten."""
    assert nether_prep.PORTAL_FRAME_OBSIDIAN == 10

    # 1. the build default, forwarded to the Java macro's `requiredObsidian`
    default = inspect.signature(nether.build_nether_portal).parameters["obsidian"].default
    assert default == 10

    # 2. the ensure_supplies gate that actually fired on A1
    source = inspect.getsource(nether_prep)
    assert '"minecraft:obsidian": 14' not in source
    assert '"minecraft:obsidian": PORTAL_FRAME_OBSIDIAN' in source

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
