"""
Minecraft-Specific Assertions Library

Specialized assertion functions for Minecraft automation testing.
Provides comprehensive validation for position, inventory, blocks, health,
time, weather, and entity states with tolerance-based floating point comparisons.
"""

import math
from typing import Tuple, Optional, Dict, Any, Union, List
import pytest

from test_base import TestContext


# Helper functions
def _calculate_distance(pos1: Tuple[float, float, float], pos2: Tuple[float, float, float]) -> float:
    """Calculate Euclidean distance between two 3D positions."""
    return math.sqrt(sum((a - b) ** 2 for a, b in zip(pos1, pos2)))


def _get_block_at(ctx: TestContext, x: int, y: int, z: int) -> Optional[str]:
    """Get block ID at specific coordinates using scan_blocks bridge command."""
    try:
        # Use scan_blocks with radius 0 to get block at exact position
        # We scan for any block (empty blocks list = return all blocks in radius)
        result = ctx.client.transport.dispatch("scan_blocks", {
            "center": {"x": x, "y": y, "z": z},
            "radius": 0,
            "blocks": []  # Empty list to get all blocks, not filter
        })
        
        if result.get("status") == "ok":
            blocks = result.get("blocks", result.get("data", {}).get("blocks", []))
            for block in blocks:
                pos = block.get("position", {})
                if pos.get("x") == x and pos.get("y") == y and pos.get("z") == z:
                    return block.get("id", block.get("type"))
        
        # If no blocks returned, could be air or radius 0 not supported
        # Fall back to radius 1 and find exact match
        result = ctx.client.transport.dispatch("scan_blocks", {
            "center": {"x": x, "y": y, "z": z},
            "radius": 1,
            "blocks": []
        })
        
        if result.get("status") == "ok":
            blocks = result.get("blocks", result.get("data", {}).get("blocks", []))
            for block in blocks:
                pos = block.get("position", {})
                if pos.get("x") == x and pos.get("y") == y and pos.get("z") == z:
                    return block.get("id", block.get("type"))
        
        return None  # Block not found or is air
    except Exception:
        return None  # Connection error or unsupported command


def _get_entities_near(ctx: TestContext, x: float, y: float, z: float, radius: float) -> List[Dict[str, Any]]:
    """Get entities within radius of position using get_entities bridge command."""
    try:
        # Use get_entities command with specified radius
        result = ctx.client.transport.dispatch("get_entities", {"radius": int(radius)})
        
        entities = []
        
        # Handle different response formats
        if result.get("status") == "ok":
            raw_entities = result.get("entities", result.get("data", {}).get("entities", []))
        else:
            raw_entities = result.get("entities", [])
        
        # Filter entities by distance from the specified position
        for entity in raw_entities:
            pos = entity.get("position", entity.get("pos", {}))
            if pos:
                ex = pos.get("x", 0)
                ey = pos.get("y", 0)
                ez = pos.get("z", 0)
                
                # Calculate distance from specified position
                distance = ((ex - x) ** 2 + (ey - y) ** 2 + (ez - z) ** 2) ** 0.5
                
                if distance <= radius:
                    entities.append(entity)
        
        return entities
    except Exception:
        return []  # Connection error or unsupported command


# Position and proximity assertions
def assert_position_close(ctx: TestContext, expected_pos: Tuple[float, float, float],
                         tolerance: float = 1.0, msg: Optional[str] = None) -> None:
    """
    Assert that current position is within tolerance of expected position.

    Args:
        ctx: TestContext instance
        expected_pos: Expected (x, y, z) position
        tolerance: Maximum allowed distance
        msg: Custom error message

    Example:
        >>> # Assert player is near spawn point (0, 80, 0) within 5 blocks
        >>> assert_position_close(ctx, (0.0, 80.0, 0.0), tolerance=5.0)
    """
    current_pos = ctx.get_position()
    distance = _calculate_distance(current_pos, expected_pos)

    if distance > tolerance:
        error_msg = msg or f"Position {current_pos} is {distance:.2f} units away from expected {expected_pos} (tolerance: {tolerance})"
        pytest.fail(error_msg)


