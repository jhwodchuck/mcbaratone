"""
Combat utilities - Mob engagement, retreat logic, and healing.
"""

import logging
import time
from typing import Dict, List, Optional
from ..core.exceptions import TransportError

from .inventory import (
    count_item,
    equip_best_weapon,
    get_equipped_armor,
    select_item,
)
from .tasks import TaskResult
from .navigation import goto

logger = logging.getLogger(__name__)


class EntityQueryError(RuntimeError):
    """The bridge entity query failed (timeout/transport/error response).

    Distinct from a successful query that returns zero entities. Silently
    collapsing the two hid a route-timeout flood as "no animals nearby":
    Bot07 spent an entire hunt reporting "No targets found" and exploring
    for leather while a cow stood three blocks away, because every
    get_entities call was timing out and being swallowed into an empty list.
    """


def get_nearby_entities(
    client, radius: int = 30, *, raise_on_error: bool = False
) -> List[Dict]:
    """
    Get list of nearby entities.

    Args:
        raise_on_error: when True, a failed bridge query raises
            EntityQueryError instead of returning ``[]``. Callers that must
            not confuse "the bridge is unreachable" with "no entities here"
            (e.g. hunt_mobs deciding whether to wander off exploring) should
            set this. Default stays False so existing best-effort callers are
            unchanged.

    Returns:
        List of entity dictionaries (empty if the area is genuinely empty, or
        on error when raise_on_error is False).
    """
    try:
        data = client.transport.dispatch("get_entities", {"radius": radius})
    except Exception as exc:
        logger.warning("get_entities bridge query failed: %s", exc)
        if raise_on_error:
            raise EntityQueryError(str(exc)) from exc
        return []
    if isinstance(data, dict) and data.get("error"):
        logger.warning("get_entities returned error: %s", data.get("error"))
        if raise_on_error:
            raise EntityQueryError(str(data.get("error")))
        return []
    return data.get("entities", [])


def entity_position(entity: Dict) -> Optional[tuple]:
    """
    Extract (x, y, z) from an entity dict.

    The bridge returns entity coordinates nested under "position"; older code
    assumed top-level x/y/z keys (which silently produced (0, 0, 0)).
    """
    pos = entity.get("position")
    if isinstance(pos, dict) and "x" in pos:
        return (pos.get("x", 0), pos.get("y", 0), pos.get("z", 0))
    if "x" in entity:
        return (entity.get("x", 0), entity.get("y", 0), entity.get("z", 0))
    return None


def look_at_entity(client, entity: Dict) -> bool:
    """
    Look at an entity using its coordinates.

    The bridge's look_at handler only accepts x/y/z (sending entity_id causes
    a NullPointerException server-side). Aim slightly above the feet so we
    face the body rather than the ground. Never raises - a failed look is not
    worth aborting a task over.
    """
    pos = entity_position(entity)
    if pos is None:
        return False
    x, y, z = pos
    try:
        client.transport.dispatch("look_at", {"x": x, "y": y + 1.0, "z": z})
        return True
    except Exception as e:
        print(f"  look_at_entity failed (non-fatal): {e}")
        return False


def find_entity_by_type(
    client,
    entity_types: List[str],
    radius: int = 30,
    *,
    raise_on_error: bool = False,
) -> Optional[Dict]:
    """
    Find nearest entity of specified types.

    Args:
        entity_types: List of entity type substrings (e.g., ["zombie", "skeleton"])
        radius: Search radius
        raise_on_error: propagate a bridge-query failure as EntityQueryError
            instead of returning None (which is otherwise indistinguishable
            from "no matching entity in range").

    Returns:
        Entity dict or None
    """
    entities = get_nearby_entities(client, radius, raise_on_error=raise_on_error)

    for entity in sorted(entities, key=lambda e: e.get("distance", 999)):
        entity_type = entity.get("type", "").lower()
        if any(t.lower() in entity_type for t in entity_types):
            return entity

    return None


def attack_nearest(
    client,
    entity_types: List[str],
    max_range: int = 10,
) -> bool:
    """
    Attack nearest entity of specified type.
    
    Args:
        client: Baritone client
        entity_types: Types to attack (e.g., ["pig", "cow"])
        max_range: Maximum attack range
        
    Returns:
        True if attacked an entity
    """
    entity = find_entity_by_type(client, entity_types, radius=max_range)
    
    if entity is None:
        return False
    
    entity_id = entity.get("id")
    if entity_id is None:
        return False
        
    # Equip weapon
    equip_best_weapon(client)
    
    try:
        # Look at entity (coordinates - the bridge does not accept entity_id here)
        look_at_entity(client, entity)
        time.sleep(0.2)

        # Attack
        client.transport.dispatch("attack_entity", {"entity_id": entity_id})
        return True

    except Exception as e:
        print(f"Attack error: {e}")
        return False


