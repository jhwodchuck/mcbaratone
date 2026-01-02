
import threading
import time
import logging
from abc import ABC, abstractmethod
from typing import Optional

from .coordination_hub import CoordinationHub, SystemEvent, EventType

logger = logging.getLogger(__name__)

class BackgroundSystem(ABC):
    """
    Abstract base class for parallel background systems.
    Runs in a separate thread to monitor state or perform tasks concurrently
    with the main phase execution loop.
    """
    
    
    def __init__(self, client, coordination_hub: CoordinationHub, name: str, interval: float = 1.0, resources=None):
        self.client = client
        self.coordination = coordination_hub
        self.name = name
        self.interval = interval
        self.resources = resources
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def start(self):
        """Start the background system loop."""
        if self._running:
            return
        
        self._running = True
        self._thread = threading.Thread(target=self._loop, name=f"System-{self.name}", daemon=True)
        self._thread.start()
        logger.info(f"Started system: {self.name}")

    def stop(self):
        """Stop the background system loop."""
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        logger.info(f"Stopped system: {self.name}")

    def _loop(self):
        while self._running:
            try:
                self.tick()
            except Exception as e:
                logger.error(f"Error in {self.name}: {e}")
            
            # Simple sleep for interval (subtracting execution time could be better but this is fine)
            time.sleep(self.interval)

    @abstractmethod
    def tick(self):
        """Perform one iteration of the system logic."""
        pass


class SafetySystem(BackgroundSystem):
    """
    Monitors player vitals and safety.
    - Checks health
    - Equips armor (todo)
    - Emits critical events
    """
    
    def __init__(self, client, coordination_hub: CoordinationHub, resources=None):
        super().__init__(client, coordination_hub, "SafetySystem", interval=1.0, resources=resources)
        self.low_health_threshold = 8.0  # 4 hearts
        self._last_health = 20.0

    def tick(self):
        try:
            # Poll player state - transport is thread-safe via RLock
            state = self.client.transport.dispatch("get_state", {}, timeout=1.0)
            
            health = state.get("health", 20.0)
            
            # Check for death
            if health <= 0:
                logger.warning("Player is dead. Triggering respawn!")
                self.client.transport.dispatch("respawn", {})
                self.coordination.broadcast(SystemEvent(
                    event_type=EventType.PLAYER_DEATH,
                    source=self.name,
                    data={"death": True}
                ))
            
            # Broadcast critical event if health is low
            elif health < self.low_health_threshold and self._last_health >= self.low_health_threshold:
                logger.warning(f"Health critical: {health}")
                self.coordination.broadcast(SystemEvent(
                    event_type=EventType.HEALTH_CRITICAL,
                    source=self.name,
                    data={"health": health, "threshold": self.low_health_threshold}
                ))
            
            self._last_health = health
            
        except Exception as e:
            logger.error(f"Safety Check Failed: {e}")