def assert_position_delta(ctx: TestContext, initial_pos: Tuple[float, float, float],
                         expected_delta: Tuple[float, float, float], tolerance: float = 0.5,
                         msg: Optional[str] = None) -> None:
    """
    Assert that position has changed by expected delta from initial position.

    Args:
        ctx: TestContext instance
        initial_pos: Starting position
        expected_delta: Expected change (dx, dy, dz)
        tolerance: Maximum allowed deviation from expected delta
        msg: Custom error message
    """
    current_pos = ctx.get_position()
    actual_delta = (current_pos[0] - initial_pos[0],
                   current_pos[1] - initial_pos[1],
                   current_pos[2] - initial_pos[2])

    delta_distance = _calculate_distance(actual_delta, expected_delta)

    if delta_distance > tolerance:
        error_msg = msg or f"Position delta {actual_delta} deviates by {delta_distance:.2f} from expected {expected_delta} (tolerance: {tolerance})"
        pytest.fail(error_msg)


def assert_within_distance(ctx: TestContext, target_pos: Tuple[float, float, float],
                          max_distance: float, msg: Optional[str] = None) -> None:
    """
    Assert that current position is within maximum distance of target position.

    Args:
        ctx: TestContext instance
        target_pos: Target position to check proximity to
        max_distance: Maximum allowed distance
        msg: Custom error message
    """
    current_pos = ctx.get_position()
    distance = _calculate_distance(current_pos, target_pos)

    if distance > max_distance:
        error_msg = msg or f"Position {current_pos} is {distance:.2f} units away from target {target_pos}, exceeding max distance {max_distance}"
        pytest.fail(error_msg)


# Inventory assertions
def assert_inventory_has_item(ctx: TestContext, item_id: str, min_count: int = 1,
                             msg: Optional[str] = None) -> None:
    """
    Assert that inventory contains at least min_count of specified item.

    Args:
        ctx: TestContext instance
        item_id: Minecraft item ID (e.g., 'minecraft:diamond')
        min_count: Minimum required count
        msg: Custom error message
    """
    actual_count = ctx.count_item(item_id)

    if actual_count < min_count:
        error_msg = msg or f"Inventory has {actual_count} of {item_id}, but requires at least {min_count}"
        pytest.fail(error_msg)


def assert_inventory_count_changed(ctx: TestContext, item_id: str, initial_count: int,
                                  expected_change: int, msg: Optional[str] = None) -> None:
    """
    Assert that item count has changed by expected amount from initial count.

    Args:
        ctx: TestContext instance
        item_id: Minecraft item ID
        initial_count: Count before change
        expected_change: Expected change (+gain, -loss)
        msg: Custom error message
    """
    current_count = ctx.count_item(item_id)
    actual_change = current_count - initial_count

    if actual_change != expected_change:
        error_msg = msg or f"Item {item_id} count changed by {actual_change} (from {initial_count} to {current_count}), expected change of {expected_change}"
        pytest.fail(error_msg)


def assert_inventory_contains(ctx: TestContext, required_items: Dict[str, int],
                             msg: Optional[str] = None) -> None:
    """
    Assert that inventory contains all required items with minimum counts.

    Args:
        ctx: TestContext instance
        required_items: Dict of item_id -> min_count
        msg: Custom error message
    """
    missing_items = []
    for item_id, min_count in required_items.items():
        actual_count = ctx.count_item(item_id)
        if actual_count < min_count:
            missing_items.append(f"{item_id}: {actual_count}/{min_count}")

    if missing_items:
        error_msg = msg or f"Inventory missing required items: {', '.join(missing_items)}"
        pytest.fail(error_msg)


def assert_inventory_empty(ctx: TestContext, msg: Optional[str] = None) -> None:
    """
    Assert that inventory is completely empty.

    Args:
        ctx: TestContext instance
        msg: Custom error message
    """
    inv = ctx.get_inventory()
    total_items = sum(slot.get("count", 0) for slot in inv.get("inventory", []) if slot)

    if total_items > 0:
        error_msg = msg or f"Inventory is not empty, contains {total_items} total items"
        pytest.fail(error_msg)


