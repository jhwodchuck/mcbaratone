"""
Death recovery action implementation.
"""

import time
from typing import Optional
from ..core.interfaces import ActionContext, ActionResult
from ..actions.base import BaseAction
from ..common.nether import find_nearest_portal
from ..common import goto


class DeathRecoveryAction(BaseAction):
    """
    Action to handle player death and recovery.

    Extracts death detection and recovery logic into a modular action.
    """

    def execute(self, context: ActionContext) -> ActionResult:
        """
        Execute death recovery sequence.

        Returns True if recovery was needed and performed.
        """
        try:
            # Check for death
            state = context.client.transport.dispatch("get_state", {})
            if not state.get("is_dead", False) and state.get("health", 20) > 0:
                return ActionResult.ok("No death detected")

            print("\n!!! PLAYER DIED !!!")
            print("Starting recovery sequence...")

            # Respawn
            context.client.transport.dispatch("respawn", {})
            time.sleep(2.0)

            # Get death location and recover items
            response = context.client.transport.dispatch("get_death_location", {})
            if response.get("status") == "ok":
                data = response.get("data", {})
                death_x, death_y, death_z = data.get("x"), data.get("y"), data.get("z")
                death_dim = data.get("dimension", "").lower()

                if death_x is not None:
                    print(f"Death location: ({death_x}, {death_y}, {death_z}) in {death_dim}")

                    # Dimension-aware recovery logic
                    from ..automator.state_manager import Phase
                    current_phase = context.state.get_current_phase()

                    if "nether" in death_dim:
                        # Died in Nether - decide whether to recover in Nether or return to Overworld
                        nether_phases = [Phase.NETHER_TRAVEL, Phase.ENDER_PEARL_FARM]

                        if current_phase in nether_phases:
                            # Can continue in Nether - recover items here
                            print("Recovering items in Nether...")
                            success = goto(context.client, int(death_x), int(death_y), int(death_z), timeout=600)
                            if success:
                                print("Recovered items from Nether death location")
                                time.sleep(2.0)
                                return ActionResult.ok("Recovery completed in Nether", recovered_in_nether=True)
                            else:
                                print("Failed to reach Nether death location")
                                return ActionResult.fail("Failed to reach Nether death location")
                        else:
                            # Need to return to Overworld - recover items in Nether first, then traverse
                            print("Recovering items in Nether before returning to Overworld...")
                            success = goto(context.client, int(death_x), int(death_y), int(death_z), timeout=600)
                            if success:
                                print("Recovered items from Nether death location")
                                time.sleep(2.0)

                            # Now find portal and return to Overworld
                            portal_coords = find_nearest_portal(context.client, "nether")
                            if portal_coords:
                                print(f"Found Nether portal at {portal_coords}")
                                success = goto(context.client, portal_coords[0], portal_coords[1], portal_coords[2], timeout=300)
                                if success:
                                    # Enter portal to return to Overworld
                                    from ..common import enter_nether_portal
                                    if enter_nether_portal(context.client, timeout=60):
                                        print("Returned to Overworld via portal")
                                        # Reset to bootstrap since we're back at spawn area
                                        context.state.set_phase(Phase.BOOT_SEQUENCE)
                                        return ActionResult.ok("Reset to bootstrap phase after Nether recovery", reset_phase=True)
                                    else:
                                        print("Failed to enter portal back to Overworld")
                                        return ActionResult.fail("Failed to enter portal back to Overworld")
                                else:
                                    print("Failed to reach Nether portal")
                                    return ActionResult.fail("Failed to reach Nether portal")
                            else:
                                print("Could not find Nether portal for return trip")
                                return ActionResult.fail("Could not find Nether portal")

                    else:
                        # Died in Overworld - standard recovery
                        success = goto(context.client, int(death_x), int(death_y), int(death_z), timeout=600)
                        if success:
                            print("Recovered items from death location")
                            time.sleep(2.0)
                            # Reset to bootstrap phase for fresh start
                            context.state.set_phase(Phase.BOOT_SEQUENCE)
                            return ActionResult.ok("Recovery completed in Overworld", reset_phase=True)
                        else:
                            print("Failed to reach death location")
                            return ActionResult.fail("Failed to reach death location")

            # Fallback: always reset to bootstrap if death location unknown
            context.state.set_phase(Phase.BOOT_SEQUENCE)
            print("Reset to BOOT_SEQUENCE phase (death location unknown)")
            return ActionResult.ok("Reset to bootstrap phase (death location unknown)", reset_phase=True)

        except Exception as e:
            print(f"Warning: Failed to handle death recovery: {e}")
            return ActionResult.fail(f"Death recovery failed: {e}")