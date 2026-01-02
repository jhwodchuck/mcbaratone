
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
            # We use "get_state" or "get_player_info" if available.
            # Assuming get_state contains health or we can fetch it.
            # Since get_state might be heavy, we should check if there is a lighter "get_vitals"
            # or rely on the State returned by the bridge.
            
            # Currently bridge 'get_state' returns a lot. Let's try to use it or rely on events?
            # Ideally we subscribe to TICK_UPDATE events from the bridge if we were purely event driven.
            # But the bridge *does* emit events. Maybe we should just subscribe to them?
            # But BackgroundSystem implies active polling or processing.
            
            # For this implementation, let's poll get_state quickly.
            # CAUTION: calling client.transport.dispatch inside a thread while main thread 
            # might be doing the same could cause race conditions on the socket if not thread-safe.
            # The TcpTransport needs to be thread-safe or we need a lock.
            
            # Checking TcpTransport safety... standard socket send/recv is not thread safe if interleaved.
            # We might need a lock on the transport.
            
            # For now, let's assume we read from a cached state in the client if available, 
            # or we accept the risk/responsibility. 
            pass
            
            # TODO: Implement safe polling or subscribe to CoordinationHub events bridged from the game?
            # Actually, the Bridge emits events. The CoordinationHub could receive them.
            # But we are connecting Client -> Bridge via TCP.
            
        except Exception as e:
            logger.error(f"Safety Check Failed: {e}")

    # Re-thinking: SafetySystem as a listener + periodic logic.
    # If using threads, we MUST insure transport is thread safe.
    # Let's verify TcpTransport implementation.

class HungerSystem(BackgroundSystem):
    """
    Monitors food level and manages eating.
    """
    def __init__(self, client, coordination_hub: CoordinationHub, resources=None):
        super().__init__(client, coordination_hub, "HungerSystem", interval=2.0, resources=resources)
        self.min_food_level = 6

    def tick(self):
        # Stub implementation
        pass

class MappingSystem(BackgroundSystem):
    """
    Passively scans environment for POIs.
    """
    def __init__(self, client, coordination_hub: CoordinationHub, resources=None):
        super().__init__(client, coordination_hub, "MappingSystem", interval=5.0, resources=resources)

    def tick(self):
        # Stub implementation
        pass