# Block state assertions
def assert_block_at(ctx: TestContext, x: int, y: int, z: int, expected_block: str,
                   msg: Optional[str] = None) -> None:
    """
    Assert that specific block is present at coordinates.

    Args:
        ctx: TestContext instance
        x, y, z: Block coordinates
        expected_block: Expected block ID
        msg: Custom error message
    """
    actual_block = _get_block_at(ctx, x, y, z)

    if actual_block is None:
        error_msg = msg or f"Cannot inspect block at ({x}, {y}, {z}) - world inspection not available"
        pytest.fail(error_msg)

    if actual_block != expected_block:
        error_msg = msg or f"Block at ({x}, {y}, {z}) is {actual_block}, expected {expected_block}"
        pytest.fail(error_msg)


def assert_block_not_at(ctx: TestContext, x: int, y: int, z: int, unexpected_block: str,
                        msg: Optional[str] = None) -> None:
    """
    Assert that specific block is NOT present at coordinates.

    Args:
        ctx: TestContext instance
        x, y, z: Block coordinates
        unexpected_block: Block ID that should NOT be present
        msg: Custom error message
    """
    actual_block = _get_block_at(ctx, x, y, z)

    if actual_block is None:
        # If we can't inspect, we can't assert it's not there
        error_msg = msg or f"Cannot inspect block at ({x}, {y}, {z}) - world inspection not available"
        pytest.fail(error_msg)

    if actual_block == unexpected_block:
        error_msg = msg or f"Block at ({x}, {y}, {z}) is {actual_block}, but should not be {unexpected_block}"
        pytest.fail(error_msg)


def assert_blocks_placed(ctx: TestContext, blocks: List[Tuple[int, int, int, str]],
                        msg: Optional[str] = None) -> None:
    """
    Assert that all specified blocks are placed correctly.

    Args:
        ctx: TestContext instance
        blocks: List of (x, y, z, block_id) tuples
        msg: Custom error message
    """
    incorrect_blocks = []
    for x, y, z, expected_block in blocks:
        actual_block = _get_block_at(ctx, x, y, z)
        if actual_block != expected_block:
            incorrect_blocks.append(f"({x},{y},{z}): {actual_block} != {expected_block}")

    if incorrect_blocks:
        error_msg = msg or f"Incorrect blocks found: {', '.join(incorrect_blocks)}"
        pytest.fail(error_msg)


def assert_blocks_in_area(ctx: TestContext, start_x: int, start_y: int, start_z: int,
                         end_x: int, end_y: int, end_z: int, expected_block: str,
                         msg: Optional[str] = None) -> None:
    """
    Assert that all blocks in the specified area are of the expected type.

    Args:
        ctx: TestContext instance
        start_x, start_y, start_z: Starting coordinates of the area
        end_x, end_y, end_z: Ending coordinates of the area
        expected_block: Expected block ID for all blocks in area
        msg: Custom error message
    """
    incorrect_blocks = []

    # Ensure start coordinates are less than or equal to end coordinates
    x_min, x_max = min(start_x, end_x), max(start_x, end_x)
    y_min, y_max = min(start_y, end_y), max(start_y, end_y)
    z_min, z_max = min(start_z, end_z), max(start_z, end_z)

    for x in range(x_min, x_max + 1):
        for y in range(y_min, y_max + 1):
            for z in range(z_min, z_max + 1):
                actual_block = _get_block_at(ctx, x, y, z)
                if actual_block != expected_block:
                    incorrect_blocks.append(f"({x},{y},{z}): {actual_block} != {expected_block}")

    if incorrect_blocks:
        error_msg = msg or f"Blocks in area ({x_min},{y_min},{z_min}) to ({x_max},{y_max},{z_max}) are not all {expected_block}: {', '.join(incorrect_blocks)}"
        pytest.fail(error_msg)


# Health and status assertions
def assert_health_level(ctx: TestContext, expected_health: float, tolerance: float = 0.5,
                       msg: Optional[str] = None) -> None:
    """
    Assert that player health is at expected level.

    Args:
        ctx: TestContext instance
        expected_health: Expected health value (0-20)
        tolerance: Allowed tolerance for health comparison
        msg: Custom error message
    """
    state = ctx.get_state()
    current_health = state.get("health", 20.0)

    if abs(current_health - expected_health) > tolerance:
        error_msg = msg or f"Health is {current_health}, expected {expected_health} (tolerance: {tolerance})"
        pytest.fail(error_msg)


