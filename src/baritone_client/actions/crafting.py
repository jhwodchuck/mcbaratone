"""
Crafting action implementation.
"""

import time
from typing import Dict, List, Optional, Any

from baritone_client.core.interfaces import ActionContext, ActionResult
from baritone_client.actions.base import BaseAction
from baritone_client.actions.inventory import InventoryAction
from baritone_client.actions.movement import MovementAction

# Import helper for safety check if available, or re-implement
try:
    from baritone_client.common.base import is_position_safe
except ImportError:
    is_position_safe = lambda c, x, y, z: True # Fallback

class CraftingAction(BaseAction):
    """Handles crafting and smelting operations."""
    
    def __init__(self):
        self.inventory = InventoryAction()
        self.movement = MovementAction()
        
    def execute(self, context: ActionContext) -> ActionResult:
        return ActionResult.fail("CraftingAction requires a specific method call")

    def craft(self, context: ActionContext, item_id: str, count: int = 1) -> bool:
        """Attempt to craft an item (simple/inventory crafting)."""
        try:
            response = self.run_command(context, "craft", {
                "item": item_id,
                "count": count,
            })
            if response.get("status") == "ok":
                return True
            return False
        except Exception as e:
            print(f"Crafting failed: {e}")
            return False

    def place_block(self, context: ActionContext, x: int, y: int, z: int, item_id: str) -> bool:
        """Place a block at specified coordinates."""
        if not self.inventory.select_item(context, item_id):
            return False
            
        try:
            response = self.run_command(context, "place_block", {
                "x": x, "y": y, "z": z,
                "block": item_id
            })
            return response.get("status") == "ok"
        except Exception:
            return False

    def ensure_crafting_table(self, context: ActionContext) -> bool:
        """Finds or places a crafting table and opens it (Robust)."""
        # 1. Try to find existing nearby using get_view (voxel scan)
        view_res = self.run_command(context, 'get_view', {'radius': 16})
        voxels = view_res.get('voxels', [])
        found_table = None
        
        for v in voxels:
            if v.get('id') == 'minecraft:crafting_table':
                found_table = v
                break
                
        if found_table:
            tx, ty, tz = found_table['x'], found_table['y'], found_table['z']
            
            # Move near (x+1)
            self.run_command(context, "goto", {"x": tx+1, "y": ty, "z": tz})
            time.sleep(4.0) 
            
            # Interact
            self.run_command(context, 'look_at', {'x': tx+0.5, 'y': ty+0.5, 'z': tz+0.5})
            time.sleep(0.1)
            self.run_command(context, 'interact_block', {'x': tx, 'y': ty, 'z': tz, 'hand': 'MAIN_HAND'})
            time.sleep(1.0)
            return True
            
        # 2. Check/Craft crafting table item
        if self.inventory.count_item(context, "minecraft:crafting_table") == 0:
             # Check planks
             planks_count = 0
             plank_types = ["oak", "spruce", "birch", "dark_oak", "acacia", "jungle", "mangrove", "cherry"]
             for wood in plank_types:
                 planks_count += self.inventory.count_item(context, f"minecraft:{wood}_planks")
                 
             if planks_count < 4:
                 self.craft(context, "minecraft:spruce_planks", 1) 
                 self.craft(context, "minecraft:oak_planks", 1) 
             
             self.craft(context, "minecraft:crafting_table", 1)
             time.sleep(0.3)
             
        # 3. Place table
        inv = self.run_command(context, 'get_inventory', {})
        table_slot = None
        for item in inv.get('inventory', []):
            if item.get('id') == 'minecraft:crafting_table' and item.get('count', 0) > 0:
                table_slot = item.get('slot')
                break
        
        if table_slot is None:
            return False

        # Move to hotbar logic
        if table_slot >= 9:
            self.run_command(context, 'inventory_click', {'slot': table_slot, 'type': 'PICKUP', 'button': 0})
            time.sleep(0.3)
            self.run_command(context, 'inventory_click', {'slot': 36, 'type': 'PICKUP', 'button': 0})
            time.sleep(0.3)
            self.run_command(context, 'inventory_click', {'slot': table_slot, 'type': 'PICKUP', 'button': 0})
            time.sleep(0.3)
            self.run_command(context, 'select_slot', {'slot': 0})
        else:
            self.run_command(context, 'select_slot', {'slot': table_slot})
        
        time.sleep(0.3)
        
        # Place logic
        state = self.run_command(context, 'get_state', {})
        pos = state.get('block_position', {})
        x, y, z = int(pos.get('x', 0)), int(pos.get('y', 0)), int(pos.get('z', 0))
        
        positions = [
            (x+1, y, z), (x-1, y, z), (x, y, z+1), (x, y, z-1),
            (x+2, y, z), (x-2, y, z), (x, y, z+2), (x, y, z-2),
             (x+1, y+1, z), (x-1, y+1, z)
        ]
        
        placed_pos = None
        for px, py, pz in positions:
            if not is_position_safe(context.client, px, py, pz):
                continue

            check = self.run_command(context, 'get_block', {'x': px, 'y': py, 'z': pz})
            bid = check.get('id', '')
            
            soft_blocks = ['air', 'grass', 'fern', 'leaf', 'flower', 'snow']
            is_soft = any(s in bid for s in soft_blocks)
            
            if bid and 'air' not in bid and bid != 'minecraft:air' and not is_soft:
                continue
                
            self.run_command(context, "chat", {"message": f"#place crafting_table {px} {py} {pz}"})
            time.sleep(3.0) 
            
            check = self.run_command(context, 'get_block', {'x': px, 'y': py, 'z': pz})
            if 'crafting_table' in check.get('id', ''):
                placed_pos = (px, py, pz)
                break
                
        if not placed_pos:
            # Mining fallback
            tx, ty, tz = x+1, y, z
            self.run_command(context, 'mine', {'x': tx, 'y': ty, 'z': tz})
            time.sleep(4.0)
            self.run_command(context, 'cancel', {})
            time.sleep(0.5)
            self.run_command(context, 'place_block', {'x': tx, 'y': ty, 'z': tz})
            time.sleep(0.5)
            check = self.run_command(context, 'get_block', {'x': tx, 'y': ty, 'z': tz})
            if 'crafting_table' in check.get('id', ''):
                 placed_pos = (tx, ty, tz)

        if not placed_pos:
            return False
            
        # Blacklist
        self.run_command(context, "chat", {"message": "#blacklist minecraft:crafting_table"})
        
        # Open
        self.run_command(context, 'interact_block', {'x': placed_pos[0], 'y': placed_pos[1], 'z': placed_pos[2]})
        time.sleep(2.0)
        return True

    def craft_with_table(self, context: ActionContext, item_id: str, count: int = 1) -> bool:
        """Craft an item requiring a table."""
        if not self.ensure_crafting_table(context):
            return False
            
        return self.craft(context, item_id, count)

    def smelt(self, context: ActionContext, item_id: str, count: int = 1) -> bool:
        """Smelt items using a furnace."""
        # Simple placeholder port
        furnace_pos = self.movement.find_nearby_block(context, ["minecraft:furnace"], radius=4)
        if furnace_pos:
            self.run_command(context, "interact_block", {"x": furnace_pos[0], "y": furnace_pos[1], "z": furnace_pos[2]})
            time.sleep(1)
            print(f"Smelting {item_id} (Stub)")
            return True
        return False
