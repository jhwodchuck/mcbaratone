"""
Shared constants for functional tests.
"""

# All overworld logs (including bamboo block if you want it treated like a "wood")
LOG_BLOCK_IDS = [
    "minecraft:oak_log",
    "minecraft:spruce_log",
    "minecraft:birch_log",
    "minecraft:jungle_log",
    "minecraft:acacia_log",
    "minecraft:dark_oak_log",
    # "minecraft:mangrove_log", # Disabled due to Baritone crash
    # "minecraft:cherry_log",   # Disabled due to Baritone crash
    # Optional:
    # "minecraft:bamboo_block",
]

# Stripped logs (often end up in inventory depending on mods/tools/automation)
STRIPPED_LOG_BLOCK_IDS = [
    "minecraft:stripped_oak_log",
    "minecraft:stripped_spruce_log",
    "minecraft:stripped_birch_log",
    "minecraft:stripped_jungle_log",
    "minecraft:stripped_acacia_log",
    "minecraft:stripped_dark_oak_log",
    "minecraft:stripped_mangrove_log",
    "minecraft:stripped_cherry_log",
]

# "Wood" blocks (bark on all sides) sometimes appear via crafting or harvesting setups
WOOD_BLOCK_IDS = [
    "minecraft:oak_wood",
    "minecraft:spruce_wood",
    "minecraft:birch_wood",
    "minecraft:jungle_wood",
    "minecraft:acacia_wood",
    "minecraft:dark_oak_wood",
    "minecraft:mangrove_wood",
    "minecraft:cherry_wood",
]

STRIPPED_WOOD_BLOCK_IDS = [
    "minecraft:stripped_oak_wood",
    "minecraft:stripped_spruce_wood",
    "minecraft:stripped_birch_wood",
    "minecraft:stripped_jungle_wood",
    "minecraft:stripped_acacia_wood",
    "minecraft:stripped_dark_oak_wood",
    "minecraft:stripped_mangrove_wood",
    "minecraft:stripped_cherry_wood",
]

# All planks
PLANK_ITEM_IDS = [
    "minecraft:oak_planks",
    "minecraft:spruce_planks",
    "minecraft:birch_planks",
    "minecraft:jungle_planks",
    "minecraft:acacia_planks",
    "minecraft:dark_oak_planks",
    "minecraft:mangrove_planks",
    "minecraft:cherry_planks",
]

# If you ever decide to do replanting later
SAPLING_ITEM_IDS = [
    "minecraft:oak_sapling",
    "minecraft:spruce_sapling",
    "minecraft:birch_sapling",
    "minecraft:jungle_sapling",
    "minecraft:acacia_sapling",
    "minecraft:dark_oak_sapling",
    "minecraft:mangrove_propagule",
    "minecraft:cherry_sapling",
]

# Tools to never deposit
TOOL_ITEM_SUBSTRINGS = [
    "_axe", "_pickaxe", "_shovel", "_hoe", "_sword",
    "crafting_table",  # you already exclude this
]

# Foods to never deposit (expand as you like)
FOOD_ITEM_SUBSTRINGS = [
    "apple", "bread", "beef", "porkchop", "mutton", "chicken",
    "potato", "carrot", "beetroot", "cookie", "melon_slice",
    "sweet_berries", "glow_berries", "pumpkin_pie",
]

# Pre-calculated mine argument for logs
LOG_MINE_ARG = " ".join([bid.replace("minecraft:", "") for bid in LOG_BLOCK_IDS])