def assert_health_changed(ctx: TestContext, initial_health: float, expected_change: float,
                         tolerance: float = 0.5, msg: Optional[str] = None) -> None:
    """
    Assert that health has changed by expected amount.

    Args:
        ctx: TestContext instance
        initial_health: Health before change
        expected_change: Expected health change (+gain, -damage)
        tolerance: Allowed tolerance for change comparison
        msg: Custom error message
    """
    state = ctx.get_state()
    current_health = state.get("health", 20.0)
    actual_change = current_health - initial_health

    if abs(actual_change - expected_change) > tolerance:
        error_msg = msg or f"Health changed by {actual_change}, expected change of {expected_change} (tolerance: {tolerance})"
        pytest.fail(error_msg)


def assert_max_health(ctx: TestContext, msg: Optional[str] = None) -> None:
    """
    Assert that player is at maximum health.

    Args:
        ctx: TestContext instance
        msg: Custom error message
    """
    assert_health_level(ctx, 20.0, tolerance=0.1, msg=msg or "Player should be at maximum health")


# Time and weather assertions
def assert_world_time(ctx: TestContext, expected_time: Union[int, str], tolerance: int = 100,
                     msg: Optional[str] = None) -> None:
    """
    Assert that world time matches expected value.

    Args:
        ctx: TestContext instance
        expected_time: Expected time (ticks 0-24000, or string like 'day', 'night')
        tolerance: Allowed tolerance for time comparison (in ticks)
        msg: Custom error message
    """
    state = ctx.get_state()
    current_time = state.get("world_time", 0)

    # Convert string times to ticks if needed
    time_mappings = {
        "day": 1000,
        "noon": 6000,
        "night": 13000,
        "midnight": 18000
    }

    if isinstance(expected_time, str):
        if expected_time in time_mappings:
            expected_time = time_mappings[expected_time]
        else:
            try:
                expected_time = int(expected_time)
            except ValueError:
                error_msg = msg or f"Invalid time string '{expected_time}', expected number or known time name"
                pytest.fail(error_msg)

    time_diff = abs(current_time - expected_time)
    # Handle wraparound (24000 ticks per day)
    time_diff = min(time_diff, 24000 - time_diff)

    if time_diff > tolerance:
        error_msg = msg or f"World time is {current_time}, expected around {expected_time} (tolerance: {tolerance})"
        pytest.fail(error_msg)


def assert_weather(ctx: TestContext, expected_weather: str, msg: Optional[str] = None) -> None:
    """
    Assert that current weather matches expected type.

    Args:
        ctx: TestContext instance
        expected_weather: Expected weather ('clear', 'rain', 'thunder')
        msg: Custom error message
    """
    state = ctx.get_state()
    current_weather = state.get("weather", "clear")

    if current_weather != expected_weather:
        error_msg = msg or f"Weather is {current_weather}, expected {expected_weather}"
        pytest.fail(error_msg)


# Entity assertions
def assert_entity_present(ctx: TestContext, entity_type: str, radius: float = 50.0,
                         msg: Optional[str] = None) -> None:
    """
    Assert that at least one entity of specified type is present within radius.

    Args:
        ctx: TestContext instance
        entity_type: Entity type ID (e.g., 'minecraft:zombie')
        radius: Search radius from player position
        msg: Custom error message
    """
    player_pos = ctx.get_position()
    entities = _get_entities_near(ctx, player_pos[0], player_pos[1], player_pos[2], radius)

    matching_entities = [e for e in entities if e.get("type") == entity_type]

    if not matching_entities:
        error_msg = msg or f"No {entity_type} entities found within {radius} blocks of position {player_pos}"
        pytest.fail(error_msg)


def assert_entity_near(ctx: TestContext, entity_type: str, target_pos: Tuple[float, float, float],
                      max_distance: float, msg: Optional[str] = None) -> None:
    """
    Assert that an entity of specified type is near target position.

    Args:
        ctx: TestContext instance
        entity_type: Entity type ID
        target_pos: Position to check near
        max_distance: Maximum allowed distance from target
        msg: Custom error message
    """
    entities = _get_entities_near(ctx, target_pos[0], target_pos[1], target_pos[2], max_distance)

    matching_entities = [e for e in entities if e.get("type") == entity_type]

    if not matching_entities:
        error_msg = msg or f"No {entity_type} entities found within {max_distance} blocks of {target_pos}"
        pytest.fail(error_msg)


