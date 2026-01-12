"""
Actions module for Minecraft automation test utilities.

This package contains reusable utility functions for block operations,
inventory management, and movement operations used in test suites.
"""

# Import commonly used functions for convenience
from .block_ops import (
    _block_id_at, _is_liquid, _in_range, _find_stand_pos, _move_near,
    _fill_plane_chunked, _fill_volume_chunked, _fill_hollow_shell,
    _place_block_at, bot_place_block, bot_build_hollow_box, bot_box_fill,
    build_simple_structure, prepare_base_test, fill_supply_chests
)

from .inventory_ops import (
    _open_supply_chest, _build_slot_map_from_screen, _refresh_supply_slot_map,
    _place_double_chest, _deposit_inventory_to_supply_chest, _withdraw_from_supply_chest,
    _ensure_item_from_supply, _smelt_in_furnace, _craft_bed_manual,
    _ensure_crafting_table_open, _get_workshop_furnace
)

from .movement_ops import _find_place_pos_near

__all__ = [
    # Block operations
    '_block_id_at', '_is_liquid', '_in_range', '_find_stand_pos', '_move_near',
    '_fill_plane_chunked', '_fill_volume_chunked', '_fill_hollow_shell',
    '_place_block_at', 'bot_place_block', 'bot_build_hollow_box', 'bot_box_fill',
    'build_simple_structure', 'prepare_base_test', 'fill_supply_chests',
    
    # Inventory operations
    '_open_supply_chest', '_build_slot_map_from_screen', '_refresh_supply_slot_map',
    '_place_double_chest', '_deposit_inventory_to_supply_chest', '_withdraw_from_supply_chest',
    '_ensure_item_from_supply', '_smelt_in_furnace', '_craft_bed_manual',
    '_ensure_crafting_table_open', '_get_workshop_furnace',
    
    # Movement operations
    '_find_place_pos_near'
]