"""
Bridge Check Phase - Verifies Bridge API connectivity and functionality.
"""

from baritone_client.automator.phase_executor import PhaseHandler
from baritone_client.automator.state_manager import Phase
from baritone_client.common import resources

class BridgeCheckHandler(PhaseHandler):
    def get_name(self) -> str:
        return "Bridge API Check"

    def execute(self, client, resources, state):
        print(f"=== Starting Bridge API Check ===")
        
        # 1. Check Inventory (Already verified, but good for completeness)
        print("Checking Inventory API...")
        inv = client.transport.dispatch("get_inventory", {})
        if "error" in inv:
            print(f"Inventory Check FAILED: {inv.get('error')}")
            return False
        print(f"Inventory Check PASS: {len(inv.get('inventory', []))} slots read")

        # 2. Check Entities
        print("Checking Entity API...")
        entities = client.transport.dispatch("get_entities", {"radius": 32})
        if "error" in entities:
            print(f"Entity Check FAILED: {entities.get('error')}")
            return False
        ent_list = entities.get("entities", [])
        print(f"Entity Check PASS: Found {len(ent_list)} entities nearby")

        # 3. Check View (Surrounding blocks)
        print("Checking View API (World Inspection)...")
        view = client.transport.dispatch("get_view", {"radius": 2})
        if "error" in view:
            print(f"View Check FAILED: {view.get('error')}")
            return False
        voxels = view.get("voxels", [])
        print(f"View Check PASS: Retrieved {len(voxels)} voxel updates around player")

        # 4. Check Selection (Status only, don't execute unsafe changes)
        print("Checking Selection API...")
        sel = client.transport.dispatch("sel", {"action": "status"})
        # Note: status might return error if no selection exists, which is fine
        if "error" in sel and sel.get("error") != "No selection":
             print(f"Selection Check WARNING: {sel.get('error')}")
        else:
             print(f"Selection Check PASS: Bridge responded to selection status")

        print(f"=== Bridge Check Completer ===")
        return True