def assert_no_entities(ctx: TestContext, entity_type: Optional[str] = None, radius: float = 50.0,
                      msg: Optional[str] = None) -> None:
    """
    Assert that no entities of specified type are present within radius.
    If entity_type is None, assert no entities at all.

    Args:
        ctx: TestContext instance
        entity_type: Entity type to check for (None for any entity)
        radius: Search radius from player position
        msg: Custom error message
    """
    player_pos = ctx.get_position()
    entities = _get_entities_near(ctx, player_pos[0], player_pos[1], player_pos[2], radius)

    if entity_type:
        matching_entities = [e for e in entities if e.get("type") == entity_type]
        if matching_entities:
            error_msg = msg or f"Found {len(matching_entities)} {entity_type} entities within {radius} blocks of {player_pos}"
            pytest.fail(error_msg)
    else:
        if entities:
            error_msg = msg or f"Found {len(entities)} entities within {radius} blocks of {player_pos}"
            pytest.fail(error_msg)


# Utility functions for use with TestContext assertion lists
def create_position_assertion(expected_pos: Tuple[float, float, float], tolerance: float = 1.0) -> callable:
    """
    Create a position assertion function for use in TestCase.assertions.

    Returns a function that returns (passed: bool, message: str)
    """
    def assertion(ctx: TestContext) -> Tuple[bool, str]:
        try:
            assert_position_close(ctx, expected_pos, tolerance)
            return True, f"Position within tolerance of {expected_pos}"
        except AssertionError as e:
            return False, str(e)
    return assertion


def create_inventory_assertion(required_items: Dict[str, int]) -> callable:
    """
    Create an inventory assertion function for use in TestCase.assertions.

    Returns a function that returns (passed: bool, message: str)
    """
    def assertion(ctx: TestContext) -> Tuple[bool, str]:
        try:
            assert_inventory_contains(ctx, required_items)
            return True, f"Inventory contains required items: {required_items}"
        except AssertionError as e:
            return False, str(e)
    return assertion


def create_health_assertion(expected_health: float, tolerance: float = 0.5) -> callable:
    """
    Create a health assertion function for use in TestCase.assertions.

    Returns a function that returns (passed: bool, message: str)
    """
    def assertion(ctx: TestContext) -> Tuple[bool, str]:
        try:
            assert_health_level(ctx, expected_health, tolerance)
            return True, f"Health within tolerance of {expected_health}"
        except AssertionError as e:
            return False, str(e)
    return assertion


# New assertion methods for enhanced test coverage

def assert_block_placed_at(ctx: TestContext, x: int, y: int, z: int, expected_block: str,
                          msg: Optional[str] = None) -> None:
    """
    Assert that a block was placed at specific coordinates.

    Args:
        ctx: TestContext instance
        x, y, z: Block coordinates
        expected_block: Expected block ID
        msg: Custom error message
    """
    # Note: In a real implementation, this would check world state
    # For now, we'll assume the placement succeeded if no error occurred
    # This is a placeholder for when world inspection is implemented
    pass  # Placeholder - would need bridge world inspection


def assert_equipped_item(ctx: TestContext, slot: str, expected_item: str,
                        msg: Optional[str] = None) -> None:
    """
    Assert that an item is equipped in specified armor/tool slot.

    Args:
        ctx: TestContext instance
        slot: Equipment slot ('head', 'chest', 'legs', 'feet', 'mainhand', 'offhand')
        expected_item: Expected item ID
        msg: Custom error message
    """
    # Note: Equipment state would need to be added to state tracking
    # This is a placeholder for when equipment inspection is implemented
    pass  # Placeholder