def safe_combat(
    client,
    target_id: int,
    retreat_health: float = 6.0,
    max_duration: int = 30,
    abort_on_other_hostiles: bool = False,
    tracking_radius: int = 30,
) -> bool:
    """
    Fight target with retreat logic.
    
    Args:
        client: Baritone client
        target_id: Entity ID to attack
        retreat_health: Retreat if health drops below this
        max_duration: Maximum combat duration
        
    Returns:
        True if target killed, False if retreated or failed
    """
    # Equip weapon
    equip_best_weapon(client)
    
    start = time.time()
    
    approach_failures = 0
    
    while time.time() - start < max_duration:
        # Check health
        state = client.transport.dispatch("get_state", {})
        health = state.get("health", 20)
        if health < retreat_health:
            print(f"Retreating! Health: {health}")
            client.transport.dispatch("cancel", {})
            return False
        
        # Check if target still exists
        entities = get_nearby_entities(client, radius=max(30, tracking_radius))
        target = next((e for e in entities if e.get("id") == target_id), None)

        if abort_on_other_hostiles:
            hostile_names = (
                "zombie", "skeleton", "creeper", "spider",
                "witch", "pillager", "slime",
            )
            other_threat = next(
                (
                    entity
                    for entity in entities
                    if entity.get("id") != target_id
                    and entity.get("distance", 999) <= 12
                    and any(
                        hostile in str(entity.get("type", "")).lower()
                        for hostile in hostile_names
                    )
                ),
                None,
            )
            if other_threat is not None:
                print(
                    f"Combat aborted: {other_threat.get('type')} entered "
                    f"{other_threat.get('distance', 999):.1f}m safety radius"
                )
                client.transport.dispatch("chat", {"message": "#stop"})
                client.transport.dispatch("cancel", {})
                return False
        
        if target is None:
            # Target dead or escaped
            return True
        
        dist = target.get("distance", 999)
        
        # Attack if in range
        if dist < 4.5:
            look_at_entity(client, target)
            try:
                client.transport.dispatch("attack_entity", {"entity_id": target_id})
            except Exception as exc:
                # The entity can die or unload between the entity scan and
                # attack dispatch.  That is a successful end to this combat,
                # not a phase-level exception that should crash recovery.
                if "entity not found" in str(exc).lower():
                    return True
                print(f"Combat attack failed: {exc}")
                return False
            # Reset pathing if we are close enough to just whack it
            if state.get("is_pathing", False):
                 client.transport.dispatch("chat", {"message": "#stop"})
        else:
            # A live run proved that replacing the same entity goal every
            # 200ms can keep Baritone permanently in its pre-path state.  Give
            # one coordinate snapshot a bounded chance to finish, then rescan
            # the moving target before issuing another goal.
            tpos = entity_position(target)
            if tpos is None:
                return False
            tx, ty, tz = int(tpos[0]), int(tpos[1]), int(tpos[2])
            remaining = max(1.0, max_duration - (time.time() - start))
            approached = goto(
                client,
                tx,
                ty,
                tz,
                timeout=min(15, int(remaining)),
                check_interval=0.5,
                tolerance=3.0,
            )
            if approached:
                approach_failures = 0
            else:
                approach_failures += 1
                if approach_failures >= 3:
                    print("DEBUG: Baritone failed three bounded target approaches")
                    client.transport.dispatch("cancel", {})
                    return False

        time.sleep(0.2)
    
    return False


EMERGENCY_FOOD_ITEMS = [
    "minecraft:cooked_beef", "minecraft:cooked_porkchop",
    "minecraft:cooked_chicken", "minecraft:cooked_mutton",
    "minecraft:bread", "minecraft:apple", "minecraft:cooked_salmon",
    "minecraft:cooked_cod", "minecraft:baked_potato",
    "minecraft:golden_apple", "minecraft:beef",
    "minecraft:porkchop", "minecraft:chicken",
    "minecraft:mutton", "minecraft:rabbit",
    "minecraft:salmon", "minecraft:cod",
    "minecraft:rotten_flesh",
]

