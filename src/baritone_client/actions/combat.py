"""
Combat action implementation.
"""

import time
from typing import Dict, List, Optional

from baritone_client.core.interfaces import ActionContext, ActionResult
from baritone_client.actions.base import BaseAction
from baritone_client.actions.inventory import InventoryAction
from baritone_client.common.combat import entity_position, hunt_mobs

class CombatAction(BaseAction):
    """Handles mob engagement and self-defense."""
    
    def __init__(self):
        self.inventory = InventoryAction()
        
    def execute(self, context: ActionContext) -> ActionResult:
        return ActionResult.fail("CombatAction requires a specific method call")

    def get_nearby_entities(self, context: ActionContext, radius: int = 30) -> List[Dict]:
        """Get list of nearby entities."""
        try:
            data = self.run_command(context, "get_entities", {"radius": radius})
            return data.get("entities", [])
        except Exception:
            return []

    def find_entity_by_type(self, context: ActionContext, entity_types: List[str], radius: int = 30) -> Optional[Dict]:
        """Find nearest entity of specified types."""
        entities = self.get_nearby_entities(context, radius)
        
        # Sort by distance
        sorted_entities = sorted(entities, key=lambda e: e.get("distance", 999))
        
        for entity in sorted_entities:
            entity_type = entity.get("type", "").lower()
            if any(t.lower() in entity_type for t in entity_types):
                return entity
        return None

    def attack_nearest(
        self,
        context: ActionContext,
        entity_types: List[str],
        max_range: int = 10,
    ) -> bool:
        """Attack nearest entity of specified type."""
        entity = self.find_entity_by_type(context, entity_types, radius=max_range)
        
        if entity is None:
            return False
        
        entity_id = entity.get("id")
        if entity_id is None:
            return False
            
        # Equip weapon using InventoryAction
        # We assume best weapon is best for now
        # Creating a temporary logic to find weapon if not implemented in InventoryAction yet (it is)
        # But wait, InventoryAction doesn't have equip_best_weapon in my previous file creation?
        # Let me check what I wrote for inventory.py. 
        # I did not include equip_best_weapon in inventory.py! I missed it or chose not to put it.
        # Checking... ID 355/352/356...
        # Ah, I see `equip_best_armor` but looking at my thought/code for inventory.py...
        # I see `equip_best_armor` but NOT `equip_best_weapon`.
        # I need to add `equip_best_weapon` to `InventoryAction` or implement it here.
        # It belongs in InventoryAction or CombatAction? `combat.py` had `equip_best_weapon` imported from `inventory`.
        # So it should be in `InventoryAction`. I missed porting it.
        # I will implement it here for now or add it to InventoryAction later.
        # Actually I can just add `equip_best_weapon` to `InventoryAction` right now with a separate tool call to update it.
        # But for flow, I'll put a helper here or just rely on selecting generic sword.
        
        # Checking `inventory.py` content from previous turn... `equip_best_weapon` WAS in `inventory.py` lines 128-140.
        # My implementation of `InventoryAction` skipped it.
        # I should fix `InventoryAction` first? Or just add it here.
        # I'll likely need it for Combat so I should put it where it belongs.
        # I'll implement `equip_best_weapon` inside `CombatAction` as a private helper for now to avoid context switching too much,
        # or better, just use `select_item` on a list of weapons.
        
        weapons = ["minecraft:netherite_sword", "minecraft:diamond_sword", "minecraft:iron_sword", 
                   "minecraft:stone_sword", "minecraft:wooden_sword", "minecraft:diamond_axe", "minecraft:iron_axe"]
        
        for weapon in weapons:
            if self.inventory.select_item(context, weapon, allow_swap=True):
                break
        
        try:
            self._look_at_entity(context, entity)
            time.sleep(0.2)
            self.run_command(context, "attack_entity", {"entity_id": entity_id})
            return True
        except Exception as e:
            print(f"Attack error: {e}")
            return False

    def _look_at_entity(self, context: ActionContext, entity: Dict) -> bool:
        """
        Look at an entity by coordinates. The bridge's look_at handler only
        accepts x/y/z; sending entity_id causes a server-side NPE. Non-fatal
        on failure.
        """
        pos = entity_position(entity)
        if pos is None:
            return False
        x, y, z = pos
        try:
            self.run_command(context, "look_at", {"x": x, "y": y + 1.0, "z": z})
            return True
        except Exception as e:
            print(f"  look_at failed (non-fatal): {e}")
            return False

    def safe_combat(
        self,
        context: ActionContext,
        target_id: int,
        retreat_health: float = 6.0,
        max_duration: int = 30,
    ) -> bool:
        """Fight target with retreat logic."""
        # Equip weapon
        weapons = ["minecraft:netherite_sword", "minecraft:diamond_sword", "minecraft:iron_sword", 
                   "minecraft:stone_sword", "minecraft:wooden_sword"]
        for weapon in weapons:
            if self.inventory.select_item(context, weapon, allow_swap=True):
                break
        
        start = time.time()
        last_goto_time = 0
        
        while time.time() - start < max_duration:
            # Check health
            state = context.state.refresh()
            health = state.health
            
            if health < retreat_health:
                print(f"Retreating! Health: {health}")
                self.run_command(context, "cancel", {})
                return False
            
            # Check target
            entities = self.get_nearby_entities(context, radius=30)
            target = next((e for e in entities if e.get("id") == target_id), None)
            
            if target is None:
                return True
            
            dist = target.get("distance", 999)
            
            if dist < 4.5:
                self._look_at_entity(context, target)
                self.run_command(context, "attack_entity", {"entity_id": target_id})

                # Check for pathing (raw state)
                raw_state = self.run_command(context, "get_state", {})
                if raw_state.get("is_pathing", False):
                     self.run_command(context, "chat", {"message": "#stop"})
            else:
                # Move closer (raw state)
                raw_state = self.run_command(context, "get_state", {})
                is_pathing = raw_state.get("is_pathing", False)

                if not is_pathing or (time.time() - last_goto_time > 1.0):
                    tpos = entity_position(target)
                    if tpos is not None:
                        tx, ty, tz = int(tpos[0]), int(tpos[1]), int(tpos[2])
                        self.run_command(context, "goto", {"x": tx, "y": ty, "z": tz})
                    last_goto_time = time.time()
                
                if not is_pathing and time.time() - last_goto_time > 3.0:
                     self.run_command(context, "cancel", {})
                     return False

            time.sleep(0.2)
        
        return False

    def hunt_passive_mobs(
        self,
        context: ActionContext,
        target_mobs: Optional[List[str]] = None,
        target_count: int = 10,
        target_loot: Optional[Dict[str, int]] = None,
        timeout: int = 300,
    ) -> ActionResult:
        """
        Hunt passive mobs for food/loot.

        Callers use several shapes (see initial_gathering / resource_gathering):
            hunt_passive_mobs(context, target_count=10, timeout=300)
            hunt_passive_mobs(context, target_mobs=["sheep"], target_count=3, timeout=180)
            hunt_passive_mobs(context, target_mobs=["cow", "sheep"],
                              target_loot={"minecraft:leather": 4}, timeout=120)

        Delegates to common.combat.hunt_mobs (which handles exploration,
        healing, night abort, and loot accounting) and wraps its TaskResult
        into an ActionResult.
        """
        mob_types = target_mobs or ["pig", "cow", "sheep", "chicken"]
        required_loot = target_loot or {}

        result = hunt_mobs(
            context.client,
            mob_types=mob_types,
            required_loot=required_loot,
            search_radius=50,
            timeout=timeout,
            heal_threshold=5.0,
            # Only chase a kill count when no loot target was given - otherwise
            # loot satisfaction is the success condition.
            target_kills=target_count if not target_loot else None,
        )

        if result.success:
            return ActionResult.ok(result.reason, **result.data)
        return ActionResult.fail(result.reason, **result.data)

    def heal_if_needed(self, context: ActionContext, threshold: float = 10.0) -> bool:
        """Eat food if health below threshold."""
        state = context.state.refresh()
        if state.health >= threshold:
            return False
        
        food_items = [
            "minecraft:cooked_beef", "minecraft:cooked_porkchop", "minecraft:bread", 
            "minecraft:apple", "minecraft:cooked_chicken", "minecraft:cooked_mutton"
        ]
        
        inventory = self.inventory.get_inventory(context)
        # We need slot to eat. 
        # InventoryAction.find_item_slot would look up again.
        # Let's iterate found foods.
        
        for food in food_items:
            if inventory.get(food, 0) > 0:
                if self.inventory.select_item(context, food):
                     self.run_command(context, "use_item", {"duration_ms": 2000})
                     return True
        return False
