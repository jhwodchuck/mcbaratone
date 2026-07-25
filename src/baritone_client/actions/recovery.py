"""
Recovery actions for death and item retrieval.
"""

import time
from typing import Optional
from ..core.interfaces import IAction, ActionContext, ActionResult
from ..common.nether import find_nearest_portal
from ..common import goto
from ..automator.state_manager import Phase



class InventoryRecoveryStrategy:
    """Strategy for recovering items from death location."""
    
    def recover_inventory(self, context: ActionContext, death_coords: tuple) -> bool:
        """Recover items at death location."""
        success = goto(context.client, *death_coords, timeout=600)
        if success:
            print("Recovered items from death location")
            time.sleep(2.0)
            return True
        else:
            print("Failed to reach death location")
            return False


class DimensionRecoveryStrategy:
    """Strategy for handling cross-dimension recovery."""
    
    def recover_cross_dimension(self, context: ActionContext, death_coords: tuple, death_dim: str) -> bool:
        """Handle recovery when death occurred in different dimension."""
        if "nether" in death_dim:
            return self._recover_from_nether(context, death_coords)
        return False
    
    def _recover_from_nether(self, context: ActionContext, death_coords: tuple) -> bool:
        """Recover from Nether death."""
        # Recover items in Nether
        success = goto(context.client, *death_coords, timeout=600)
        if success:
            print("Recovered items from Nether death location")
            time.sleep(2.0)
            
            # Find portal and return to Overworld
            portal_coords = find_nearest_portal(context.client, "nether")
            if portal_coords:
                print(f"Found Nether portal at {portal_coords}")
                success = goto(context.client, *portal_coords, timeout=300)
                if success:
                    from ..common import enter_nether_portal
                    if enter_nether_portal(
                        context.client,
                        timeout=60,
                        target_dimension="minecraft:overworld",
                        portal=portal_coords,
                    ):
                        print("Returned to Overworld via portal")
                        return True
                    else:
                        print("Failed to enter portal back to Overworld")
                else:
                    print("Failed to reach Nether portal")
            else:
                print("Could not find Nether portal for return trip")
        
        return False


