"""
Crafting action implementation.
"""

import time
from typing import Dict, List, Optional, Any

from baritone_client.core.interfaces import ActionContext, ActionResult
from baritone_client.actions.base import BaseAction
from baritone_client.actions.inventory import InventoryAction
from baritone_client.actions.movement import MovementAction
from baritone_client.common.inventory import ensure_tool_sticks

# Import helper for safety check if available, or re-implement
try:
    from baritone_client.common.base import is_position_safe
except ImportError:
    is_position_safe = lambda c, x, y, z: True # Fallback

# Battle-tested primitives from the functional-test harness (placement with
# repositioning/retries, manual grid crafting that works without recipe
# listing). Optional: everything degrades to native logic when absent.
from baritone_client.common import harness_ops

_PLANK_WOODS = ["oak", "spruce", "birch", "dark_oak", "acacia", "jungle", "mangrove", "cherry"]


class CraftingAction(BaseAction):
    """Handles crafting and smelting operations."""

    def __init__(self):
        self.inventory = InventoryAction()
        self.movement = MovementAction()

    def execute(self, context: ActionContext) -> ActionResult:
        return ActionResult.fail("CraftingAction requires a specific method call")

    def _family_count(self, context: ActionContext, item_id: str) -> int:
        """
        Count an item for craft verification. Plank requests are counted
        across all wood types: the bridge crafts from whatever logs are in
        inventory (asking for oak_planks with birch logs yields birch_planks).
        """
        if item_id.split(":")[-1].endswith("_planks"):
            return sum(
                self.inventory.count_item(context, f"minecraft:{w}_planks")
                for w in _PLANK_WOODS
            )
        return self.inventory.count_item(context, item_id)

    def _wait_family_count(self, context: ActionContext, item_id: str, target: int, timeout: float = 6.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._family_count(context, item_id) >= target:
                return True
            time.sleep(0.5)
        return False

    def craft(self, context: ActionContext, item_id: str, count: int = 1) -> bool:
        """
        Craft an item, verified by inventory delta (a bare status=ok reply
        does not mean anything appeared - the 1.21.4 client has no recipe
        listing, so crafting silently no-ops in several situations).

        Fallback chain: bridge "craft" -> "auto_craft" -> manual grid clicks
        for tools (via the harness library, requires/opens a crafting table).
        """
        if not ensure_tool_sticks(context.client, item_id, count):
            print(f"  craft: could not prepare stick dependency for {item_id}")
            return False

        before = self._family_count(context, item_id)
        target = before + count

        try:
            self.run_command(context, "craft", {"item": item_id, "count": count})
        except Exception as e:
            print(f"  craft dispatch failed for {item_id}: {e}")
        if self._wait_family_count(context, item_id, target):
            return True

        try:
            self.run_command(context, "auto_craft", {"item": item_id, "quantity": count})
        except Exception as e:
            print(f"  auto_craft dispatch failed for {item_id}: {e}")
        if self._wait_family_count(context, item_id, target, timeout=8.0):
            return True

        manual = self._manual_fallback_for(item_id)
        if harness_ops.available() and manual is not None:
            print(f"  craft: falling back to manual grid crafting for {item_id}...")
            if item_id.split(":")[-1] == "crafting_table":
                self.run_command(context, "close_screen", {})
            else:
                state = self.run_command(context, "get_state", {})
                if not state.get("has_gui") and not self.ensure_crafting_table(context):
                    print("  craft: could not open a crafting table for manual crafting")
                    return False
            try:
                if manual(context.client):
                    return True
            except Exception as e:
                print(f"  manual grid craft failed for {item_id}: {e}")

        print(f"  craft: all methods failed for {item_id} (have {self._family_count(context, item_id)}, wanted {target})")
        return False

    @staticmethod
    def _manual_fallback_for(item_id: str):
        """Manual grid-click fallback for recipes the harness knows by hand."""
        name = item_id.split(":")[-1]
        if harness_ops.parse_tool_id(item_id):
            return lambda client: harness_ops.craft_tool_manual(client, item_id)
        if name.endswith("_bed"):
            return lambda client: harness_ops.craft_bed_manual(client, item_id)
        if name.endswith("_door"):
            return lambda client: harness_ops.craft_door_manual(client, item_id)
        if name == "chest":
            return lambda client: harness_ops.craft_chest_manual(client)
        if name == "furnace":
            return lambda client: harness_ops.craft_furnace_manual(client)
        if name == "crafting_table":
            return lambda client: harness_ops.craft_crafting_table_manual(client)
        return None

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

    def _open_crafting_table(self, context: ActionContext, x: int, y: int, z: int) -> bool:
        """Move within reach, interact, and verify that a GUI actually opened."""
        self.run_command(context, "goto", {"x": x + 1, "y": y, "z": z})

        deadline = time.time() + 30.0
        while time.time() < deadline:
            state = self.run_command(context, "get_state", {})
            pos = state.get("block_position", state.get("position", {}))
            distance_sq = (
                (float(pos.get("x", 0)) - (x + 1)) ** 2
                + (float(pos.get("y", 0)) - y) ** 2
                + (float(pos.get("z", 0)) - z) ** 2
            )
            if distance_sq <= 9.0:
                break
            time.sleep(0.25)

        self.run_command(context, "look_at", {"x": x + 0.5, "y": y + 0.5, "z": z + 0.5})
        time.sleep(0.1)
        self.run_command(context, "interact_block", {
            "x": x,
            "y": y,
            "z": z,
            "hand": "MAIN_HAND",
        })
        time.sleep(0.5)

        state = self.run_command(context, "get_state", {})
        return bool(state.get("has_gui"))

    def _find_and_open_existing_table(self, context: ActionContext) -> bool:
        """Voxel-scan for an already-placed crafting table and open it."""
        # Crafting tables only need to be within interaction range.  A radius-16
        # voxel scan checks more than 35,000 positions and can monopolize the
        # bridge long enough for otherwise healthy craft requests to time out.
        view_res = self.run_command(context, 'get_view', {'radius': 5})
        for v in view_res.get('voxels', []):
            if v.get('id') == 'minecraft:crafting_table':
                return self._open_crafting_table(context, v['x'], v['y'], v['z'])
        return False

    def ensure_crafting_table(self, context: ActionContext) -> bool:
        """Finds or places a crafting table and opens it (Robust)."""
        # 1. Try to find existing nearby
        if self._find_and_open_existing_table(context):
            return True

        # 2. Check/Craft crafting table item
        if self.inventory.count_item(context, "minecraft:crafting_table") == 0:
             # Check planks
             planks_count = 0
             plank_types = ["oak", "spruce", "birch", "dark_oak", "acacia", "jungle", "mangrove", "cherry"]
             for wood in plank_types:
                 planks_count += self.inventory.count_item(context, f"minecraft:{wood}_planks")
                 
             if planks_count < 4:
                 self.craft(context, "minecraft:oak_planks", 4 - planks_count)

             self.craft(context, "minecraft:crafting_table", 1)
             time.sleep(0.3)

        # 3. Preferred path: harness placement/open (handles repositioning,
        # retries across candidates, and verifies the GUI actually opened).
        if self._harness_ensure_table(context):
            return True

        # The harness may have placed the table but failed to open the GUI
        # (consuming the inventory item). Re-scan before the native path so
        # the freshly placed table gets found and opened rather than lost.
        if self._find_and_open_existing_table(context):
            return True

        # 4. Native fallback: scan for a spot, place, open.
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
        
        positions = []
        for radius in range(1, 5):
            for dy in (0, 1, -1):
                for dx, dz in (
                    (radius, 0), (-radius, 0), (0, radius), (0, -radius),
                    (radius, radius), (radius, -radius),
                    (-radius, radius), (-radius, -radius),
                ):
                    positions.append((x + dx, y + dy, z + dz))
        
        placed_pos = None
        for px, py, pz in positions:
            below = self.run_command(
                context, 'get_block', {'x': px, 'y': py - 1, 'z': pz}
            ).get('id', '')
            if not below or any(s in below for s in ('air', 'water', 'lava')):
                continue

            check = self.run_command(context, 'get_block', {'x': px, 'y': py, 'z': pz})
            bid = check.get('id', '')
            replaceable = any(s in bid for s in ('air', 'grass', 'fern', 'leaf', 'flower', 'snow'))

            if not replaceable:
                continue

            try:
                self.run_command(context, 'place_block', {
                    'x': px,
                    'y': py,
                    'z': pz,
                    'block': 'minecraft:crafting_table',
                })
                time.sleep(0.5)
            except Exception as exc:
                print(f"  Could not place crafting table at {(px, py, pz)}: {exc}")
                continue

            check = self.run_command(context, 'get_block', {'x': px, 'y': py, 'z': pz})
            if 'crafting_table' in check.get('id', ''):
                placed_pos = (px, py, pz)
                break

        if not placed_pos:
            # No naturally replaceable spot (e.g. bottom of a mined 1x1 shaft
            # where every neighbor is solid stone). Carve out an adjacent
            # block so the table has somewhere to go.
            placed_pos = self._carve_and_place_table(context, x, y, z)

        if not placed_pos:
            print("  ensure_crafting_table: no placeable spot found (even after carving)")
            return False

        # Open
        return self._open_crafting_table(context, *placed_pos)

    def _harness_ensure_table(self, context: ActionContext) -> bool:
        """Robust find/place/open via the functional-harness library."""
        if not harness_ops.available():
            return False
        try:
            return harness_ops.ensure_crafting_table_open(context.client)
        except Exception as e:
            print(f"  harness ensure_crafting_table_open failed: {e}")
            return False

    def _carve_and_place_table(self, context: ActionContext, x: int, y: int, z: int):
        """Break a solid horizontal neighbor to make room, then place the table there."""
        for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            px, py, pz = x + dx, y, z + dz
            bid = self.run_command(context, 'get_block', {'x': px, 'y': py, 'z': pz}).get('id', '')
            if not bid or any(s in bid for s in ('water', 'lava', 'bedrock', 'chest', 'air')):
                continue
            below = self.run_command(context, 'get_block', {'x': px, 'y': py - 1, 'z': pz}).get('id', '')
            if not below or any(s in below for s in ('air', 'water', 'lava')):
                continue

            print(f"  Carving placement spot at {(px, py, pz)} (was {bid})...")
            try:
                self.run_command(context, 'break_block', {'x': px, 'y': py, 'z': pz})
            except Exception as exc:
                print(f"  break_block failed at {(px, py, pz)}: {exc}")
                continue

            # Wait for the block to actually break (stone without a pickaxe
            # can take ~8s to mine)
            deadline = time.time() + 15.0
            while time.time() < deadline:
                check = self.run_command(context, 'get_block', {'x': px, 'y': py, 'z': pz}).get('id', '')
                if 'air' in check:
                    break
                time.sleep(0.5)
            else:
                continue

            # break_block runs through Baritone's builder process - stop it so
            # it doesn't fight the placement below.
            try:
                self.run_command(context, 'cancel', {})
            except Exception:
                pass
            time.sleep(0.3)

            # Breaking dropped the mined item; reselect the table before placing.
            if not self.inventory.select_item(context, 'minecraft:crafting_table'):
                continue
            time.sleep(0.3)
            try:
                self.run_command(
                    context,
                    'place_block',
                    {'x': px, 'y': py, 'z': pz, 'block': 'minecraft:crafting_table'}
                )
                time.sleep(0.5)
            except Exception as exc:
                print(f"  Could not place crafting table at {(px, py, pz)}: {exc}")
                continue

            check = self.run_command(context, 'get_block', {'x': px, 'y': py, 'z': pz})
            if 'crafting_table' in check.get('id', ''):
                return (px, py, pz)
        return None

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
