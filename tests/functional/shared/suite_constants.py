"""
Suite Constants Module

Standardized timeouts and constants for Extended Suite 1000 tests.
"""

import time

# Timeout constants by operation complexity
TIMEOUTS = {
    # Simple operations (single block/item)
    "simple_craft": 60,        # Single item crafting
    "block_place": 10,         # Single block placement
    "inventory_sync": 3,       # Inventory synchronization
    "container_open": 5,       # Opening a container
    
    # Medium operations
    "complex_craft": 120,      # Multi-step crafting
    "smelting": 120,           # Furnace smelting operation
    "structure_small": 180,    # Small structure (5x5 shack)
    
    # Complex operations
    "complex_build": 300,      # Structure building (house, storage)
    "multi_step": 600,         # Multi-phase operations like T1002B
    
    # Full suite operations
    "integration": 900,        # Full suite integration tests
}

# Inventory size constants
PLAYER_INVENTORY_SIZE = 36    # 9 hotbar + 27 main inventory
PLAYER_HOTBAR_SIZE = 9
SINGLE_CHEST_SIZE = 27
DOUBLE_CHEST_SIZE = 54

# Block placement constants
MAX_PLACE_RANGE = 5          # Blocks player can reach from standing position
BARITONE_PLACE_RANGE = 4     # Conservative Baritone placement range

# Supply chest configuration
SUPPLY_CHEST_SPACING = 2     # Gap between single chests to prevent merging
MAX_SUPPLY_CHESTS = 8        # Default number of supply chests

# Polling constants
DEFAULT_POLL_INTERVAL = 0.2  # seconds between polls
MIN_POLL_INTERVAL = 0.05     # minimum for fast operations


def get_timeout(operation: str, default: int = 120) -> int:
    """
    Get timeout for a specific operation type.
    
    Args:
        operation: Name of the operation (key in TIMEOUTS dict)
        default: Default timeout if operation not found
        
    Returns:
        Timeout in seconds
    """
    return TIMEOUTS.get(operation, default)


def poll_until(condition_fn, timeout: float = 5.0, interval: float = 0.2) -> bool:
    """
    Poll until condition is met or timeout.
    
    Replaces bare time.sleep() with proper polling pattern.
    
    Args:
        condition_fn: Callable returning True when condition is met
        timeout: Maximum time to wait
        interval: Time between polls
        
    Returns:
        True if condition was met, False on timeout
        
    Example:
        >>> # Wait for block to appear
        >>> poll_until(lambda: "chest" in block_id_at(ctx, x, y, z), timeout=3.0)
    """
    start = time.time()
    while time.time() - start < timeout:
        if condition_fn():
            return True
        time.sleep(interval)
    return False


__all__ = [
    "TIMEOUTS",
    "PLAYER_INVENTORY_SIZE",
    "PLAYER_HOTBAR_SIZE", 
    "SINGLE_CHEST_SIZE",
    "DOUBLE_CHEST_SIZE",
    "MAX_PLACE_RANGE",
    "BARITONE_PLACE_RANGE",
    "SUPPLY_CHEST_SPACING",
    "MAX_SUPPLY_CHESTS",
    "get_timeout",
    "poll_until",
    "DEFAULT_POLL_INTERVAL",
    "MIN_POLL_INTERVAL",
]
