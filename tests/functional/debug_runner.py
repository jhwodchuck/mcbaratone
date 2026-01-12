import sys
import os
import traceback

# Mimic run_tests.py path setup
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'src'))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')) # tests
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..')) # Root

print("Starting Import Debug...")
try:
    from tests.functional.shared.inventory_ops import (
        fill_supply_chests,
        open_supply_chest as _open_supply_chest,
        build_slot_map_from_screen as _build_slot_map_from_screen,
        refresh_supply_slot_map as _refresh_supply_slot_map,
        deposit_inventory_to_supply_chest as _deposit_inventory_to_supply_chest,
        withdraw_from_supply_chest as _withdraw_from_supply_chest,
        ensure_item_from_supply as _ensure_item_from_supply,
        smelt_in_furnace as _smelt_in_furnace,
        craft_bed_manual as _craft_bed_manual,
        ensure_crafting_table_open as _ensure_crafting_table_open,
        get_workshop_furnace,
        get_inv_slots as _get_inv_slots,
    )
    import extended_suite_1000
    print("Import Successful")
except Exception as e:
    print(f"ERROR TYPE: {type(e)}")
    print(f"ERROR MSG: {e}")
    # traceback.print_exc() # Disable full trace to avoid truncation mess
try:
    print("Probing dependencies...")
    import baritone_client.common.inventory
    print(" - baritone_client.common.inventory OK")
    import tests.utils.mc_harness
    print(" - tests.utils.mc_harness OK")
    import tests.functional.shared.block_ops
    print(" - tests.functional.shared.block_ops OK")
except Exception as e:
    print(f"ERROR TYPE: {type(e)}")
    print(f"ERROR MSG: {e}")
    # traceback.print_exc() # Disable full trace to avoid truncation mess
