"""
Phase 4: Nether Logic
"""

from ..phase_executor import PhaseHandler
from ..resource_manager import ResourceManager
from ..state_manager import Phase, StateManager
from ...common import TaskResult, SequentialTask, ActionTask
from ...common.resources import ensure_supplies, _read_state_with_retry
from ...common.inventory import _ensure_raw_planks
from ...common.inventory import count_item
from ...common.landmark_scanner import import_shared_landmarks
from ...common.storage_catalog import catalog_for
from ...common.nether import (
    build_nether_portal,
    enter_portal,
    find_nearest_portal,
    find_nether_fortress,
    hunt_blazes,
    ignite_portal,
    verify_portal,
)

class NetherAndBlazeHandler(PhaseHandler):
    """Phase 4: Nether exploration - Hour 3-4."""
    
    def get_name(self) -> str:
        return "Nether Phase (Hour 3-4)"
    
    def execute(self, client, resources: ResourceManager, state: StateManager) -> TaskResult:
        tasks = [
            ActionTask(
                "Build, ignite, and verify Nether portal",
                lambda c: self._prepare_portal(c, state),
            ),
            ActionTask("Enter Nether", lambda c: self._enter_nether(c, state)),
            ActionTask(
                "Locate and persist fortress",
                lambda c: self._locate_fortress(c, state),
            ),
            ActionTask(
                "Kill blazes -> 6+ rods",
                lambda c: self._hunt_blazes(c, state),
            ),
            ActionTask(
                "Return to Overworld",
                lambda c: self._return_to_overworld(c, state),
            ),
        ]

        executor = SequentialTask("Nether Phase", tasks)
        result = executor.run(client)
        if not result.success:
            return result
        rods = count_item(client, "minecraft:blaze_rod")
        payload = {
            "blaze_rods": rods,
            "portal_pair": state.get_locations("nether_portal").get(
                "nether_portal", []
            ),
            "fortress": state.get_locations("nether_fortress").get(
                "nether_fortress", []
            ),
        }
        state.record_phase_payload(Phase.NETHER_AND_BLAZE, payload)
        return TaskResult.ok("Nether portal and blaze supply verified", **payload)

    @staticmethod
    def _current_dimension(client) -> str:
        snapshot = client.transport.dispatch("get_state", {})
        return str(snapshot.get("dimension", "")).lower()

    @staticmethod
    def _persisted_portal_candidates(state: StateManager, dimension: str):
        """Return every persisted portal for a dimension, best guess first.

        Entries tagged ``active`` were verified by this bot when it built or
        adopted the portal; ``shared``/``observed`` ones are second-hand
        sightings that may never have been real. Returning them in insertion
        order meant the first sighting won regardless of quality.
        """
        locations = state.get_locations("nether_portal").get("nether_portal", [])
        matches = [
            location
            for location in locations
            if dimension in str(location.get("dimension", "")).lower()
        ]
        matches.sort(key=lambda loc: 0 if "active" in (loc.get("tags") or []) else 1)
        candidates = []
        for location in matches:
            try:
                candidates.append(
                    (int(location["x"]), int(location["y"]), int(location["z"]))
                )
            except (KeyError, TypeError, ValueError):
                continue
        return candidates

    @classmethod
    def _persisted_portal(cls, state: StateManager, dimension: str):
        candidates = cls._persisted_portal_candidates(state, dimension)
        return candidates[0] if candidates else None

    def _prepare_portal(self, client, state: StateManager) -> bool:
        """Build, ignite, verify, and persist the Overworld portal."""
        if "nether" in self._current_dimension(client):
            return True
        try:
            import_shared_landmarks(client, state)
        except Exception as exc:
            print(f"  Shared landmark import deferred: {exc}")
        persisted = self._persisted_portal(state, "overworld")
        if persisted and verify_portal(client, persisted, require_active=True):
            return True

        # Adopt any lit portal already standing nearby before spending an
        # expedition on 14 obsidian and a flint & steel. find_nearest_portal
        # was already imported here but only ever used from inside the Nether
        # to locate the return portal, so an Overworld portal the bot could
        # see -- one it built on an earlier run, or one an operator placed --
        # was invisible to this phase. Live 2026-08-02: Bot16 abandoned
        # NETHER_AND_BLAZE after three failed builds while a usable portal
        # stood by the settlement.
        nearby = find_nearest_portal(client, "overworld")
        active_portal_block = False
        if nearby:
            try:
                block = client.transport.dispatch(
                    "get_block",
                    {"x": nearby[0], "y": nearby[1], "z": nearby[2]},
                )
                data = block.get("data", block) if isinstance(block, dict) else {}
                active_portal_block = (
                    str(data.get("id") or data.get("block") or "")
                    == "minecraft:nether_portal"
                )
            except Exception:
                pass
        if nearby and (
            active_portal_block
            or verify_portal(client, nearby, require_active=True)
        ):
            print(f"  Reusing existing nether portal at {nearby}")
            state.add_location(
                "nether_portal",
                *nearby,
                dimension="overworld",
                tags=["active", "entry", "observed"],
            )
            return True

        # A fleet peer may have observed a portal outside this bot's loaded
        # radius. The entry step must still path there and prove the dimension
        # transition, so this avoids both blind trust and duplicate building.
        try:
            shared = catalog_for(client, state).list_landmarks("nether_portal")
        except Exception:
            shared = []
        recorded = 0
        for landmark in shared:
            dimension = str(landmark.get("dimension", "")).lower()
            if "overworld" not in dimension:
                continue
            try:
                portal = (
                    int(landmark["x"]),
                    int(landmark["y"]),
                    int(landmark["z"]),
                )
            except (KeyError, TypeError, ValueError):
                continue
            print(f"  Using fleet-observed nether portal at {portal}")
            state.add_location(
                "nether_portal",
                *portal,
                dimension=dimension,
                tags=["active", "entry", "shared", "observed"],
            )
            recorded += 1
        # Record every sighting rather than returning on the first. These are
        # unverified second-hand observations and periodic_visible_scan does
        # keep entries for portals that no longer exist, so stopping at the
        # first one persisted a phantom and hid the live portal entirely from
        # the entry step. Live 2026-08-03: Bot07 and Bot16 each logged 96
        # "Refusing to enter inactive portal" against (-144, 69, -278) while
        # the real portal at (-156, 64, -278) was also in the shared catalog.
        # _enter_nether tries every persisted candidate, so handing it the
        # full set lets a dead sighting cost one attempt instead of the phase.
        if recorded:
            return True

        if not _ensure_raw_planks(client, 4):
            print(
                "  Could not prepare planks for portal support; proceeding with "
                "minimal-material fallback."
            )
        materials_ready = ensure_supplies(
            client,
            {"minecraft:obsidian": 14, "minecraft:flint_and_steel": 1},
            timeout=180,
        )
        if not materials_ready.success:
            print("  Could not gather portal materials; cannot build nether portal.")
            return False

        # Get current position
        snapshot, _ = _read_state_with_retry(
            client, retries=2, label="Portal build state read"
        )
        if snapshot is None:
            print(
                "  Portal position read timed out; using last-known safe origin "
                "(0, 64, 0) for this attempt."
            )
            snapshot = {"block_position": {"x": 0, "y": 64, "z": 0}}
        pos = snapshot.get("block_position", snapshot.get("position", {}))
        px = int(pos.get("x", 0))
        py = int(pos.get("y", 64))
        pz = int(pos.get("z", 0))

        # Try nearby offsets if the first construction attempt is blocked.
        candidate_offsets = (
            (3, 0),
            (3, 2),
            (0, 3),
            (-3, 0),
            (0, -2),
            (0, 2),
        )
        for offset_x, offset_z in candidate_offsets:
            x = px + int(offset_x)
            z = pz + int(offset_z)
            print(f"  Attempting portal frame at ({x}, {py}, {z})")
            portal = (x, py, z)
            if build_nether_portal(client, *portal) and ignite_portal(client, portal):
                state.add_location(
                    "nether_portal",
                    x,
                    py,
                    z,
                    dimension="overworld",
                    tags=["active", "entry"],
                )
                return True
        print("  All portal placement attempts failed; will retry later.")
        return False

    def _enter_nether(self, client, state: StateManager) -> bool:
        if "nether" in self._current_dimension(client):
            return True
        # Try every persisted portal, not just the first. Fleet-observed
        # sightings are recorded with an "active" tag without ever being
        # verified, so one dead coordinate at the head of the list used to
        # fail the whole objective while a live portal sat further down.
        # Live 2026-08-03: Bot07 logged "Refusing to enter inactive portal"
        # 120 times against (-144, 69, -278) -- not a portal at all -- while
        # the real one at (-156, 64, -278) was third in its own list.
        candidates = self._persisted_portal_candidates(state, "overworld")
        if not candidates:
            return False
        entered = False
        for portal in candidates:
            if enter_portal(
                client,
                portal,
                target_dimension="minecraft:the_nether",
                timeout=60,
            ):
                entered = True
                break
            print(f"  Portal at {portal} did not admit entry; trying the next one.")
        if not entered:
            return False
        nether_portal = find_nearest_portal(client, "the_nether")
        if nether_portal is None:
            return False
        state.add_location(
            "nether_portal",
            *nether_portal,
            dimension="the_nether",
            tags=["active", "return"],
        )
        return True

    def _locate_fortress(self, client, state: StateManager) -> bool:
        known = state.get_locations("nether_fortress").get("nether_fortress", [])
        if known:
            fortress = known[0]
            client.transport.dispatch(
                "goto",
                {
                    "x": int(fortress["x"]),
                    "y": int(fortress["y"]),
                    "z": int(fortress["z"]),
                    "radius": 8,
                },
            )
            return True
        fortress = find_nether_fortress(client)
        if fortress is None:
            return False
        state.add_location(
            "nether_fortress",
            *fortress,
            dimension="the_nether",
            tags=["verified", "blaze_source"],
        )
        return True

    @staticmethod
    def _hunt_blazes(client, state: StateManager) -> bool:
        return hunt_blazes(client, target_count=6, state=state) >= 6

    def _return_to_overworld(self, client, state: StateManager) -> bool:
        """Return through portal to Overworld."""
        if "overworld" in self._current_dimension(client):
            return True
        portal = self._persisted_portal(state, "the_nether")
        portal = portal or find_nearest_portal(client, "the_nether")
        if portal is None:
            return False
        return enter_portal(
            client,
            portal,
            target_dimension="minecraft:overworld",
            timeout=60,
        )
