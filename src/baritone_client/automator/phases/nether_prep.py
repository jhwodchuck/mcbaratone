"""
Phase 4: Nether Logic
"""

from ..phase_executor import PhaseHandler
from ..resource_manager import ResourceManager
from ..state_manager import Phase, StateManager
from ...common import TaskResult, SequentialTask, ActionTask
from ...common.resources import ensure_supplies, _read_state_with_retry
from ...common.inventory import (
    _ensure_raw_planks,
    armor_piece_is_durable,
    count_item,
    equip_best_armor,
    equip_best_weapon,
    has_durable_full_armor,
    has_full_armor,
    withdraw_required_from_catalog,
)
from ...common.combat import (
    _emergency_food_count,
    acquire_emergency_food,
    eat_until_hunger,
)
from ...common.landmark_scanner import import_shared_landmarks
from ...common.health_recovery import recover_health
from ...common.storage_catalog import catalog_for
from .iron_age_food import recover_food_from_known_sources
from ...common.nether import (
    _block_id,
    _frame_positions,
    _portal_interior,
    build_nether_portal,
    enter_portal,
    find_nearest_portal,
    find_nether_fortress,
    hunt_blazes,
    ignite_portal,
    verify_portal,
)

#: A portal's four corners sit diagonal to the interior and vanilla's shape
#: check never tests them, so a frame costs ten obsidian, not fourteen. This
#: gate demanded 14 while ``end_readiness`` already judged the bot ready at 10,
#: so A1 could be portal-ready and still refuse to build: live 2026-09-02 it
#: failed "Could not gather portal materials" for hours holding 9 obsidian.
PORTAL_FRAME_OBSIDIAN = 10

# Candidate origins are relative to the bot's current feet position, nearest
# ring first -- the same expanding-ring idiom obsidian_casting._lava_candidates
# already uses. A home base accumulates chests, a furnace, and a house right
# where the bot spends most of its time, and the original six points at one
# fixed radius all sat inside that clutter: live A1 2026-09-05 failed site
# selection on 100% of NETHER_AND_BLAZE attempts over an hour. A live probe
# from the bot's actual position found nothing pristine within radius 7 in any
# of 8 directions -- the first clear pocket was at radius 16. Radii go to 20
# to leave margin past that measured worst case, not because 16 was assumed
# sufficient elsewhere.
#
# Cost is paid only in the failure case: the loop returns on the first match,
# and open ground resolves within the first ring or two (a separate live
# check succeeded at radius 3 from open terrain). It is the base-adjacent
# case, which is also the common one, that needs the full width.
#
# This widens the search only. Once found, a candidate must still pass the
# identical pristine/reachable check below -- read-only, no mutation until one
# fully qualifies -- and once a plan is saved the code reuses that exact
# origin forever after. Widening the candidate list cannot reintroduce the
# site-scattering the original finite set was written to prevent; only a
# saved plan controls that, and this list is never consulted once one exists.
_PORTAL_SITE_RADII = (3, 5, 7, 9, 11, 13, 16, 20)
_PORTAL_SITE_ANGLES = (
    (1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1), (0, -1), (1, -1),
)
_PORTAL_SITE_OFFSETS = tuple(
    (radius * dx, radius * dz)
    for radius in _PORTAL_SITE_RADII
    for dx, dz in _PORTAL_SITE_ANGLES
)
_PORTAL_AIR = {"minecraft:air", "minecraft:cave_air"}
_PORTAL_UNKNOWN = {"", "minecraft:void_air", "minecraft:unloaded"}


def _portal_foundation_is_solid(block):
    """Reject air, fluids, and unknown reads as portal foundations."""
    return (
        block is not None
        and block not in _PORTAL_AIR
        and block not in _PORTAL_UNKNOWN
        and not any(token in block for token in ("water", "lava", "fire"))
    )


def _read_block_for_site(client, position):
    """Return a block id, or ``None`` when the bridge did not prove it."""
    try:
        response = client.transport.dispatch(
            "get_block", {"x": position[0], "y": position[1], "z": position[2]}
        )
    except Exception:
        return None
    if not isinstance(response, dict):
        return None
    data = response.get("data", response)
    if not isinstance(data, dict):
        return None
    block = str(data.get("id") or data.get("type") or data.get("block") or "")
    return None if block in _PORTAL_UNKNOWN else block