# Mob-type substrings whose kill drops raw food, i.e. hunting them is itself
# a way back to a stable hunger level.
_FOOD_YIELDING_MOBS = ("cow", "mooshroom", "sheep", "pig", "chicken", "rabbit")


def _emergency_food_count(client) -> int:
    return sum(count_item(client, item_id) for item_id in EMERGENCY_FOOD_ITEMS)


def eat_until_hunger(client, minimum_food: int = 14) -> bool:
    """Consume carried food until the hunger bar is safe for progression."""
    minimum_food = max(1, min(int(minimum_food), 20))
    attempts = 0
    while attempts < 10:
        state = client.transport.dispatch("get_state", {})
        food_level = int(state.get("food_level", state.get("food", 20)))
        if food_level >= minimum_food:
            return True

        selected = None
        for item_id in EMERGENCY_FOOD_ITEMS:
            if count_item(client, item_id) <= 0:
                continue
            if select_item(client, item_id, allow_swap=True):
                selected = item_id
                break
        if selected is None:
            print(f"HUNGER: no carried food available at level {food_level}")
            return False

        print(f"HUNGER: eating {selected} at level {food_level}")
        before_count = count_item(client, selected)
        # Right-clicking food while the crosshair is on the home chest opens
        # the container instead of eating.  Face harmlessly upward first so
        # the held-use action cannot interact with a nearby block.
        try:
            client.transport.dispatch("look", {"yaw": 0, "pitch": -90})
        except Exception:
            pass
        client.transport.dispatch("use_item", {"duration_ms": 2500})
        # The bridge starts a held-use action asynchronously.  Reissuing it
        # every half-second resets the eating timer, so wait for a real hunger
        # or stack-count delta before touching the selected item again.
        completed = False
        deadline = time.monotonic() + 4.0
        while time.monotonic() < deadline:
            time.sleep(0.25)
            updated = client.transport.dispatch("get_state", {})
            updated_food = int(updated.get("food_level", updated.get("food", food_level)))
            if updated_food > food_level or count_item(client, selected) < before_count:
                completed = True
                break
        if not completed:
            # The held-use release and the food update can land on the next
            # client tick just after the polling deadline.  Verify once more
            # before reporting failure; the live 1.21 client has exhibited
            # this exact late-but-successful completion.
            time.sleep(0.25)
            final_food = int(
                client.transport.dispatch("get_state", {}).get("food_level", food_level)
            )
            if final_food >= minimum_food:
                return True
            print(f"HUNGER: eating {selected} did not complete; retrying after cooldown")
            attempts += 1
            time.sleep(1.0)
            continue
        attempts += 1

    final_state = client.transport.dispatch("get_state", {})
    return int(final_state.get("food_level", final_state.get("food", 0))) >= minimum_food


def heal_if_needed(client, threshold: float = 10.0) -> bool:
    """
    Eat food if health below threshold.
    
    Args:
        client: Baritone client
        threshold: Health threshold to trigger healing
        
    Returns:
        True if healing was attempted
    """
    # Check health
    state = client.transport.dispatch("get_state", {})
    health = state.get("health", 20)
    if health >= threshold:
        return False
    
    # Early survival frequently has only raw meat or rotten flesh, and it may
    # be in the main inventory.  Move it to the hotbar instead of silently
    # ignoring every non-hotbar slot.
    for item_id in EMERGENCY_FOOD_ITEMS:
        if count_item(client, item_id) <= 0:
            continue
        if select_item(client, item_id, allow_swap=True):
            time.sleep(0.1)
            try:
                client.transport.dispatch("look", {"yaw": 0, "pitch": -90})
            except Exception:
                pass
            client.transport.dispatch("use_item", {"duration_ms": 2500})
            return True
    
    return False


def recover_health(
    client,
    minimum_health: float = 12.0,
    timeout: float = 45.0,
) -> bool:
    """Hold position and consume available food until work is safe to resume."""
    state = client.transport.dispatch("get_state", {})
    health = float(state.get("health", 20) or 0)
    if health >= minimum_health:
        return True

    print(f"RECOVERY: health {health:.1f}/{minimum_health:.1f}; stopping work")
    client.transport.dispatch("cancel", {})
    client.transport.dispatch("chat", {"message": "#stop"})

    deadline = time.time() + timeout
    last_food_attempt = 0.0
    while time.time() < deadline:
        state = client.transport.dispatch("get_state", {})
        health = float(state.get("health", 20) or 0)
        if health >= minimum_health:
            print(f"RECOVERY: safe to resume at {health:.1f} health")
            return True
        if health <= 0:
            ensure_alive(client)
            return False

        now = time.time()
        if now - last_food_attempt >= 4.0:
            last_food_attempt = now
            if not heal_if_needed(client, threshold=minimum_health):
                if _emergency_food_count(client) == 0:
                    print("RECOVERY: no edible food available; refusing unsafe work")
                    return False
        time.sleep(2)

    print(f"RECOVERY: timed out below safe health ({health:.1f})")
    return False


