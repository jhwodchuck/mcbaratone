"""
Inventory action implementation.
"""

import time
import logging
from typing import Dict, Optional, List

from baritone_client.core.interfaces import ActionContext, ActionResult
from baritone_client.actions.base import BaseAction

logger = logging.getLogger(__name__)

class InventoryAction(BaseAction):
    """Handles item management and equipment."""

    def execute(self, context: ActionContext) -> ActionResult:
        return ActionResult.fail("InventoryAction requires a specific method call")

    def get_inventory(self, context: ActionContext) -> Dict[str, int]:
        """Get aggregated inventory counts."""
        try:
            response = self.run_command(context, "get_inventory", {})
            data = response.get("data", response)
            counts: Dict[str, int] = {}
            
            for section in ["inventory", "armor", "offhand"]:
                for item in data.get(section, []):
                    item_id = item.get("id", "")
                    count = item.get("count", 0)
                    if item_id and count > 0:
                        counts[item_id] = counts.get(item_id, 0) + count
            return counts
        except Exception as e:
            logger.exception(f"Exception in get_inventory: {e}")
            return {}

    def count_item(self, context: ActionContext, item_id: str) -> int:
        """Count specific item in inventory."""
        inventory = self.get_inventory(context)
        return inventory.get(item_id, 0)

    def find_item_slot(self, context: ActionContext, item_id: str) -> Optional[int]:
        """Find slot containing specified item."""
        try:
            response = self.run_command(context, "get_inventory", {})
            data = response.get("data", response)
            
            for item in data.get("inventory", []):
                if item.get("id") == item_id and item.get("count", 0) > 0:
                    return item.get("slot")
            return None
        except Exception:
            return None

    def select_item(self, context: ActionContext, item_id: str, allow_swap: bool = False) -> bool:
        """Select item in hotbar."""
        slot = self.find_item_slot(context, item_id)
        if slot is None:
            return False
        
        # Hotbar is slots 0-8
        if 0 <= slot <= 8:
            self.run_command(context, "select_slot", {"slot": slot})
            return True
        
        if allow_swap:
            # Move to Hotbar 0 (Protocol 36)
            self.run_command(context, 'inventory_click', {'slot': slot, 'type': 'PICKUP', 'button': 0})
            time.sleep(0.15)
            self.run_command(context, 'inventory_click', {'slot': 36, 'type': 'PICKUP', 'button': 0})
            time.sleep(0.15)
            self.run_command(context, 'inventory_click', {'slot': slot, 'type': 'PICKUP', 'button': 0})
            time.sleep(0.15)
            self.run_command(context, 'select_slot', {'slot': 0})
            return True
            
        return False

    def equip_best_armor(self, context: ActionContext) -> int:
        """Equip best available armor from inventory."""
        materials = ["leather", "chainmail", "iron", "diamond", "netherite"]
        armor_slots = ["helmet", "chestplate", "leggings", "boots"]
        
        equipped = 0
        inventory = self.get_inventory(context)
        
        for slot_type in armor_slots:
            for material in reversed(materials):  # Best first
                item_id = f"minecraft:{material}_{slot_type}"
                if inventory.get(item_id, 0) > 0:
                    slot = self.find_item_slot(context, item_id)
                    if slot is not None:
                        try:
                            self.run_command(context, "inventory_click", {
                                "slot": slot,
                                "type": "PICKUP",
                                "button": 1,  # Right-click
                            })
                            equipped += 1
                        except:
                            pass
                    break
        return equipped

    def equip_best_weapon(self, context: ActionContext) -> bool:
        """Equip through the canonical shared combat loadout policy."""
        from baritone_client.common.inventory import equip_best_weapon

        return equip_best_weapon(context.client)

    def drop_items(self, context: ActionContext, item_ids: List[str]) -> int:
        """Drop specified items from inventory to clear space."""
        dropped = 0
        try:
            response = self.run_command(context, "get_inventory", {})
            items = response.get("data", response).get("inventory", [])
            
            for item in items:
                item_id = item.get("id")
                slot = item.get("slot")
                
                if item_id in item_ids:
                    self.run_command(context, "inventory_click", {
                        "slot": slot,
                        "type": "THROW",
                        "button": 1 
                    })
                    dropped += 1
                    time.sleep(0.1)
        except Exception as e:
            print(f"Drop items error: {e}")
            
        return dropped
