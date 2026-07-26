"""
Combat utilities - Mob engagement, retreat logic, and healing.
"""

import logging
import time
from typing import Callable, Dict, List, Optional
from .food_recovery import (
    bounded_exploration_origin,
    collect_edible_drop,
    must_hold_for_critical_food,
)
from .combat_targeting import matches_requested_mob

from ..core.exceptions import TransportError

from .defense import (
    AttackStyle,
    DefenseMode,
    DefenseRuntime,
    ThreatAssessment,
    assess_threats,
    choose_defense_action,
    plan_escape_candidates,
)

from .inventory import (
    count_item,
    equip_best_weapon,
    get_equipped_armor,
    select_item,
)
from .tasks import PlayerDeathDetected, TaskResult
from .navigation import goto

logger = logging.getLogger(__name__)

MELEE_ATTACK_COOLDOWN_THRESHOLD = 0.9
MULTI_THREAT_ABORT_RADIUS = 12.0


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
    no_retreat: bool = False,
) -> bool:
    """
    Fight target with retreat logic.

    Args:
        client: Baritone client
        target_id: Entity ID to attack
        retreat_health: Retreat if health drops below this
        max_duration: Maximum combat duration
        no_retreat: Skip the health-based retreat check entirely. Only for
            last-resort callers where fleeing has already been tried and
            failed repeatedly -- a fixed retreat_health floor doesn't work
            there because each failed flee attempt costs HP unpredictably,
            so health is often already below any floor by the time this is
            reached (live proof: Bot09 hit "Retreating! Health: 1.999" with
            retreat_health=2.0 and never landed a single hit before dying).
            Other safety checks (drowning, other-hostiles abort) still apply.

    Returns:
        True if target killed, False if retreated or failed
    """
    # Equip weapon
    equip_best_weapon(client)
    
    start = time.time()
    
    approach_failures = 0
    
    while time.time() - start < max_duration:
        snapshot = _get_combat_snapshot(
            client,
            radius=max(30, tracking_radius),
        )
        if snapshot is not None:
            state = snapshot["player"]
            entities = snapshot["entities"]
        else:
            state = client.transport.dispatch("get_state", {})
            entities = get_nearby_entities(
                client,
                radius=max(30, tracking_radius),
            )

        # Surface before drowning: a fish fight keeps the bot submerged and
        # attacking in place with no goto to trigger the normal reflex. Allow a
        # brief dive, then force a surface well under the ~15s air supply.
        if _submerged_too_long(client, state, max_seconds=8.0):
            print("SURVIVAL: submerged too long mid-combat; surfacing to avoid drowning")
            client.transport.dispatch("cancel", {})
            _surface_after_aquatic_hunt(client, timeout=8.0)
            client._submerged_since = None
            return False

        # Check health
        health = state.get("health", 20)
        if health < retreat_health and not no_retreat:
            print(f"Retreating! Health: {health}")
            client.transport.dispatch("cancel", {})
            return False
        if health <= 0:
            return False
        
        # Check if target still exists
        target = next((e for e in entities if e.get("id") == target_id), None)

        if abort_on_other_hostiles:
            other_threat = next(
                (
                    threat
                    for threat in assess_threats(entities, state)
                    if threat.entity.get("id") != target_id
                    and threat.distance <= MULTI_THREAT_ABORT_RADIUS
                ),
                None,
            )
            if other_threat is not None:
                print(
                    f"Combat aborted: {other_threat.entity.get('type')} entered "
                    f"{other_threat.distance:.1f}m safety radius"
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
            cooldown = _attack_cooldown(state)
            if cooldown < MELEE_ATTACK_COOLDOWN_THRESHOLD:
                # Continue observing the fight while the weapon recharges.
                # The bridge repeats this check on the game thread so a
                # delayed request cannot turn into a weak spam attack.
                time.sleep(0.05)
                continue
            try:
                result = client.transport.dispatch(
                    "attack_entity",
                    {
                        "entity_id": target_id,
                        "min_cooldown": MELEE_ATTACK_COOLDOWN_THRESHOLD,
                    },
                )
            except Exception as exc:
                # The entity can die or unload between the entity scan and
                # attack dispatch.  That is a successful end to this combat,
                # not a phase-level exception that should crash recovery.
                if "entity not found" in str(exc).lower():
                    return True
                print(f"Combat attack failed: {exc}")
                return False
            if isinstance(result, dict) and result.get("attacked") is False:
                if result.get("reason") == "cooldown":
                    time.sleep(0.05)
                    continue
                print(f"Combat attack declined: {result.get('reason', 'unknown')}")
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


def _approach_aquatic_food(
    client,
    target_id: int,
    entity_type: str,
    *,
    timeout: float = 15.0,
) -> bool:
    """Use Baritone's dynamic entity goal to reach a swimming food mob.

    A coordinate goal at a fish's water block is not standable, so normal
    ``goto`` retries can never enter melee range. Entity-follow maintains a
    nearby reachable goal as the fish moves, and is always cancelled before
    combat takes ownership of movement.
    """
    command_type = str(entity_type).split(":")[-1]
    try:
        client.transport.dispatch(
            "chat",
            {"message": f"#follow entity {command_type}"},
        )
        deadline = time.time() + max(0.0, float(timeout))
        while time.time() < deadline:
            # #follow drives the bot into the water after the fish with no
            # goto reflex to catch drowning -- this is exactly where Bot08
            # drowned. Bail out to surface after a bounded dive.
            follow_state = client.transport.dispatch("get_state", {})
            if _submerged_too_long(client, follow_state, max_seconds=8.0):
                print("SURVIVAL: submerged too long chasing aquatic food; surfacing")
                return False
            entities = get_nearby_entities(client, radius=64)
            target = next(
                (entity for entity in entities if entity.get("id") == target_id),
                None,
            )
            if target is None:
                return True
            if float(target.get("distance", 999)) < 4.5:
                return True
            threats = scan_for_threats(client, radius=12)
            if threats and float(threats[0].get("distance", 999)) <= 12:
                return False
            time.sleep(0.5)
        return False
    finally:
        client.transport.dispatch("chat", {"message": "#stop"})
        client.transport.dispatch("cancel", {})


def _surface_after_aquatic_hunt(client, *, timeout: float = 10.0) -> bool:
    """Reach breathing air after a fish kill without targeting its water block."""
    try:
        client.transport.dispatch("chat", {"message": "#surface"})
        deadline = time.time() + max(0.0, float(timeout))
        while time.time() < deadline:
            state = client.transport.dispatch("get_state", {})
            ensure_alive(client, state)
            position = state.get("block_position", state.get("position", {}))
            if all(axis in position for axis in ("x", "y", "z")):
                head = client.transport.dispatch(
                    "get_block",
                    {
                        "x": int(position["x"]),
                        "y": int(position["y"]) + 1,
                        "z": int(position["z"]),
                    },
                ).get("id", "")
                if "water" not in str(head):
                    return True
            time.sleep(0.5)
        return False
    finally:
        client.transport.dispatch("chat", {"message": "#stop"})
        client.transport.dispatch("cancel", {})


# Consecutive supervision ticks with the head underwater before forcing a
# surface.  At ~3s between ticks this reacts within ~6s -- well inside the
# ~15s before drown damage begins -- while ignoring a momentary dip through a
# waterfall or a one-block plunge.
_SUBMERSION_TICKS_BEFORE_SURFACE = 2


def _head_block_is_water(client, state) -> bool:
    """True when the block at head height is water (player fully submerged).

    A bot swimming across the surface has its head in air (only the feet block
    is water), so this fires only on genuine full submersion.
    """
    position = state.get("block_position", state.get("position", {}))
    if not all(axis in position for axis in ("x", "y", "z")):
        return False
    try:
        head = client.transport.dispatch(
            "get_block",
            {
                "x": int(position["x"]),
                "y": int(position["y"]) + 1,
                "z": int(position["z"]),
            },
        ).get("id", "")
    except Exception:
        return False
    return "water" in str(head)


def _submerged_too_long(client, state, *, max_seconds: float) -> bool:
    """Track continuous head-underwater TIME; True once it exceeds max_seconds.

    The tick-count reflex (escape_water_if_submerged) surfaces after 2
    supervision ticks, which is right for the slow (~2s) goto/defence loops
    but wrong for the fast (~0.2-0.5s) aquatic-hunt/combat loops -- there 2
    ticks is under a second, so it would surface before ever reaching a fish.
    Conversely those loops previously had NO drowning check at all, so a bot
    chasing a fish stayed submerged the full ~15s air supply and DROWNED
    (confirmed live: Bot08 died "safely hunting tropical_fish"). A wall-clock
    limit lets a hunt dive briefly to grab a near-surface fish, then forces a
    surface well before the air runs out, regardless of loop frequency.
    """
    if not _head_block_is_water(client, state):
        client._submerged_since = None
        return False
    since = getattr(client, "_submerged_since", None)
    now = time.time()
    if since is None:
        client._submerged_since = now
        return False
    return (now - since) >= float(max_seconds)


def escape_water_if_submerged(client, state) -> bool:
    """Surface the bot before it drowns -- the single largest cause of fleet deaths.

    The bridge does not expose the air-supply meter, so submersion is inferred
    from the block at head height.  Baritone crosses water on the surface fine,
    but bots still drown when fully submerged: knocked into deep water by a mob,
    pathing along a lake bottom, or breaking blocks while under.  Once the head
    has stayed underwater across consecutive supervision ticks, drive Baritone's
    ``#surface`` and confirm the head reaches air.  Returns True when a surfacing
    action was taken so the caller pauses/retries like any other defensive
    reflex.
    """
    if not _head_block_is_water(client, state):
        client._submersion_ticks = 0
        return False
    ticks = int(getattr(client, "_submersion_ticks", 0)) + 1
    client._submersion_ticks = ticks
    if ticks < _SUBMERSION_TICKS_BEFORE_SURFACE:
        return False
    print(
        f"SURVIVAL: head underwater for {ticks} supervision ticks; "
        "surfacing to avoid drowning"
    )
    if _surface_after_aquatic_hunt(client, timeout=12.0):
        client._submersion_ticks = 0
    return True


def survival_tick(client, state=None) -> bool:
    """Central per-poll survival reflex for any long-running wait loop.

    Hooking the drowning check only into ``defend_or_flee`` covered gather/base
    loops but missed the activity where bots actually drown -- pathing across
    water -- because ``goto``'s wait loop never called it.  This is the shared
    entry point those blocking primitives call each poll so the reflex fires
    during ALL activity.  Currently it surfaces a submerged player; returns
    True if it intervened (the caller should assume the current Baritone
    process was cancelled and re-issue it).
    """
    try:
        if state is None:
            state = client.transport.dispatch("get_state", {})
        return escape_water_if_submerged(client, state)
    except PlayerDeathDetected:
        raise
    except Exception:
        return False


def _attack_cooldown(state: Dict) -> float:
    """Return normalized melee readiness, defaulting ready for old bridges."""
    try:
        return min(1.0, max(0.0, float(state.get("attack_cooldown", 1.0))))
    except (TypeError, ValueError):
        return 0.0


def _threat_can_reach_player(
    threat: Dict,
    player_state: Dict,
    *,
    max_vertical_separation: float = 6.0,
) -> bool:
    """Reject sealed cave mobs that are close only in three-dimensional range."""
    threat_position = entity_position(threat)
    player_position = player_state.get(
        "block_position", player_state.get("position", {})
    )
    if threat_position is None or "y" not in player_position:
        return True
    try:
        return abs(float(threat_position[1]) - float(player_position["y"])) <= float(
            max_vertical_separation
        )
    except (TypeError, ValueError):
        return True


EMERGENCY_FOOD_ITEMS = [
    "minecraft:cooked_beef", "minecraft:cooked_porkchop",
    "minecraft:cooked_chicken", "minecraft:cooked_mutton",
    "minecraft:bread", "minecraft:apple", "minecraft:cooked_salmon",
    "minecraft:cooked_cod", "minecraft:baked_potato",
    "minecraft:golden_apple", "minecraft:beef",
    "minecraft:porkchop", "minecraft:chicken",
    "minecraft:mutton", "minecraft:rabbit",
    "minecraft:salmon", "minecraft:cod", "minecraft:tropical_fish",
    "minecraft:rotten_flesh",
]

# Mob-type substrings whose kill drops raw food, i.e. hunting them is itself
# a way back to a stable hunger level.
_FOOD_YIELDING_MOBS = ("cow", "mooshroom", "sheep", "pig", "chicken", "rabbit")

# Below this health, acquire_emergency_food restricts its passive-food target
# search to a short radius rather than committing to a long unprotected trek.
# See the hunt_radius comment at its call site for the live failure this fixes.
_CRITICAL_HUNT_HEALTH = 10.0


def _emergency_food_count(client) -> int:
    return sum(count_item(client, item_id) for item_id in EMERGENCY_FOOD_ITEMS)


def eat_until_hunger(client, minimum_food: int = 14) -> bool:
    """Consume carried food until the hunger bar is safe for progression."""
    minimum_food = max(1, min(int(minimum_food), 20))
    attempts = 0
    while attempts < 10:
        state = client.transport.dispatch("get_state", {})
        ensure_alive(client, state)
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
    exploration_center: Optional[tuple[float, float]] = None,
    renewable_source_callback: Optional[
        Callable[[str, tuple[int, int, int]], None]
    ] = None,
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
    origin_x, origin_z = bounded_exploration_origin(
        position,
        exploration_center,
        max_exploration_distance,
    )
    exploring = False
    sprint_suppressed = False

    def stop_exploring() -> None:
        nonlocal exploring, sprint_suppressed
        if exploring:
            client.transport.dispatch("cancel", {})
            client.transport.dispatch("chat", {"message": "#stop"})
            exploring = False
        if sprint_suppressed:
            client.transport.dispatch(
                "chat", {"message": "#set allowSprint true"}
            )
            sprint_suppressed = False

    # Emergency exploration values remaining hunger more than travel speed.
    # Live Bot10 burned food 9 -> 6 in roughly 35 seconds while sprinting and
    # died before a source loaded. Restore the normal setting on every bounded
    # exit through ``stop_exploring``.
    client.transport.dispatch("chat", {"message": "#set allowSprint false"})
    sprint_suppressed = True

    # Prefer the more nourishing land animals during emergency recovery, and
    # search the full set of loaded chunks after waiting safely for dawn.
    primary_land_food = ["cow", "pig"]
    secondary_land_food = ["sheep", "chicken", "rabbit"]
    water_food = ["salmon", "cod", "tropical_fish"]
    # A bot already near death cannot regenerate without eating, and cannot
    # eat without exploring: holding indefinitely guarantees it never
    # recovers. Give the hold a short, bounded grace period (a genuinely new
    # threat can still abort immediately via the check above) and then fall
    # through to the same bounded, threat-checked exploration a merely-hurt
    # bot already uses -- it strictly dominates permanent inaction once no
    # threat is present.
    hold_cycles = 0
    max_hold_cycles = 3
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
        ensure_alive(client, state)
        if recovery_complete(state):
            stop_exploring()
            return True
        # A prior combat/pickup cycle may have added raw food while health
        # remained above the healing threshold. Re-attempt eating every loop;
        # otherwise the bot can carry several meals at hunger 10 and continue
        # hunting until timeout without ever consuming them.
        if eat_until_hunger(client, minimum_food=minimum_food):
            refreshed = client.transport.dispatch("get_state", {})
            if recovery_complete(refreshed):
                stop_exploring()
                return True
        from .navigation import goto as recovery_goto

        if collect_edible_drop(
            client,
            get_nearby_entities(client, radius=48),
            recovery_goto,
        ):
            continue

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

        threats = scan_for_threats(client, radius=16, player_state=state)
        immediate_threat = next(
            (
                threat
                for threat in threats
                if threat.get("distance", 999) <= 12
                and _threat_can_reach_player(threat, state)
            ),
            None,
        )
        if immediate_threat is not None:
            stop_exploring()
            client.transport.dispatch("cancel", {})
            from .base import _has_existing_enclosure
            if _has_existing_enclosure(client, state):
                print(
                    f"RECOVERY: {immediate_threat.get('type')} is "
                    f"{immediate_threat.get('distance', 999):.1f}m away; "
                    "holding inside verified shelter"
                )
                time.sleep(5)
                continue
            print("RECOVERY: hostile nearby and no enclosure; aborting food run")
            return False

        current_food = int(state.get("food_level", state.get("food", 20)))
        current_health = float(state.get("health", 20) or 0)
        # must_hold_for_critical_food only holds below health 6.0 (deliberately
        # low -- see its docstring for the soft-lock bug that floor fixes), so
        # a bot at e.g. 7.3hp is treated as "safe enough" to proceed here and
        # will otherwise commit to a full 64-block, sprint-suppressed hike
        # toward the nearest singleton target with no further health check
        # until it arrives. Confirmed live: Bot08 repeatedly walked 48-64m
        # toward a chicken/salmon while sitting at ~7hp and died to combat or
        # drowning partway there, since a single stray hit or a few seconds
        # submerged is fatal at that health and the outer threat/hostile
        # checks only run once per loop iteration (~every 15s of travel).
        # Below _CRITICAL_HUNT_HEALTH, shrink the search radius to match the
        # same perimeter already trusted for the immediate-threat scan above
        # (16 blocks) so a wounded bot only ever commits to food it can reach
        # quickly; anything farther falls through to the existing bounded
        # hold-then-explore path instead of a long unprotected trek.
        hunt_radius = 16.0 if current_health < _CRITICAL_HUNT_HEALTH else 64.0
        nearby = get_nearby_entities(client, radius=hunt_radius)

        def food_group(animal_type: str) -> List[dict]:
            return [
                entity
                for entity in nearby
                if animal_type in str(entity.get("type", "")).lower()
                and not entity.get("is_baby", False)
            ]

        target = None
        for animal_type in primary_land_food + secondary_land_food:
            group = food_group(animal_type)
            if len(group) >= 3:
                positions = [entity_position(entity) for entity in group]
                positions = [position for position in positions if position is not None]
                if positions and renewable_source_callback is not None:
                    centroid = (
                        round(sum(position[0] for position in positions) / len(positions)),
                        round(sum(position[1] for position in positions) / len(positions)),
                        round(sum(position[2] for position in positions) / len(positions)),
                    )
                    renewable_source_callback(animal_type, centroid)
                # Kill at most the animals beyond a breeding pair. The next
                # loop sees only two and leaves them intact.
                target = min(group, key=lambda entity: float(entity.get("distance", 999)))
                break
            if len(group) == 1 and current_food <= 8:
                # A singleton is not a renewable herd. Use it before hunger
                # becomes critical rather than spending the remaining safety
                # margin wandering past a non-renewable food source.
                target = group[0]
                break
            if len(group) == 2 and current_food <= 2:
                # Preserve a viable breeding pair unless starvation is
                # immediately life-threatening.
                target = min(group, key=lambda entity: float(entity.get("distance", 999)))
                break
        # Water-mob drops are substantially harder to collect reliably: the
        # target can vanish below the player while its item floats elsewhere.
        # When hunger is still stable, spend the first part of the bounded
        # search loading land animals instead. Keep fish as a last-resort path
        # for genuinely critical hunger or after land exploration had time to
        # work.
        land_search_elapsed = time.time() - start
        water_fallback_after = min(90.0, max(15.0, timeout / 2.0))
        if target is None and (
            current_food <= 6
            or land_search_elapsed >= water_fallback_after
        ):
            # A distant fish is not emergency food: the follow path spends the
            # remaining hunger margin and repeatedly pulled live bots into
            # water without ever reaching melee/loot range. Restrict aquatic
            # fallback to a genuinely nearby target; otherwise keep the
            # bounded land search active.
            target = find_entity_by_type(client, water_food, radius=16)
        if target is None:
            if (
                must_hold_for_critical_food(
                    state,
                    minimum_health=minimum_health,
                    current_food=current_food,
                )
                and hold_cycles < max_hold_cycles
            ):
                hold_cycles += 1
                stop_exploring()
                print(
                    "RECOVERY: critical health/hunger with no loaded food "
                    f"target; holding {hold_cycles}/{max_hold_cycles} cycles "
                    "before searching anyway"
                )
                time.sleep(3)
                continue
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
        target_type = str(target.get("type", ""))
        if any(water_type in target_type for water_type in water_food) and float(
            target.get("distance", 999)
        ) >= 4.5:
            if not _approach_aquatic_food(
                client,
                target_id,
                target_type,
                timeout=8.0,
            ):
                print("RECOVERY: aquatic target could not be reached safely")
                time.sleep(1)
                continue
        aquatic_target = any(
            water_type in target_type for water_type in water_food
        )
        if not safe_combat(
            client,
            target_id,
            retreat_health=1.0,
            max_duration=35,
            abort_on_other_hostiles=True,
        ):
            return False

        if aquatic_target:
            if not _surface_after_aquatic_hunt(client):
                print("RECOVERY: could not prove breathing air after aquatic hunt")
                return False
        else:
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
    max_kills: Optional[int] = None,
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
        max_kills: Hard safety cap, even when requested loot is still missing
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
        if max_kills is not None and kills >= max(0, int(max_kills)):
            break
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
                    and not matches_requested_mob(str(entity.get("type", "")), mob_types)
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


def scan_for_threats(
    client,
    radius: int = 16,
    *,
    raise_on_error: bool = False,
    player_state: Optional[Dict] = None,
) -> List[Dict]:
    """Return active hostile entities ordered by assessed danger.

    The return type remains a list of bridge entity dictionaries for existing
    callers.  Classification and ordering come from the canonical policy in
    :mod:`baritone_client.common.defense`.
    """
    nearby = get_nearby_entities(
        client,
        radius,
        raise_on_error=raise_on_error,
    )
    return [item.entity for item in assess_threats(nearby, player_state)]


def _get_combat_snapshot(client, radius: int = 16) -> Optional[Dict]:
    """Use the atomic bridge observation when available.

    Bridge 1.0.23 and older do not expose this route.  Treat an absent or
    malformed response as a capability miss and retain the established
    get_state/get_entities path.
    """
    try:
        snapshot = client.transport.dispatch(
            "get_combat_snapshot",
            {"radius": radius},
        )
    except Exception as exc:
        logger.debug("Atomic combat snapshot unavailable: %s", exc)
        return None
    if not isinstance(snapshot, dict):
        return None
    if not isinstance(snapshot.get("player"), dict):
        return None
    if not isinstance(snapshot.get("entities"), list):
        return None
    return snapshot


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


_ESCAPE_HAZARDS = (
    "lava",
    "fire",
    "cactus",
    "magma_block",
    "campfire",
    "pointed_dripstone",
    "sweet_berry_bush",
)
_ESCAPE_NON_GROUND = ("air", "water", "lava", "cave_air", "void_air")
_ESCAPE_PASSABLE = (
    "air",
    "grass",
    "fern",
    "flower",
    "snow",
    "vine",
)


def _defense_runtime(client) -> DefenseRuntime:
    """Return state attached to the client without module-global bot mixing."""
    runtime = getattr(client, "_mcbaratone_defense_runtime", None)
    if runtime is None:
        runtime = getattr(client.transport, "_mcbaratone_defense_runtime", None)
    if isinstance(runtime, DefenseRuntime):
        return runtime
    runtime = DefenseRuntime()
    try:
        setattr(client, "_mcbaratone_defense_runtime", runtime)
    except Exception:
        # A few client doubles use slots; their transport remains session-local.
        setattr(client.transport, "_mcbaratone_defense_runtime", runtime)
    return runtime


def _stop_for_defense(client) -> None:
    """Cancel both Baritone command and transport-level work."""
    client.transport.dispatch("chat", {"message": "#stop"})
    client.transport.dispatch("cancel", {})


def _block_id(client, x: int, y: int, z: int) -> Optional[str]:
    try:
        result = client.transport.dispatch("get_block", {"x": x, "y": y, "z": z})
    except Exception as exc:
        logger.debug("Escape terrain probe failed at %s,%s,%s: %s", x, y, z, exc)
        return None
    if not isinstance(result, dict):
        return None
    block_id = result.get("id", result.get("block"))
    return str(block_id).lower() if block_id else None


def _escape_destination_safe(client, x: int, y: int, z: int) -> bool:
    """Reject an obvious hazard or unsupported endpoint using existing reads."""
    feet = _block_id(client, x, y, z)
    head = _block_id(client, x, y + 1, z)
    below = _block_id(client, x, y - 1, z)
    known = tuple(value for value in (feet, head, below) if value)
    if any(token in block for block in known for token in _ESCAPE_HAZARDS):
        return False
    if feet and not any(token in feet for token in _ESCAPE_PASSABLE):
        return False
    if head and not any(token in head for token in _ESCAPE_PASSABLE):
        return False
    if below and any(token in below for token in _ESCAPE_NON_GROUND):
        return False
    # Unknown probes are neutral: refusing every route during partial bridge
    # degradation is worse than using the best directional fallback.
    return True


def _separation_from(entity: Dict, player_position: Dict) -> float:
    position = entity_position(entity)
    if position is None:
        return float(entity.get("distance", 0) or 0)
    try:
        return (
            (float(position[0]) - float(player_position.get("x", 0))) ** 2
            + (float(position[2]) - float(player_position.get("z", 0))) ** 2
        ) ** 0.5
    except (TypeError, ValueError):
        return float(entity.get("distance", 0) or 0)


def _verify_escape(
    client,
    threat_id: Optional[int],
    initial_distance: float,
    *,
    timeout: float,
    minimum_gain: float,
) -> bool:
    """Confirm that the selected route actually increases separation."""
    deadline = time.monotonic() + max(0.5, timeout)
    while time.monotonic() < deadline:
        ensure_alive(client)
        try:
            entities = get_nearby_entities(client, 40, raise_on_error=True)
        except EntityQueryError:
            time.sleep(0.5)
            continue
        target = next((entity for entity in entities if entity.get("id") == threat_id), None)
        if target is None:
            return True
        state = client.transport.dispatch("get_state", {})
        position = state.get("block_position", state.get("position", {})) or {}
        separation = _separation_from(target, position)
        if separation >= max(14.0, initial_distance + minimum_gain):
            return True
        time.sleep(0.5)
    return False


def run_away(
    client,
    threat: Dict,
    *,
    timeout: float = 6.0,
    minimum_gain: float = 5.0,
) -> bool:
    """Choose a terrain-screened route and verify increasing separation."""
    try:
        state = client.transport.dispatch("get_state", {})
        position = state.get("block_position", state.get("position", {})) or {}
        try:
            nearby = scan_for_threats(
                client,
                radius=16,
                raise_on_error=True,
                player_state=state,
            )
        except EntityQueryError:
            nearby = [threat]
        if not any(item.get("id") == threat.get("id") for item in nearby):
            nearby.append(threat)
        assessments = assess_threats(nearby, state)
        if not assessments:
            assessments = [
                ThreatAssessment(
                    entity=threat,
                    entity_type=str(threat.get("type", "unknown")),
                    distance=float(threat.get("distance", 0) or 0),
                    closing_speed=0.0,
                    score=1.0,
                    style=AttackStyle.MELEE,
                    always_evade=True,
                )
            ]
        initial_distance = _separation_from(threat, position)
        candidates = plan_escape_candidates(position, assessments)
        safe_candidates = [
            candidate
            for candidate in candidates[:3]
            if _escape_destination_safe(client, candidate.x, candidate.y, candidate.z)
        ]
        if not safe_candidates:
            print("FLEE: no terrain-safe escape endpoint found")
            return False

        per_candidate = max(1.5, timeout / len(safe_candidates))
        for candidate in safe_candidates:
            print(
                f"FLEE: pathing to {candidate.x},{candidate.y},{candidate.z}; "
                f"initial separation {initial_distance:.1f}m"
            )
            client.transport.dispatch(
                "goal",
                {"x": candidate.x, "y": candidate.y, "z": candidate.z},
            )
            client.transport.dispatch("chat", {"message": "#path"})
            if _verify_escape(
                client,
                threat.get("id"),
                initial_distance,
                timeout=per_candidate,
                minimum_gain=minimum_gain,
            ):
                print("FLEE: separation verified")
                return True
            client.transport.dispatch("cancel", {})
        print("FLEE: candidate routes did not increase separation")
        return False
    except PlayerDeathDetected:
        raise
    except Exception as exc:
        print(f"Run away failed: {exc}")
        return False


def ensure_alive(client, state: Optional[Dict] = None) -> bool:
    """Raise when dead so only top-level death recovery may respawn."""
    try:
        if state is None:
            state = client.transport.dispatch("get_state", {})
        health = state.get("health", 20)
        if state.get("is_dead", False) or (
            health is not None and float(health) <= 0
        ):
            raise PlayerDeathDetected(
                "Player died during combat or health recovery"
            )
    except PlayerDeathDetected:
        raise
    except Exception as e:
        print(f"DEATH: ensure_alive check failed: {e}")
    return False


def defend_or_flee(client) -> bool:
    """Advance the canonical defensive state machine by one supervised tick."""
    snapshot = _get_combat_snapshot(client)
    state = (
        snapshot["player"]
        if snapshot is not None
        else client.transport.dispatch("get_state", {})
    )
    ensure_alive(client, state)
    # Drowning is the fleet's #1 killer and more urgent than any land threat:
    # get the head above water before assessing mobs.
    if escape_water_if_submerged(client, state):
        return True
    health = float(state.get("health", 20) or 0)
    runtime = _defense_runtime(client)
    try:
        if snapshot is not None:
            threats = [
                item.entity
                for item in assess_threats(snapshot["entities"], state)
            ]
        else:
            threats = scan_for_threats(
                client,
                raise_on_error=True,
                player_state=state,
            )
    except TypeError:
        # Preserve compatibility with existing callers/tests that replace the
        # old one-argument scanner.
        threats = scan_for_threats(client)
    except EntityQueryError as exc:
        runtime.transition(DefenseMode.ALERT, f"entity query unavailable: {exc}")
        _stop_for_defense(client)
        return True

    assessments = assess_threats(threats, state)
    armor_count = (
        int(state.get("armor_count", 0) or 0)
        if assessments and "armor_count" in state
        else len(get_equipped_armor(client)) if assessments else 0
    )
    has_weapon = False
    if assessments:
        primary = assessments[0]
        urgent_count = sum(item.distance <= 10.0 for item in assessments)
        if (
            health >= 16.0
            and armor_count >= 3
            and not primary.always_evade
            and primary.distance <= 10.0
            and urgent_count <= 1
        ):
            has_weapon = equip_best_weapon(client)

    decision = choose_defense_action(
        assessments,
        health=health,
        armor_count=armor_count,
        has_weapon=has_weapon,
        runtime=runtime,
    )
    runtime.transition(decision.mode, decision.reason)

    if decision.mode == DefenseMode.CLEAR:
        return False
    if decision.mode == DefenseMode.ALERT:
        return False
    if decision.mode == DefenseMode.RECOVER:
        print(f"DEFENSE: Recovery mode ({decision.reason})")
        _stop_for_defense(client)
        heal_if_needed(client, threshold=12.0)
        return True
    if decision.primary is None:
        return False

    primary = decision.primary
    print(
        f"DEFENSE: {decision.mode.value} {primary.entity.get('type')} "
        f"at {primary.distance:.1f}m ({decision.reason}; score={primary.score:.1f})"
    )
    _stop_for_defense(client)
    if decision.mode == DefenseMode.EVADE:
        threat_id = primary.entity.get("id")
        escaped = run_away(client, primary.entity)
        runtime.record_evade_result(threat_id, escaped)
        if escaped or not runtime.should_escalate_to_combat(threat_id):
            runtime.hold_recovery(8.0 if escaped else 12.0)
            return True
        # Repeated evasion against this exact threat has failed every time
        # (see DefenseRuntime.record_evade_result) -- continuing to hold that
        # 0% strategy is worse than fighting, even unarmored. This is the
        # one case that bypasses the armor_count<3 EVADE gate: it is reached
        # only after evasion has already been tried and demonstrably failed,
        # not instead of it.
        print(
            f"DEFENSE: evasion failed {runtime.evade_failures}x against "
            f"{primary.entity.get('type')}; fighting back as a last resort"
        )
        # A fixed retreat_health floor does not work here: each failed flee
        # attempt costs unpredictable HP, so health is often already below
        # any floor by the time escalation fires. Live proof: this fired at
        # 4.8hp with retreat_health=6.0, then again at 1.999hp with
        # retreat_health=2.0 -- both times safe_combat retreated on its very
        # first health check and never landed a single hit before the bot
        # died anyway. Skip the retreat check entirely: evasion is already a
        # proven 0% strategy against this threat, so committing to the fight
        # is strictly better regardless of current health.
        defeated = safe_combat(
            client,
            threat_id,
            no_retreat=True,
            abort_on_other_hostiles=True,
        )
        if defeated:
            runtime.record_evade_result(threat_id, True)
        runtime.hold_recovery(6.0 if defeated else 10.0)
        return True

    defeated = safe_combat(
        client,
        primary.entity.get("id"),
        retreat_health=12.0,
        abort_on_other_hostiles=True,
    )
    if not defeated:
        escape_target = primary.entity
        latest = _get_combat_snapshot(client)
        if latest is not None:
            updated = assess_threats(latest["entities"], latest["player"])
            if updated:
                escape_target = updated[0].entity
        run_away(client, escape_target)
    runtime.hold_recovery(6.0 if defeated else 10.0)
    return True