def acquire_emergency_food(
    client,
    minimum_health: float = 12.0,
    minimum_food: int = 14,
    timeout: float = 240.0,
    max_exploration_distance: float = 96.0,
) -> bool:
    """Recover an early-game player by hunting only passive food sources.

    This is intentionally conservative: it waits for daylight, never explores
    blind while critically wounded, and aborts when a hostile approaches.
    """
    minimum_food = max(1, min(int(minimum_food), 20))

    def recovery_complete(state: Optional[Dict] = None) -> bool:
        state = state or client.transport.dispatch("get_state", {})
        health = float(state.get("health", 20) or 0)
        food = int(state.get("food_level", state.get("food", 20)))
        return health >= minimum_health and food >= minimum_food

    recover_health(client, minimum_health=minimum_health, timeout=10.0)
    if recovery_complete():
        return True

    try:
        state = client.transport.dispatch("get_state", {})
    except Exception:
        print("RECOVERY: could not read initial state for emergency food; retrying briefly.")
        time.sleep(1.0)
        try:
            state = client.transport.dispatch("get_state", {})
        except Exception:
            state = {}
    cached_state = state if isinstance(state, dict) else {}
    if int(state.get("world_time", 0)) % 24000 >= 12000:
        print("RECOVERY: night detected; waiting in shelter before seeking food")
        from .base import wait_for_safe_daylight
        if not wait_for_safe_daylight(client, max_wait=720, poll_interval=5):
            return False

    start = time.time()
    state = client.transport.dispatch("get_state", {})
    position = state.get("block_position", state.get("position", {}))
    origin_x = float(position.get("x", state.get("x", 0)) or 0)
    origin_z = float(position.get("z", state.get("z", 0)) or 0)
    exploring = False

    def stop_exploring() -> None:
        nonlocal exploring
        if not exploring:
            return
        client.transport.dispatch("cancel", {})
        client.transport.dispatch("chat", {"message": "#stop"})
        exploring = False

    # Prefer the more nourishing land animals during emergency recovery, and
    # search the full set of loaded chunks after waiting safely for dawn.
    primary_land_food = ["cow", "pig"]
    secondary_land_food = ["sheep", "chicken", "rabbit"]
    water_food = ["salmon", "cod"]
    while time.time() - start < timeout:
        try:
            state = client.transport.dispatch("get_state", {})
            if isinstance(state, dict) and state:
                cached_state = state
        except Exception:
            if cached_state:
                print("RECOVERY: state read failed; using cached state this cycle.")
                state = cached_state
            else:
                print("RECOVERY: state unavailable and no cache; waiting before retry.")
                time.sleep(1.0)
                continue
        if recovery_complete(state):
            stop_exploring()
            return True

        day_time = int(state.get("world_time", 0)) % 24000
        if day_time >= 12000:
            stop_exploring()
            print("RECOVERY: daylight ended before emergency food was secured")
            return False

        position = state.get("block_position", state.get("position", {}))
        current_x = float(position.get("x", state.get("x", origin_x)) or origin_x)
        current_z = float(position.get("z", state.get("z", origin_z)) or origin_z)
        distance = ((current_x - origin_x) ** 2 + (current_z - origin_z) ** 2) ** 0.5
        if distance > max_exploration_distance:
            stop_exploring()
            print(
                "RECOVERY: emergency food search reached its "
                f"{max_exploration_distance:.0f}-block safety radius"
            )
            return False

        threats = scan_for_threats(client, radius=16)
        if threats and threats[0].get("distance", 999) <= 12:
            stop_exploring()
            client.transport.dispatch("cancel", {})
            from .base import _has_existing_enclosure
            if _has_existing_enclosure(client, state):
                print(
                    f"RECOVERY: {threats[0].get('type')} is "
                    f"{threats[0].get('distance', 999):.1f}m away; "
                    "holding inside verified shelter"
                )
                time.sleep(5)
                continue
            print("RECOVERY: hostile nearby and no enclosure; aborting food run")
            return False

        target = find_entity_by_type(client, primary_land_food, radius=64)
        if target is None:
            target = find_entity_by_type(client, secondary_land_food, radius=64)
        # Water-mob drops are substantially harder to collect reliably: the
        # target can vanish below the player while its item floats elsewhere.
        # When hunger is still stable, spend the first part of the bounded
        # search loading land animals instead. Keep fish as a last-resort path
        # for genuinely critical hunger or after land exploration had time to
        # work.
        land_search_elapsed = time.time() - start
        water_fallback_after = min(90.0, max(15.0, timeout / 2.0))
        if target is None and (
            int(state.get("food_level", state.get("food", 20))) <= 6
            or land_search_elapsed >= water_fallback_after
        ):
            target = find_entity_by_type(client, water_food, radius=64)
        if target is None:
            if not exploring:
                print(
                    "RECOVERY: no passive food source loaded; starting bounded "
                    "daylight exploration"
                )
                client.transport.dispatch(
                    "explore",
                    {"x": int(origin_x), "z": int(origin_z)},
                )
                exploring = True
            time.sleep(3)
            continue

        stop_exploring()

        target_id = target.get("id")
        target_pos = entity_position(target)
        if target_id is None or target_pos is None:
            time.sleep(1)
            continue

        print(
            f"RECOVERY: safely hunting {target.get('type')} at "
            f"{target.get('distance', 999):.1f}m"
        )
        if not safe_combat(
            client,
            target_id,
            retreat_health=1.0,
            max_duration=35,
            abort_on_other_hostiles=True,
        ):
            return False

        from .navigation import goto
        goto(
            client,
            int(target_pos[0]),
            int(target_pos[1]),
            int(target_pos[2]),
            timeout=45,
            tolerance=1.5,
        )
        time.sleep(2)
        heal_if_needed(client, threshold=minimum_health)
        recover_health(client, minimum_health=minimum_health, timeout=25.0)
        if recovery_complete():
            return True

    stop_exploring()
    return False