def _portal_site_is_pristine_and_reachable(client, origin):
    """Prove a candidate is an empty, grounded, reachable portal site.

    This is intentionally read-only. The check includes the full frame,
    interior, optional corner support cells, and a standing pose. A missing or
    unloaded block is unknown evidence and rejects the candidate.
    """
    x, y, z = origin
    corners = {(x + dx, y + dy, z) for dx in (0, 3) for dy in (0, 4)}
    frame = set(_frame_positions(x, y, z))
    interior = set(_portal_interior(x, y, z))
    positions = frame | interior | corners
    observed = {position: _read_block_for_site(client, position) for position in positions}
    if any(block is None for block in observed.values()):
        return False
    if any(observed[position] not in _PORTAL_AIR for position in frame | interior | corners):
        return False

    # The bottom row and the two approach poses need actual solid footing.
    for floor_x in range(x, x + 4):
        floor = _read_block_for_site(client, (floor_x, y - 1, z))
        if not _portal_foundation_is_solid(floor):
            return False
    for pose in ((x + 1, y, z - 1), (x + 1, y, z + 1)):
        feet = _read_block_for_site(client, pose)
        head = _read_block_for_site(client, (pose[0], pose[1] + 1, pose[2]))
        floor = _read_block_for_site(client, (pose[0], pose[1] - 1, pose[2]))
        if feet in _PORTAL_AIR and head in _PORTAL_AIR and _portal_foundation_is_solid(floor):
            return True
    return False


def _choose_portal_site(client, snapshot):
    """Choose one proven site, without issuing any world mutation."""
    position = snapshot.get("block_position", snapshot.get("position", {}))
    if not isinstance(position, dict) or not all(key in position for key in ("x", "y", "z")):
        return None
    try:
        px, py, pz = (int(position[key]) for key in ("x", "y", "z"))
    except (TypeError, ValueError):
        return None
    for dx, dz in _PORTAL_SITE_OFFSETS:
        candidate = (px + dx, py, pz + dz)
        if _portal_site_is_pristine_and_reachable(client, candidate):
            return candidate
    return None


