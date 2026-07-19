
import argparse
import sys
import os
import time
from typing import Dict, Callable

# Add src to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../src')))
sys.path.append(os.path.abspath(os.path.dirname(__file__)))

from baritone_client import Client, TcpTransport
from baritone_client.automator import ResourceManager, StateManager
from baritone_client.automator.phases.base_construction import BaseConstructionHandler
from baritone_client.automator.phases.iron_age import FoodAndIronHandler
from baritone_client.automator.phases.diamond_mining import DiamondMiningHandler
from baritone_client.common.base import sleep_through_night
from baritone_client.common.resources import gather_water
from baritone_client.common.navigation import goto
from baritone_client.common.combat import hunt_mobs
from baritone_client.common.inventory import equip_offhand


try:
    from tests.functional.utils import give_item, set_time, clear_inventory, gamemode
except ImportError:
    from utils import give_item, set_time, clear_inventory, gamemode

def create_client(host, port, timeout):
    transport = TcpTransport(host=host, port=port, timeout=timeout)
    return Client(transport)

def test_base_construction(client, setup=False):
    print(">>> TESTING BASE CONSTRUCTION")
    if setup:
        gamemode(client, "survival")
        clear_inventory(client)
        give_item(client, "minecraft:iron_pickaxe")
        give_item(client, "minecraft:iron_axe")
        give_item(client, "minecraft:iron_shovel")
        give_item(client, "minecraft:cooked_beef", 64)
        give_item(client, "minecraft:cobblestone", 64)
        give_item(client, "minecraft:oak_planks", 64)
        give_item(client, "minecraft:wheat_seeds", 16)
        give_item(client, "minecraft:iron_hoe", 1)
        give_item(client, "minecraft:dirt", 64)
        set_time(client, "day")

    handler = BaseConstructionHandler()
    resources = ResourceManager(client)
    state = StateManager()
    
    result = handler.execute(client, resources, state)
    print(f"Result: {result}")
    return result.success

def test_iron_age(client, setup=False):
    print(">>> TESTING IRON AGE")
    if setup:
        gamemode(client, "survival")
        clear_inventory(client)
        give_item(client, "minecraft:stone_pickaxe")
        give_item(client, "minecraft:cooked_beef", 64)
        give_item(client, "minecraft:oak_log", 32)
        give_item(client, "minecraft:coal", 32)
        give_item(client, "minecraft:crafting_table")
        give_item(client, "minecraft:furnace")
        set_time(client, "day")
        
    handler = FoodAndIronHandler()
    resources = ResourceManager(client)
    state = StateManager()
    
    result = handler.execute(client, resources, state)
    print(f"Result: {result}")
    return result.success

def test_diamond_mining(client, setup=False):
    print(">>> TESTING DIAMOND MINING")
    if setup:
        gamemode(client, "survival")
        clear_inventory(client)
        # Needs iron gear to mine diamonds
        give_item(client, "minecraft:iron_pickaxe")
        give_item(client, "minecraft:iron_sword")
        give_item(client, "minecraft:cooked_beef", 64)
        give_item(client, "minecraft:torch", 64)
        give_item(client, "minecraft:water_bucket")
        give_item(client, "minecraft:white_bed") # For sleep
        set_time(client, "day")
        
    handler = DiamondMiningHandler()
    resources = ResourceManager(client)
    state = StateManager()
    
    result = handler.execute(client, resources, state)
    print(f"Result: {result}")
    return result.success

def test_sleep(client, setup=False):
    print(">>> TESTING SLEEP LOGIC")
    if setup:
        gamemode(client, "survival")
        give_item(client, "minecraft:white_bed")
        give_item(client, "minecraft:dirt", 64) # For finding ground
        set_time(client, "13000") # Night
        
    print("Attempting to sleep...")
    result = sleep_through_night(client)
    print(f"Result: {result}")
    return result

def test_water(client, setup=False):
    print(">>> TESTING WATER COLLECTION")
    if setup:
        gamemode(client, "survival")
        clear_inventory(client)
        give_item(client, "minecraft:bucket")
        give_item(client, "minecraft:cooked_beef", 64)
        set_time(client, "day")
        
    print("Attempting to gather water...")
    result = gather_water(client)
    print(f"Result: {result}")
    return result

def test_combat(client, setup=False):
    print(">>> TESTING COMBAT (Hunt Sheep)")
    if setup:
        gamemode(client, "survival")
        give_item(client, "minecraft:iron_sword")
        set_time(client, "day")
        
    print("Hunting sheep for wool...")
    result = hunt_mobs(client, mob_types=["sheep"], required_loot={"minecraft:white_wool": 1}, timeout=60)
    print(f"Result: {result}")
    return result.success

def test_goto(client, setup=False):
    print(">>> TESTING NAVIGATION (Goto)")
    if setup:
        gamemode(client, "spectator") # Spectator flies faster for testing? No, pathing needs survival/creative.
        gamemode(client, "survival")
        
    # Get current pos, go 20 blocks X+
    state = client.transport.dispatch("get_state", {})
    pos = state.get("block_position", {})
    x, y, z = pos.get("x", 0), pos.get("y", 64), pos.get("z", 0)
    
    target_x = x + 20
    print(f"Going to {target_x} {y} {z}")
    success = goto(client, target_x, y, z)
    print(f"Result: {success}")
    return success




TESTS: Dict[str, Callable] = {
    "base": test_base_construction,
    "iron": test_iron_age,
    "diamond": test_diamond_mining,
    "sleep": test_sleep,
    "water": test_water,
    "combat": test_combat,
    "goto": test_goto,
}

def main():
    parser = argparse.ArgumentParser(description="Functional Test Runner")
    parser.add_argument("test_name", choices=TESTS.keys(), help="Test to run")
    parser.add_argument("--setup", action="store_true", help="Run setup (cheats)")
    parser.add_argument("--host", default="localhost", help="Bridge host")
    parser.add_argument("--port", type=int, default=5555, help="Bridge port")
    args = parser.parse_args()
    
    print(f"Running test: {args.test_name}")
    client = create_client(args.host, args.port, 15.0)
    
    try:
        # Ping
        client.transport.dispatch("get_state", {})
        print("Connected.")
        
        test_func = TESTS[args.test_name]
        success = test_func(client, setup=args.setup)
        
        if success:
            print("PASS")
        else:
            print("FAIL")
            sys.exit(1)
            
    except Exception as e:
        print(f"Test error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        client.shutdown()

if __name__ == "__main__":
    main()