def hunt_passive_mobs(
    client,
    target_count: int = 10,
    timeout: int = 300,
    type_filter: Optional[List[str]] = None,
) -> int:
    """
    Hunt passive mobs for food.
    
    Args:
        client: Baritone client
        target_count: Number of mobs to kill
        timeout: Maximum time
        
    Returns:
        Number of mobs killed
    """
    result = hunt_mobs(
        client,
        mob_types=type_filter if type_filter else ["pig", "cow", "sheep", "chicken"],
        required_loot={"minecraft:cooked_beef": 0},
        search_radius=50,
        timeout=timeout,
        heal_threshold=5.0,
        target_kills=target_count,
    )
    return int(result.data.get("kills", 0))


def hunt_mobs(
    client,
    mob_types: List[str],
    required_loot: Dict[str, int],
    search_radius: int = 48,
    timeout: int = 600,
    heal_threshold: float = 8.0,
    target_kills: Optional[int] = None,
    abort_on_other_hostiles: bool = False,
    latest_world_time: Optional[int] = None,
    max_distance_from_origin: Optional[float] = None,
    exploration_center: Optional[tuple[int, int]] = None,
) -> TaskResult:
    """
    Hunt a set of mobs until loot requirements are satisfied.

    Args:
        mob_types: Substrings of entity types to target
        required_loot: Dict of item_id -> count
        search_radius: Radius to scan for mobs
        timeout: Maximum duration
        heal_threshold: Auto-heal when health drops below this value
        target_kills: Optional kill target to stop early even if loot collected
        abort_on_other_hostiles: Stop passive hunting when another hostile is close
        latest_world_time: Stop early enough to return before sunset
        max_distance_from_origin: Bound expedition radius around its start
        exploration_center: Explicit X/Z center that leads away from owned structures
    """
    start = time.time()
    baseline = {item: count_item(client, item) for item in required_loot}
    kills = 0
    start_state = client.transport.dispatch("get_state", {})
    start_pos = start_state.get("block_position", start_state.get("position", {}))
    origin = (
        float(start_pos.get("x", 0)),
        float(start_pos.get("z", 0)),
    )

    def _missing() -> Dict[str, int]:
        missing: Dict[str, int] = {}
        for item_id, count in required_loot.items():
            current = count_item(client, item_id) - baseline.get(item_id, 0)
            if current < count:
                missing[item_id] = count - max(current, 0)
        return missing

    missing = _missing()
    if not missing and not target_kills:
        return TaskResult.ok("Already satisfied", kills=kills, missing={})

    exploring = False
    last_time_check = 0
    
    while (missing or (target_kills and kills < target_kills)) and time.time() - start < timeout:
        live_state = client.transport.dispatch("get_state", {})
        day_time = int(live_state.get("world_time", 0)) % 24000
        if latest_world_time is not None and day_time >= int(latest_world_time):
            if exploring:
                client.transport.dispatch("chat", {"message": "#stop"})
            client.transport.dispatch("cancel", {})
            return TaskResult.fail(
                "Daylight return boundary reached",
                missing=missing,
                kills=kills,
            )
        if max_distance_from_origin is not None:
            live_pos = live_state.get(
                "block_position", live_state.get("position", {})
            )
            distance_from_origin = (
                (float(live_pos.get("x", 0)) - origin[0]) ** 2
                + (float(live_pos.get("z", 0)) - origin[1]) ** 2
            ) ** 0.5
            if distance_from_origin > float(max_distance_from_origin):
                if exploring:
                    client.transport.dispatch("chat", {"message": "#stop"})
                client.transport.dispatch("cancel", {})
                return TaskResult.fail(
                    "Passive-hunt expedition radius reached",
                    missing=missing,
                    kills=kills,
                    distance=distance_from_origin,
                )
        food_level = int(live_state.get("food_level", live_state.get("food", 20)))
        if food_level <= 10:
            client.transport.dispatch("cancel", {})
            if exploring:
                exploring = False
            if eat_until_hunger(client, minimum_food=14):
                continue
            # No carried food and none of the hunted mobs can supply it
            # (e.g. blaze/enderman/spider hunts): keep the original safety
            # net and stop rather than risk continued combat while hungry.
            hunting_food_animal = any(
                food_mob in mob_type.lower()
                for mob_type in mob_types
                for food_mob in _FOOD_YIELDING_MOBS
            )
            if not hunting_food_animal or food_level <= 3:
                return TaskResult.fail(
                    "Hunt stopped because hunger could not be stabilized",
                    missing=missing,
                    kills=kills,
                    food_level=food_level,
                )
            # These mob types (cow/mooshroom/sheep/pig/chicken/rabbit) drop
            # raw meat on death, so hunting them is itself the path back to
            # food. Aborting here deadlocked forever -- confirmed live on
            # Bot07, stuck retrying "Gather 46 leather" with an empty food
            # supply chest and nothing to eat.
            print(
                f"  HUNGER: no carried food at {food_level}; continuing hunt "
                "to find some"
            )
        # Check for night every 10 seconds
        if time.time() - last_time_check > 10:
            last_time_check = time.time()
            state = live_state
            if day_time >= 13000:
                 print("  Night detected! Aborting hunt.")
                 if exploring:
                      client.transport.dispatch("chat", {"message": "#stop"})
                 return TaskResult.fail("Night detected")

        heal_if_needed(client, threshold=heal_threshold)
        if abort_on_other_hostiles:
            nearby = get_nearby_entities(client, radius=12)
            hostile_names = (
                "zombie", "skeleton", "creeper", "spider",
                "witch", "pillager", "slime",
            )
            threat = next(
                (
                    entity
                    for entity in nearby
                    if any(
                        hostile in str(entity.get("type", "")).lower()
                        for hostile in hostile_names
                    )
                ),
                None,
            )
            if threat is not None:
                client.transport.dispatch("chat", {"message": "#stop"})
                client.transport.dispatch("cancel", {})
                return TaskResult.fail(
                    f"Hostile {threat.get('type')} entered passive-hunt radius",
                    kills=kills,
                    missing=missing,
                )
        try:
            entity = find_entity_by_type(
                client, mob_types, radius=search_radius, raise_on_error=True
            )
        except EntityQueryError as exc:
            # The bridge could not answer the entity query (route timeout /
            # transport error). This is NOT an empty area -- do not "explore"
            # away from mobs that may be right here. Back off and let the
            # controller's route-timeout-flood detector recover the bridge.
            # Swallowing this into "no targets found" is what made Bot07 hunt
            # leather for many minutes with a cow three blocks away.
            print(f"  Entity query failed (bridge issue: {exc}); pausing hunt scan.")
            if exploring:
                client.transport.dispatch("chat", {"message": "#stop"})
                exploring = False
            time.sleep(3)
            continue

        if entity is None:
            if not exploring:
                print("  No targets found, starting exploration...")
                if exploration_center is not None:
                    client.transport.dispatch(
                        "explore",
                        {
                            "x": int(exploration_center[0]),
                            "z": int(exploration_center[1]),
                        },
                    )
                else:
                    client.transport.dispatch("chat", {"message": "#explore"})
                exploring = True
            time.sleep(3)
            continue

        if exploring:
             print("  Target found! Stopping exploration.")
             client.transport.dispatch("chat", {"message": "#stop"})
             exploring = False
             time.sleep(0.5)

        target_id = entity.get("id")
        if target_id is None:
            time.sleep(1)
            continue
            
        target_pos = entity_position(entity)
        if safe_combat(
            client,
            target_id,
            retreat_health=heal_threshold - 2,
            max_duration=40,
            abort_on_other_hostiles=abort_on_other_hostiles,
            tracking_radius=search_radius,
        ):
            kills += 1
            # Entity death can be detected while the player is still near the
            # edge of melee range.  Step onto the last known position after
            # the pickup delay so leather/food is not left behind while the
            # next target is selected.
            if target_pos is not None:
                time.sleep(0.6)
                goto(
                    client,
                    int(target_pos[0]),
                    int(target_pos[1]),
                    int(target_pos[2]),
                    timeout=10,
                    check_interval=0.25,
                    tolerance=0.75,
                )
                time.sleep(0.4)
            missing = _missing()
        else:
            heal_if_needed(client, threshold=heal_threshold)
        time.sleep(1)

    gained = {
        item: max(0, count_item(client, item) - baseline.get(item, 0))
        for item in required_loot
    }

    if missing:
        return TaskResult.fail(
            "Failed to acquire required loot",
            missing=missing,
            kills=kills,
            gained=gained,
            duration=time.time() - start,
        )

    return TaskResult.ok(
        "Loot collection complete",
        missing={},
        kills=kills,
        gained=gained,
        duration=time.time() - start,
    )


