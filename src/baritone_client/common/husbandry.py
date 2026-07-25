"""Animal husbandry - breeding a known herd for renewable food and leather.

The offline test world's spawn biome is animal-sparse, so hunting near base
starves the food/leather supply. Once a herd is located, breeding it turns a
fixed, dwindling set of animals into a renewable source. Breeding uses the
bridge's entity-id interaction so a moving target cannot turn a feed into an
air click. A narrow crosshair fallback remains only for clients that have not
received the newer bridge JAR yet.
"""

import logging
import time
from typing import Dict, List, Optional

from .combat import entity_position, get_nearby_entities, hunt_mobs
from .inventory import count_item, select_item
from .navigation import goto

logger = logging.getLogger(__name__)

# Operator-identified distant herds, keyed by animal type. The local spawn
# biome has none of these animals within any practical search radius (a bug
# in the local search was ruled out live: get_entities/hunt_mobs work
# correctly elsewhere and even against animals summoned right next to a
# bot), so a known-good waypoint is the only way a base in this biome ever
# gets food or leather without this manual fallback.
KNOWN_HERD_WAYPOINTS: Dict[str, tuple] = {
    "cow": (-320, 72, -202),
}

# Breeding item per animal family (substring-matched against the entity type).
# Cows/sheep breed with wheat; pigs with carrots; chickens with seeds; rabbits
# with carrots. Kept small and explicit -- these are the passive food/leather
# animals worth farming.
BREEDING_FOOD: Dict[str, str] = {
    "mooshroom": "minecraft:wheat",
    "cow": "minecraft:wheat",
    "sheep": "minecraft:wheat",
    "pig": "minecraft:carrot",
    "chicken": "minecraft:wheat_seeds",
    "rabbit": "minecraft:carrot",
}


def breeding_food_for(animal_type: str) -> Optional[str]:
    at = animal_type.lower()
    for family, food in BREEDING_FOOD.items():
        if family in at:
            return food
    return None


def _animals_of_type(
    client, animal_type: str, radius: int, adults_only: bool
) -> List[dict]:
    """Passive animals of ``animal_type`` currently in range."""
    at = animal_type.lower()
    found = []
    for entity in get_nearby_entities(client, radius):
        etype = str(entity.get("type", "")).lower()
        if at not in etype:
            continue
        if adults_only and entity.get("is_baby", False):
            continue
        found.append(entity)
    return found


def count_herd(client, animal_type: str, radius: int = 16) -> int:
    """Total animals (adult + baby) of a type in range -- the herd-size signal
    used to confirm a breed actually produced offspring."""
    return len(_animals_of_type(client, animal_type, radius, adults_only=False))


def discover_herd(
    client,
    animal_type: str = "cow",
    *,
    radius: int = 64,
    timeout: float = 300.0,
    minimum_size: int = 2,
    max_distance: float = 384.0,
) -> Optional[tuple[int, int, int]]:
    """Explore until a renewable-size herd is observed, then verify it nearby."""
    state = client.transport.dispatch("get_state", {})
    position = state.get("block_position", state.get("position", {}))
    origin_x = float(position.get("x", state.get("x", 0)) or 0)
    origin_z = float(position.get("z", state.get("z", 0)) or 0)
    deadline = time.monotonic() + max(1.0, float(timeout))
    exploring = False

    def stop() -> None:
        nonlocal exploring
        if exploring:
            client.transport.dispatch("cancel", {})
            client.transport.dispatch("chat", {"message": "#stop"})
            exploring = False

    try:
        while time.monotonic() < deadline:
            live = client.transport.dispatch("get_state", {})
            live_pos = live.get("block_position", live.get("position", {}))
            current_x = float(live_pos.get("x", live.get("x", origin_x)) or origin_x)
            current_z = float(live_pos.get("z", live.get("z", origin_z)) or origin_z)
            distance = ((current_x - origin_x) ** 2 + (current_z - origin_z) ** 2) ** 0.5
            if distance > float(max_distance):
                return None

            animals = _animals_of_type(client, animal_type, radius, adults_only=True)
            positioned = [entity_position(animal) for animal in animals]
            positioned = [value for value in positioned if value is not None]
            if len(positioned) >= int(minimum_size):
                stop()
                x = round(sum(value[0] for value in positioned) / len(positioned))
                y = round(sum(value[1] for value in positioned) / len(positioned))
                z = round(sum(value[2] for value in positioned) / len(positioned))
                if goto(
                    client,
                    x,
                    y,
                    z,
                    timeout=180,
                    check_interval=0.5,
                    tolerance=8.0,
                ) and count_herd(client, animal_type, radius=32) >= int(minimum_size):
                    return (x, y, z)
                return None

            if not exploring:
                client.transport.dispatch(
                    "explore",
                    {"x": int(origin_x), "z": int(origin_z)},
                )
                exploring = True
            time.sleep(3.0)
    finally:
        stop()
    return None