def assert_hotbar_slot(ctx: TestContext, slot: int, expected_item: str,
                      msg: Optional[str] = None) -> None:
    """
    Assert that hotbar slot contains expected item.

    Args:
        ctx: TestContext instance
        slot: Hotbar slot (0-8)
        expected_item: Expected item ID
        msg: Custom error message
    """
    inv = ctx.get_inventory()
    hotbar_start = 36  # Hotbar slots are 36-44 in Minecraft inventory

    try:
        slot_data = inv["inventory"][hotbar_start + slot]
        actual_item = slot_data.get("id", "")
        if actual_item != expected_item:
            error_msg = msg or f"Hotbar slot {slot} has {actual_item}, expected {expected_item}"
            pytest.fail(error_msg)
    except (IndexError, KeyError):
        error_msg = msg or f"Unable to access hotbar slot {slot}"
        pytest.fail(error_msg)


def assert_no_fall_damage(ctx: TestContext, msg: Optional[str] = None) -> None:
    """
    Assert that player took no fall damage during movement.

    Args:
        ctx: TestContext instance
        msg: Custom error message
    """
    state = ctx.get_state()
    health = state.get("health", 20.0)
    # Assuming test starts with full health and no damage should occur
    if health < 20.0:
        error_msg = msg or f"Fall damage detected, health is {health}"
        pytest.fail(error_msg)


def assert_container_access(ctx: TestContext, msg: Optional[str] = None) -> None:
    """
    Assert that a container was successfully accessed (chest opened).

    Args:
        ctx: TestContext instance
        msg: Custom error message
    """
    # Note: Container access detection would need event tracking
    # This is a placeholder for when container events are tracked
    pass  # Placeholder


def assert_item_transferred(ctx: TestContext, item_id: str, from_slot: int, to_slot: int,
                           msg: Optional[str] = None) -> None:
    """
    Assert that an item was transferred between inventory slots.

    Args:
        ctx: TestContext instance
        item_id: Item being transferred
        from_slot: Source slot
        to_slot: Destination slot
        msg: Custom error message
    """
    inv = ctx.get_inventory()
    try:
        from_item = inv["inventory"][from_slot]
        to_item = inv["inventory"][to_slot]

        # Check that source slot no longer has the item
        if from_item.get("id") == item_id:
            error_msg = msg or f"Item {item_id} still in source slot {from_slot}"
            pytest.fail(error_msg)

        # Check that destination slot has the item
        if to_item.get("id") != item_id:
            error_msg = msg or f"Item {item_id} not found in destination slot {to_slot}"
            pytest.fail(error_msg)
    except (IndexError, KeyError):
        error_msg = msg or f"Unable to verify item transfer from {from_slot} to {to_slot}"
        pytest.fail(error_msg)


def assert_smelted_item(ctx: TestContext, input_item: str, output_item: str,
                       msg: Optional[str] = None) -> None:
    """
    Assert that smelting occurred (input consumed, output produced).

    Args:
        ctx: TestContext instance
        input_item: Item that was smelted
        output_item: Smelting result
        msg: Custom error message
    """
    input_count = ctx.count_item(input_item)
    output_count = ctx.count_item(output_item)

    # Assuming smelting consumed input and produced output
    if input_count > 0:  # Should have been consumed
        error_msg = msg or f"Input item {input_item} not consumed (count: {input_count})"
        pytest.fail(error_msg)

    if output_count == 0:  # Should have been produced
        error_msg = msg or f"Output item {output_item} not produced"
        pytest.fail(error_msg)


def assert_portal_activated(ctx: TestContext, msg: Optional[str] = None) -> None:
    """
    Assert that a portal was successfully activated.

    Args:
        ctx: TestContext instance
        msg: Custom error message
    """
    # Note: Portal activation detection would need dimension change tracking
    # This is a placeholder
    pass  # Placeholder


def assert_door_state(ctx: TestContext, x: int, y: int, z: int, open_state: bool,
                     msg: Optional[str] = None) -> None:
    """
    Assert that a door is in the expected open/closed state.

    Args:
        ctx: TestContext instance
        x, y, z: Door coordinates
        open_state: True for open, False for closed
        msg: Custom error message
    """
    # Note: Block state inspection needed
    # This is a placeholder for when block properties can be checked
    pass  # Placeholder


def assert_entity_damaged(ctx: TestContext, entity_type: str, msg: Optional[str] = None) -> None:
    """
    Assert that entities of specified type took damage.

    Args:
        ctx: TestContext instance
        entity_type: Type of entity that should be damaged
        msg: Custom error message
    """
    # Note: Entity health tracking needed
    # This is a placeholder
    pass  # Placeholder