def scan_for_threats(client, radius: int = 16) -> List[Dict]:
    """Return list of nearby hostile mobs sorted by distance."""
    hostiles = ["zombie", "skeleton", "creeper", "spider", "witch", "pillager", "enderman", "slime"]
    
    nearby = get_nearby_entities(client, radius)
    threats = []
    
    for entity in nearby:
        etype = entity.get("type", "").lower()
        if any(h in etype for h in hostiles):
            threats.append(entity)
            
    return sorted(threats, key=lambda e: e.get("distance", 999))


def secure_recovery_area(
    client,
    timeout: float = 90.0,
    clear_seconds: float = 10.0,
    radius: int = 16,
) -> bool:
    """Protect a recovered inventory until the surrounding area is stable.

    Death drops are commonly guarded by the mob that killed the player.  Do
    not hand control back to a long-running phase immediately after pickup,
    and do not begin slow block placement while a threat can approach.  Keep
    combat supervision attached until there have been ``clear_seconds`` of
    daylight with no nearby hostiles.
    """
    deadline = time.time() + timeout
    clear_since: Optional[float] = None

    while time.time() < deadline:
        try:
            state = client.transport.dispatch("get_state", {})
        except TransportError:
            # If the bridge times out while securing, keep the area safe and
            # continue polling rather than immediately failing recovery.
            clear_since = None
            time.sleep(0.5)
            continue
        if float(state.get("health", 20) or 0) <= 0:
            print("RECOVERY: player died while securing the pickup area")
            return False

        threats = scan_for_threats(client, radius=radius)
        if threats:
            clear_since = None
            defend_or_flee(client)
            continue

        now = time.time()
        clear_since = clear_since or now
        world_time = int(state.get("world_time", 0)) % 24000
        if world_time < 12000 and now - clear_since >= clear_seconds:
            print("RECOVERY: pickup area is clear and stable")
            return True
        time.sleep(0.5)

    print("RECOVERY: pickup area did not remain safely clear")
    return False