def feed_animal(client, animal: dict, food_item: str) -> bool:
    """Right-click ``food_item`` onto a specific animal by entity id.

    Returns True only when a food item was verifiably consumed, so a missed
    or rejected interaction reports failure instead of a false success.
    """
    entity_id = animal.get("id")
    position = animal.get("position") or {}
    if entity_id is None:
        return False
    if count_item(client, food_item) <= 0:
        return False
    if not select_item(client, food_item, allow_swap=True):
        return False

    before = count_item(client, food_item)
    try:
        result = client.transport.dispatch(
            "entity_interact",
            {"action": "feed", "entity_id": int(entity_id), "max_distance": 6.0},
        )
        if not isinstance(result, dict) or not result.get("accepted", False):
            return False
    except Exception as exc:
        # Do not retry ambiguous timeouts: the first request might have fed the
        # animal. Fall back only when an old bridge explicitly rejects the new
        # command/action as unknown.
        message = str(exc).lower()
        unsupported = "unknown interaction action" in message or "unknown command" in message
        if not unsupported or not all(axis in position for axis in ("x", "y", "z")):
            return False
        try:
            client.transport.dispatch(
                "look_at",
                {
                    "x": float(position["x"]),
                    "y": float(position["y"]) + 0.5,
                    "z": float(position["z"]),
                },
            )
            time.sleep(0.2)
            client.transport.dispatch("use_item", {"duration_ms": 0})
        except Exception:
            return False

    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        time.sleep(0.2)
        if count_item(client, food_item) < before:
            return True
    return count_item(client, food_item) < before


def breed_pair(client, animal_type: str, radius: int = 8) -> bool:
    """Feed two nearby adults so they breed, verified by the herd growing.

    Requires the player to already be near the herd and to carry at least two
    breeding items. Returns True only when a new animal (the calf) is
    detected, never on feed-attempts alone -- an animal already in love mode
    or a missed second target must not be miscounted as success.
    """
    food = breeding_food_for(animal_type)
    if food is None:
        logger.warning("No breeding food known for %s", animal_type)
        return False
    if count_item(client, food) < 2:
        return False

    adults = _animals_of_type(client, animal_type, radius, adults_only=True)
    if len(adults) < 2:
        return False

    herd_before = count_herd(client, animal_type, radius=max(radius, 16))

    fed = 0
    for animal in adults:
        if fed >= 2:
            break
        if feed_animal(client, animal, food):
            fed += 1
    if fed < 2:
        return False

    # The calf spawns a moment after both parents enter love mode.
    deadline = time.monotonic() + 4.0
    while time.monotonic() < deadline:
        time.sleep(0.4)
        if count_herd(client, animal_type, radius=max(radius, 16)) > herd_before:
            return True
    return count_herd(client, animal_type, radius=max(radius, 16)) > herd_before