class HungerSystem(BackgroundSystem):
    """
    Monitors food level and manages eating.
    """
    def __init__(self, client, coordination_hub: CoordinationHub, resources=None):
        super().__init__(client, coordination_hub, "HungerSystem", interval=2.0, resources=resources)
        self.min_food_level = 18  # Threshold to start eating
        self._last_food = 20
        self.food_priority = [
            "minecraft:golden_apple",
            "minecraft:enchanted_golden_apple",
            "minecraft:cooked_beef",
            "minecraft:cooked_porkchop",
            "minecraft:steak",
            "minecraft:cooked_mutton",
            "minecraft:cooked_chicken",
            "minecraft:cooked_rabbit",
            "minecraft:cooked_salmon",
            "minecraft:cooked_cod",
            "minecraft:bread",
            "minecraft:baked_potato",
            "minecraft:pumpkin_pie",
            "minecraft:mushroom_stew",
            "minecraft:beetroot_soup",
            "minecraft:rabbit_stew",
            "minecraft:apple",
            "minecraft:carrot",
            "minecraft:potato",  # Raw potato - low hunger restore
            "minecraft:melon_slice",
            "minecraft:sweet_berries",
            "minecraft:glow_berries",
            "minecraft:dried_kelp",
            # Raw meats - not ideal but better than starving
            "minecraft:beef",
            "minecraft:porkchop", 
            "minecraft:mutton",
            "minecraft:chicken",  # 30% chance of hunger effect
            "minecraft:rabbit",
            "minecraft:salmon",
            "minecraft:cod",
            # Absolute last resort - has negative effects
            "minecraft:rotten_flesh",  # 80% chance of hunger effect
            "minecraft:spider_eye",    # Poison - avoid if possible
        ]
        # Foods to avoid eating unless starving (food_level <= 2)
        self.desperate_only_foods = {
            "minecraft:rotten_flesh",
            "minecraft:spider_eye",
            "minecraft:poisonous_potato",
            "minecraft:pufferfish",
            "minecraft:chicken",  # Raw chicken has hunger chance
        }

    def tick(self):
        try:
            state = self.client.transport.dispatch("get_state", {}, timeout=1.0)
            
            food_level = state.get("food_level", state.get("food", 20))
            
            # Check if we need to eat
            if food_level < self.min_food_level:
                self.try_eat(food_level)

            # Broadcast critical event if food is VERY low
            crit_threshold = 6
            if food_level < crit_threshold and self._last_food >= crit_threshold:
                logger.warning(f"Hunger critical: {food_level}")
                self.coordination.broadcast(SystemEvent(
                    event_type=EventType.HUNGER_CRITICAL,
                    source=self.name,
                    data={"food_level": food_level, "threshold": crit_threshold}
                ))
            
            self._last_food = food_level
            
        except Exception as e:
            logger.error(f"Hunger Check Failed: {e}")

    def try_eat(self, current_food: int):
        """Attempt to find food and eat it."""
        from .actions import EatAction
        
        # We need to find the best food available
        # Refresh inventory cache if possible or just fetch it
        # Since we're in a background thread, strict cache sync with main thread isn't guaranteed
        # But we can query the bridge directly.
        
        try:
            inventory_data = self.client.transport.dispatch("get_inventory", {})
            all_items = inventory_data.get("inventory", []) + inventory_data.get("offhand", [])
            
            best_food = None
            
            # Create a localized map of available items
            available_items = set()
            for item in all_items:
                if item.get("count", 0) > 0:
                    available_items.add(item.get("id"))
            
            # Determine if we're desperate (very low food)
            is_desperate = current_food <= 2
            
            # Find highest priority food present
            for food_id in self.food_priority:
                if food_id in available_items:
                    # Skip desperate-only foods unless we're starving
                    if food_id in self.desperate_only_foods and not is_desperate:
                        continue
                    best_food = food_id
                    break
            
            if best_food:
                if best_food in self.desperate_only_foods:
                    logger.warning(f"HungerSystem: Desperately eating {best_food} (Food: {current_food})")
                else:
                    logger.info(f"HungerSystem: Eating {best_food} (Food: {current_food})")
                action = EatAction(best_food)
                result = action.execute(self.client)
                
                if result.success:
                    logger.info(f"HungerSystem: Finished eating {best_food}")
                else:
                    logger.warning(f"HungerSystem: Failed to eat {best_food}: {result.message}")
            else:
                # No food found in priority list
                logger.debug("HungerSystem: No suitable food found in inventory")
                pass

        except Exception as e:
            logger.error(f"Error in try_eat: {e}")

class MappingSystem(BackgroundSystem):
    """
    Passively scans environment for POIs.
    """
    def __init__(self, client, coordination_hub: CoordinationHub, resources=None):
        super().__init__(client, coordination_hub, "MappingSystem", interval=5.0, resources=resources)

    def tick(self):
        # Stub implementation
        pass
