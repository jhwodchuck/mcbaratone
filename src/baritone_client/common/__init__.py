"""
Common utilities for Minecraft automation.
"""

from .state import WorldState, PlayerState
from .navigation import (
    goto,
    explore_until,
    spiral_explore,
    return_to_base,
    find_nearby_block,
    goto_block,
    safe_return,
)
from .resources import gather_wood, gather_stone, gather_ores, go_to_y_level, ensure_supplies
from .inventory import (
    has_items,
    count_item,
    find_item_slot,
    equip_best_armor,
    craft,
    get_inventory,
    craft,
    get_inventory,
    select_item,
    equip_best_weapon,
)
from .combat import attack_nearest, safe_combat, heal_if_needed, hunt_passive_mobs
from .tasks import Task, TaskResult, SequentialTask, RetryTask, ActionTask
from .nether import (
    build_nether_portal,
    enter_portal,
    enter_nether_portal,
    ignite_portal,
    verify_portal,
    find_nearest_portal,
    find_nether_fortress,
    hunt_blazes,
    hunt_endermen,
    craft_eyes_of_ender,
    mine_nether_gold,
    barter_with_piglins,

)
from .end import (
    triangulate_stronghold,
    spiral_stronghold_search,
    find_end_portal,
    activate_end_portal,
    enter_end_portal,
    fight_ender_dragon,
)
from .base import (
    find_flat_ground,
    place_crafting_table,
    place_furnace,
    place_chest,
    place_bed,
    setup_base,
    open_crafting_table,
    open_crafting_table,
    open_furnace,
    sleep_through_night,
)
from .automation_utils import get_player_pos, place_block, safe_goto
from .villager import (
    capture_villager,
    build_villager_breeder,
    lock_librarian,
    start_villager_multiplication,
    find_villager_workstation,
)
from .mob_farm import (
    find_spawner,
    build_simple_mob_farm,
    grind_xp_at_location,
    enchant_tool_perfectly,
)
from .iron_farm import (
    build_iron_farm,
    move_villagers_to_farm,
    add_zombie_to_farm,
    start_iron_production,
)

LambdaTask = ActionTask

__all__ = [
    # Automation Utils
    "get_player_pos",
    "place_block",
    "safe_goto",
    "sleep_through_night",
    # State
    "WorldState",
    "PlayerState",
    # Navigation
    "goto",
    "explore_until",
    "spiral_explore",
    "return_to_base",
    "safe_return",
    "find_nearby_block",
    "goto_block",
    # Resources
    "gather_wood",
    "gather_stone",
    "gather_ores",
    "go_to_y_level",
    "ensure_supplies",
    # Inventory
    "has_items",
    "count_item",
    "find_item_slot",
    "equip_best_armor",
    "craft",
    "get_inventory",
    "get_inventory",
    "select_item",
    "equip_best_weapon",
    # Combat
    "attack_nearest",
    "safe_combat",
    "heal_if_needed",
    "hunt_passive_mobs",
    # Tasks
    "Task",
    "TaskResult",
    "SequentialTask",
    "RetryTask",
    "ActionTask",
    "LambdaTask",
    # Nether
    "build_nether_portal",
    "enter_portal",
    "enter_nether_portal",
    "ignite_portal",
    "verify_portal",
    "find_nearest_portal",
    "find_nether_fortress",
    "hunt_blazes",
    "hunt_endermen",
    "craft_eyes_of_ender",
    "mine_nether_gold",
    "barter_with_piglins",

    # End
    "triangulate_stronghold",
    "spiral_stronghold_search",
    "find_end_portal",
    "activate_end_portal",
    "enter_end_portal",
    "fight_ender_dragon",
    # Villager
    "capture_villager",
    "build_villager_breeder",
    "lock_librarian",
    "start_villager_multiplication",
    "find_villager_workstation",
    # Mob Farm
    "find_spawner",
    "build_simple_mob_farm",
    "grind_xp_at_location",
    "enchant_tool_perfectly",
    # Iron Farm
    "build_iron_farm",
    "move_villagers_to_farm",
    "add_zombie_to_farm",
    "start_iron_production",
]