def run_away(client, threat: Dict):
    """Run away from a specific threat."""
    try:
        tp = entity_position(threat)
        t_pos = (tp[0], tp[2]) if tp else (0, 0)

        # Get our pos
        state = client.transport.dispatch("get_state", {})
        pos = state.get("block_position", {})
        o_x, o_z = int(pos.get("x", 0)), int(pos.get("z", 0))
        
        # Vector away
        dx = o_x - t_pos[0]
        dz = o_z - t_pos[1]
        
        # Normalize roughly
        dist = (dx*dx + dz*dz)**0.5
        if dist < 0.1: dist = 1 # Avoid div by zero
        
        # Target 25 blocks away
        target_dist = 25
        tx = int(o_x + (dx/dist) * target_dist)
        tz = int(o_z + (dz/dist) * target_dist)
        
        print(f"FLEE: Running to {tx}, {o_z} (Away from {t_pos})")
        client.transport.dispatch("goal", {"x": tx, "y": int(pos.get("y", 64)), "z": tz})
        client.transport.dispatch("chat", {"message": "#path"})
        time.sleep(1) # Let it start
        
    except Exception as e:
        print(f"Run away failed: {e}")


def ensure_alive(client) -> bool:
    """Respawn if dead. Returns True if a respawn was needed."""
    try:
        state = client.transport.dispatch("get_state", {})
        if state.get("health", 20) is not None and state.get("health", 20) <= 0:
            print("DEATH: Player is dead! Respawning...")
            client.transport.dispatch("respawn", {})
            time.sleep(3)
            for _ in range(10):
                state = client.transport.dispatch("get_state", {})
                if state.get("health", 0) > 0:
                    print(f"DEATH: Respawned at {state.get('block_position')}")
                    break
                time.sleep(1)
            return True
    except Exception as e:
        print(f"DEATH: ensure_alive check failed: {e}")
    return False