class NetherAndBlazeHandler(PhaseHandler):
    """Phase 4: Nether exploration - Hour 3-4."""
    
    def get_name(self) -> str:
        return "Nether Phase (Hour 3-4)"
    
    def execute(self, client, resources: ResourceManager, state: StateManager) -> TaskResult:
        rods = count_item(client, "minecraft:blaze_rod")
        if rods >= 6:
            if "nether" in self._current_dimension(client):
                if not self._return_to_overworld(client, state):
                    return TaskResult.fail(
                        "Blaze supply is ready but the bot could not return safely"
                    )
            return self._record_blaze_supply(client, state)

        tasks = [
            ActionTask(
                "Verify Nether expedition loadout",
                lambda c: self._ensure_nether_readiness(c, state),
            ),
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
        return self._record_blaze_supply(client, state)

    @staticmethod
    def _record_blaze_supply(client, state: StateManager) -> TaskResult:
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
        # expedition on ten obsidian and a flint & steel. find_nearest_portal
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

        # Keep the chosen site across failures and restarts. Existing obsidian
        # at that site counts as installed progress, not a lost resource.
        from ...common.inventory import get_inventory
        plan = state.custom_data.get("portal_build_plan")
        if not isinstance(plan, dict) or plan.get("dimension") != "overworld":
            snapshot, _ = _read_state_with_retry(client, retries=2, label="Portal build state read")
            if not snapshot:
                return False
            origin = _choose_portal_site(client, snapshot)
            if origin is None:
                print("  No loaded, grounded, reachable portal site is available.")
                return False
            plan = {"dimension": "overworld", "origin": list(origin), "status": "planned"}
            state.custom_data["portal_build_plan"] = plan
            try:
                state.save_checkpoint(get_inventory(client))
            except Exception as exc:
                print(f"  Portal site plan could not be durably checkpointed: {exc}")
                # A plan that exists only in memory must not authorize a later
                # mutation. The next phase invocation must re-prove the site
                # and checkpoint it again before spending any materials.
                state.custom_data.pop("portal_build_plan", None)
                return False
        try:
            portal = tuple(int(v) for v in plan["origin"])
            if len(portal) != 3:
                return False
            installed = sum(
                _block_id(
                    client.transport.dispatch(
                        "get_block", {"x": p[0], "y": p[1], "z": p[2]}
                    )
                )
                == "minecraft:obsidian"
                for p in _frame_positions(*portal)
            )
        except Exception:
            return False
        # Non-flammable corner supports. Their observed presence is reconciled
        # by construct_frame; never use obsidian for the optional corners.
        materials_ready = ensure_supplies(
            client,
            {
                "minecraft:obsidian": max(0, PORTAL_FRAME_OBSIDIAN - installed),
                "minecraft:cobblestone": 4,
                "minecraft:flint_and_steel": 1,
            },
            timeout=180,
        )
        if not materials_ready.success:
            print("  Could not gather portal materials; cannot build nether portal.")
            return False

        if build_nether_portal(client, *portal) and ignite_portal(client, portal):
            plan["status"] = "active_verified"
            state.add_location("nether_portal", *portal, dimension="overworld", tags=["active", "entry"])
            try:
                state.save_checkpoint(get_inventory(client))
            except Exception as exc:
                print(f"  Portal is active but progress checkpoint failed: {exc}")
                return False
            return True
        plan["status"] = "interrupted_reconcile"
        try:
            state.save_checkpoint(get_inventory(client))
        except Exception as exc:
            print(f"  Portal interruption could not be checkpointed: {exc}")
        return False

    @staticmethod
    def _nether_loadout_ready(client, snapshot=None) -> bool:
        """Require a durable combat kit, health, hunger, and reserve food."""
        snapshot = snapshot or client.transport.dispatch("get_state", {})
        health = float(snapshot.get("health", 0) or 0)
        food = int(snapshot.get("food_level", snapshot.get("food", 0)) or 0)
        return (
            not bool(snapshot.get("is_dead", False))
            and health >= 18.0
            and food >= 18
            and has_full_armor(client, minimum_material="iron")
            and has_durable_full_armor(client, minimum_material="iron")
            and count_item(client, "minecraft:shield") >= 1
            and _emergency_food_count(client) >= 6
            and equip_best_weapon(client)
        )

    def _ensure_nether_readiness(self, client, state: StateManager) -> bool:
        """Build and verify the loadout before allowing Nether progression.

        A completed FOOD_AND_IRON checkpoint proves historical progress, not
        the bot's equipment after a death. Re-check live inventory every time
        this phase resumes so a naked respawn cannot walk back into a fortress.
        """
        snapshot = client.transport.dispatch("get_state", {})
        if self._nether_loadout_ready(client, snapshot):
            return True

        if "nether" in str(snapshot.get("dimension", "")).lower():
            print("  Nether loadout is unsafe; returning to the Overworld to rearm.")
            self._return_to_overworld(client, state)
            # Restart this task after the dimension transition; never combine
            # portal travel and a potentially long rearm operation in one run.
            return False

        # Armor first, hunger second. Both of the steps below are free or paid
        # for with iron already in the bag, neither needs food, and an armored
        # bot is far likelier to survive the foraging the hunger gate demands.
        #
        # The old order deadlocked the entire fleet on 2026-08-06: all six bots
        # sat at zero food, so none of them ever reached this code. Three were
        # carrying unequipped armor and two were holding 40 and 66 iron ingots
        # they could not spend, while the defense supervisor kept reporting
        # "only 0/4 armor pieces" and they died to ordinary hostiles.
        #
        # Grave recovery returns equipment to ordinary inventory slots, so
        # equipping is often all that is needed.
        equip_best_armor(client)
        equip_best_weapon(client)
        self._craft_armor_from_carried_iron(client)

        if not eat_until_hunger(client, minimum_food=18):
            if (
                not acquire_emergency_food(
                    client,
                    minimum_health=12.0,
                    minimum_food=18,
                    timeout=180.0,
                    max_exploration_distance=64.0,
                )
                or not eat_until_hunger(client, minimum_food=18)
            ):
                # The bounded local hunt above can never succeed in an
                # animal-sparse biome -- it retries the same empty area
                # forever. Live A1 2026-09-06: rearm rotated through a dozen
                # empty 64-block sweeps across several failed attempts (and
                # two deaths) while a farm or herd FOOD_AND_IRON had already
                # verified sat unused in this same checkpoint. Try it before
                # giving up, the same fallback FOOD_AND_IRON's own hunger
                # gate already uses.
                if not recover_food_from_known_sources(
                    client, state, minimum_food=18
                ):
                    print("  Nether rearm paused until hunger can be stabilized.")
                    return False
        if not recover_health(client, minimum_health=18.0, timeout=60.0):
            print("  Nether rearm paused until health can be stabilized.")
            return False

        equip_best_armor(client)
        equip_best_weapon(client)

        # Provision armor in small, immediately useful increments. The old
        # path gathered one 24-27 ingot batch before crafting anything. Live
        # on Easy, Bot07 and Bot18 repeatedly died to zombies deep underground
        # during that batch with only 0-1 equipped pieces. Crafting and
        # equipping the three cheapest pieces first gives the defense runtime
        # enough armor to engage a single ordinary hostile while the remaining
        # loadout is gathered.
        armor_plan = (
            ("minecraft:iron_boots", 4),
            ("minecraft:iron_helmet", 5),
            ("minecraft:iron_leggings", 7),
        )
        for item_id, iron_cost in armor_plan:
            replace = count_item(client, item_id) >= 1 and not armor_piece_is_durable(
                client, item_id
            )
            if not self._provision_iron_gear(
                client, state, item_id, iron_cost, force_replacement=replace
            ):
                return False
            equip_best_armor(client)

        # Ask the gate's own question. _provision_iron_gear short-circuits on
        # count>=1, but weapon_score rejects a blade at <=3 durability, so a
        # worn sword counted as "provisioned" while _nether_loadout_ready kept
        # failing on equip_best_weapon: live A1 burned a day retrying the
        # Nether with a 3/250 sword it never replaced. Armor already forces
        # replacement this way; the weapon must use the same predicate as the
        # check, or the two can silently disagree again.
        replace_sword = (
            count_item(client, "minecraft:iron_sword") >= 1
            and not equip_best_weapon(client)
        )
        if not self._provision_iron_gear(
            client, state, "minecraft:iron_sword", 2, force_replacement=replace_sword
        ):
            return False
        equip_best_weapon(client)

        replace_chestplate = count_item(
            client, "minecraft:iron_chestplate"
        ) >= 1 and not armor_piece_is_durable(
            client, "minecraft:iron_chestplate"
        )
        if not self._provision_iron_gear(
            client,
            state,
            "minecraft:iron_chestplate",
            8,
            force_replacement=replace_chestplate,
        ):
            return False
        equip_best_armor(client)

        if not self._provision_iron_gear(client, state, "minecraft:shield", 1):
            return False

        if _emergency_food_count(client) < 6:
            acquire_emergency_food(
                client,
                minimum_health=18.0,
                minimum_food=18,
                minimum_reserve=6,
                timeout=180.0,
                max_exploration_distance=64.0,
            )
            if _emergency_food_count(client) < 6:
                recover_food_from_known_sources(client, state, minimum_food=18)
        eat_until_hunger(client, minimum_food=18)
        verified = self._nether_loadout_ready(client)
        if not verified:
            print(
                "  Nether loadout verification failed (full iron, shield, "
                "6 food, 18 health/hunger required)."
            )
        return verified

    @staticmethod
    def _craft_armor_from_carried_iron(client) -> int:
        """Craft missing armor from iron already carried. Best effort.

        Deliberately never gathers and never fails the caller: this runs ahead
        of the hunger gate purely to convert iron the bot is already holding
        into protection it can wear right now. The strict, gathering
        provisioning pass still runs afterwards and still decides readiness.
        """
        equipped = 0
        for item_id, iron_cost in (
            ("minecraft:iron_boots", 4),
            ("minecraft:iron_helmet", 5),
            ("minecraft:iron_leggings", 7),
            ("minecraft:iron_chestplate", 8),
        ):
            try:
                if count_item(client, item_id) >= 1:
                    continue
                if count_item(client, "minecraft:iron_ingot") < iron_cost:
                    continue
                if ensure_supplies(client, {item_id: 1}, timeout=120).success:
                    equipped += 1
                    equip_best_armor(client)
            except Exception as exc:  # pragma: no cover - defensive
                print(f"  Early armor craft for {item_id} skipped: {exc}")
        if equipped:
            print(f"  Crafted {equipped} armor piece(s) from carried iron.")
        return equipped

    @staticmethod
    def _provision_iron_gear(
        client,
        state: StateManager,
        item_id: str,
        iron_cost: int,
        *,
        force_replacement: bool = False,
    ) -> bool:
        """Gather and consume only the iron needed for one gear upgrade."""
        current = count_item(client, item_id)
        if current >= 1 and not force_replacement:
            return True
        if count_item(client, "minecraft:iron_ingot") < iron_cost:
            # Withdraw already-banked ingots before mining and smelting more
            # from scratch. Live A1 2026-09-08: this gather step alone took
            # ~10 minutes per attempt even though the home chest already held
            # spare iron gear -- ensure_supplies deliberately never touches
            # storage (see resources.py), so an explicit, checkpointed
            # objective like this one must ask first, the same way
            # villager.py's bread provisioning already does.
            withdraw_required_from_catalog(
                client,
                {"minecraft:iron_ingot": iron_cost},
                state=state,
                max_travel_distance=96.0,
                max_vertical_distance=32.0,
            )
        if count_item(client, "minecraft:iron_ingot") < iron_cost:
            ingots = ensure_supplies(
                client,
                {"minecraft:iron_ingot": iron_cost},
                timeout=600,
            )
            if not ingots.success:
                print(f"  Could not provision iron for {item_id}.")
                return False
        gear = ensure_supplies(
            client,
            {item_id: current + 1 if force_replacement else 1},
            timeout=300,
        )
        if not gear.success:
            print(f"  Could not craft {item_id}; Nether rearm remains blocked.")
            return False
        return True

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