def breed_herd(
    client,
    location,
    animal_type: str = "cow",
    target_size: int = 6,
    max_pairs: int = 4,
) -> bool:
    """Travel to a known herd and breed it up toward ``target_size``.

    ``location`` is an (x, y, z) waypoint (e.g. the operator-provided cow
    coordinate). Best-effort: breeds as many pairs as wheat/animals allow,
    stopping at target_size, max_pairs, or when a breed fails (out of food,
    too few adults, animals wandered). Returns True if the herd grew at all.
    """
    if not isinstance(location, (list, tuple)) or len(location) != 3:
        return False
    lx, ly, lz = (int(v) for v in location)

    if not goto(client, lx, ly, lz, timeout=600, check_interval=0.5, tolerance=3.0):
        logger.warning("Could not reach herd at %s", (lx, ly, lz))
        return False

    start = count_herd(client, animal_type, radius=16)
    grown = start
    for _ in range(max_pairs):
        if grown >= target_size:
            break
        if not breed_pair(client, animal_type):
            break
        grown = count_herd(client, animal_type, radius=16)

    if grown > start:
        print(f"  Herd of {animal_type} grew {start} -> {grown} at {(lx, ly, lz)}.")
        return True
    print(f"  Herd of {animal_type} did not grow (had {start}) at {(lx, ly, lz)}.")
    return False


def visit_known_herd_for_loot(
    client,
    required_loot: Dict[str, int],
    animal_type: str = "cow",
    *,
    preserve_breeding_pair: bool = False,
    location=None,
) -> bool:
    """Travel to a known distant herd and hunt it for food/leather.

    Last-resort fallback for a base in an animal-sparse biome: the local
    bounded search (hunt_mobs/acquire_emergency_food) can retry forever in
    place and never succeed there, so this explicitly leaves for a
    known-good location instead. Breeds the herd first when wheat is
    carried, so a renewable herd -- not a one-shot kill -- is what actually
    gets hunted. ``required_loot`` here means absolute carried amounts (not a
    gain-since-call baseline like hunt_mobs uses internally): this function
    can be called with the target already satisfied, and a baseline captured
    at call time would always read zero gain, wrongly reporting "still
    missing" for loot already banked.
    """
    location = location or KNOWN_HERD_WAYPOINTS.get(animal_type)
    if location is None:
        logger.warning("No known herd waypoint for %s", animal_type)
        return False
    lx, ly, lz = location

    def deficits() -> Dict[str, int]:
        return {
            item: target - count_item(client, item)
            for item, target in required_loot.items()
            if count_item(client, item) < target
        }

    if not deficits() and not preserve_breeding_pair:
        return True

    if not goto(client, lx, ly, lz, timeout=900, check_interval=0.5, tolerance=6.0):
        logger.warning("Could not reach known herd at %s", location)
        return False

    # Best-effort: breeding failure (out of wheat, too few adults) should not
    # block the hunt that's the actual point of this trip.
    breed_pair(client, animal_type)

    missing = deficits()
    max_kills = None
    if preserve_breeding_pair:
        herd_size = count_herd(client, animal_type, radius=32)
        max_kills = max(0, herd_size - 2)
        if missing and max_kills == 0:
            logger.warning(
                "Known herd at %s has no safely huntable animals beyond its pair",
                location,
            )
            return False
    if missing:
        result = hunt_mobs(
            client,
            mob_types=[animal_type],
            required_loot=missing,
            search_radius=32,
            timeout=300,
            heal_threshold=10.0,
            max_distance_from_origin=48.0,
            max_kills=max_kills,
        )
        if not result or not result.success:
            return False

    if not preserve_breeding_pair:
        return True

    # Hunting can pull the player away from the waypoint. Return before
    # counting so a remote or unloaded survivor is not mistaken for a
    # renewable source. Two nearby adults are the minimum future breeding
    # population; anything less is a one-shot food cache.
    if not goto(client, lx, ly, lz, timeout=300, check_interval=0.5, tolerance=8.0):
        return False
    remaining = count_herd(client, animal_type, radius=32)
    if remaining < 2:
        logger.warning(
            "Known herd at %s is not renewable: only %s %s remain",
            location,
            remaining,
            animal_type,
        )
        return False
    return True