def defend_or_flee(client) -> bool:
    """
    Check surroundings. If threat:
    - If weapon: Attack
    - If no weapon: Flee

    Returns: True if action taken (was interrupted)
    """
    # Death makes every other consideration moot - and the death screen
    # blocks all commands, so long-running tasks spin forever without this.
    if ensure_alive(client):
        return True

    state = client.transport.dispatch("get_state", {})
    health = float(state.get("health", 20) or 0)
    threats = scan_for_threats(client)

    # Never re-enter combat immediately after retreating.  Previously each
    # gathering loop saw the same skeleton, attacked again, and drove the
    # player from 5 health to 1 health.  Low health always means stop, eat,
    # and create distance if a threat is still close.
    if health < 12.0:
        print(f"DEFENSE: Critical health {health:.1f}; holding recovery mode")
        client.transport.dispatch("chat", {"message": "#stop"})
        client.transport.dispatch("cancel", {})
        heal_if_needed(client, threshold=12.0)
        if threats and threats[0].get("distance", 999) <= 10:
            run_away(client, threats[0])
        time.sleep(2)
        return True

    if not threats:
        return False
        
    closest = threats[0]
    dist = closest.get("distance", 999)
    if dist > 10:
        return False # Too far to worry yet
        
    print(f"DEFENSE: Threat detected! {closest.get('type')} at {dist:.1f}m")

    threat_type = str(closest.get("type", "")).lower()
    # Skeletons and witches can keep damaging an unarmored player while the
    # bot paths into melee range, and creepers must never be approached as a
    # routine fight.  Until shield blocking/ranged combat is implemented,
    # create distance from these threats even at full health.
    armor_count = len(get_equipped_armor(client))
    unprotected = armor_count < 3
    must_flee = unprotected or any(
        hostile in threat_type for hostile in ("skeleton", "witch", "creeper")
    )
    if must_flee or health < 16.0:
        if unprotected:
            reason = f"only {armor_count}/4 armor pieces equipped"
        elif must_flee:
            reason = "ranged/explosive threat"
        else:
            reason = f"health only {health:.1f}"
        print(f"DEFENSE: Fleeing {closest.get('type')} ({reason})")
        client.transport.dispatch("chat", {"message": "#stop"})
        client.transport.dispatch("cancel", {})
        run_away(client, closest)
        time.sleep(2)
        return True
    
    # Are we equipped?
    # Simple check: do we have a sword or axe in hotbar/inventory?
    # equip_best_weapon does the checking and equipping.
    has_weapon = equip_best_weapon(client)
    
    if has_weapon:
        print("DEFENSE: Engels mode engaged. Attacking.")
        client.transport.dispatch("chat", {"message": "#stop"}) # Stop mining
        safe_combat(client, closest.get("id"), retreat_health=12.0)
        return True
    else:
        print("DEFENSE: No weapon! FLEE!")
        client.transport.dispatch("chat", {"message": "#stop"})
        run_away(client, closest)
        time.sleep(5) # Run for a bit
        return True
