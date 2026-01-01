"""
Resource gathering utilities - Wood, stone, ores, and materials.
"""

import time
from typing import Any, Callable, Dict, Optional
from .inventory import count_item, craft
from .tasks import TaskResult

# Log block types (full IDs)
LOG_BLOCKS = [
    "minecraft:oak_log",
    "minecraft:birch_log",
    "minecraft:spruce_log",
    "minecraft:dark_oak_log",
    "minecraft:acacia_log",
    "minecraft:jungle_log",
    "minecraft:mangrove_log",
    "minecraft:cherry_log",
]

# Stone block types
STONE_BLOCKS = [
    "minecraft:stone",
    "minecraft:cobblestone",
    "minecraft:deepslate",
    "minecraft:cobbled_deepslate",
]

# Ore types by tier
ORES = {
    "coal": ["minecraft:coal_ore", "minecraft:deepslate_coal_ore"],
    "iron": ["minecraft:iron_ore", "minecraft:deepslate_iron_ore"],
    "gold": ["minecraft:gold_ore", "minecraft:deepslate_gold_ore", "minecraft:nether_gold_ore"],
    "diamond": ["minecraft:diamond_ore", "minecraft:deepslate_diamond_ore"],
    "copper": ["minecraft:copper_ore", "minecraft:deepslate_copper_ore"],
    "lapis": ["minecraft:lapis_ore", "minecraft:deepslate_lapis_ore"],
    "redstone": ["minecraft:redstone_ore", "minecraft:deepslate_redstone_ore"],
    "emerald": ["minecraft:emerald_ore", "minecraft:deepslate_emerald_ore"],
}


def gather_wood(client, count: int = 16, timeout: int = 180) -> bool:
    """Gather wood logs until count reached."""
    try:
        client.transport.dispatch("mine", {"blocks": LOG_BLOCKS, "quantity": count + 4})
        start = time.time()
        while time.time() - start < timeout:
            total = sum(count_item(client, block) for block in LOG_BLOCKS)
            if total >= count:
                client.transport.dispatch("cancel", {})
                return True
            time.sleep(3)
        client.transport.dispatch("cancel", {})
        return False
    except Exception as exc:
        print(f"Wood gathering error: {exc}")
        return False


def gather_stone(client, count: int = 16, timeout: int = 180) -> bool:
    """Gather cobblestone until count reached."""
    try:
        client.transport.dispatch("mine", {"blocks": STONE_BLOCKS, "quantity": count + 4})
        start = time.time()
        while time.time() - start < timeout:
            total = count_item(client, "minecraft:cobblestone") + count_item(client, "minecraft:cobbled_deepslate")
            if total >= count:
                client.transport.dispatch("cancel", {})
                return True
            time.sleep(3)
        client.transport.dispatch("cancel", {})
        return False
    except Exception as exc:
        print(f"Stone gathering error: {exc}")
        return False


def gather_ores(client, ore_type: str, count: int, timeout: int = 600) -> bool:
    """Gather specific ore type."""
    if ore_type not in ORES:
        print(f"Unknown ore type: {ore_type}")
        return False
    drop_items = {
        "coal": "minecraft:coal",
        "iron": "minecraft:raw_iron",
        "gold": "minecraft:raw_gold",
        "diamond": "minecraft:diamond",
        "copper": "minecraft:raw_copper",
        "lapis": "minecraft:lapis_lazuli",
        "redstone": "minecraft:redstone",
        "emerald": "minecraft:emerald",
    }
    drop_item = drop_items.get(ore_type, f"minecraft:raw_{ore_type}")
    try:
        client.transport.dispatch("mine", {"blocks": ORES[ore_type], "quantity": count + 2})
        start = time.time()
        while time.time() - start < timeout:
            total = count_item(client, drop_item)
            if total >= count:
                client.transport.dispatch("cancel", {})
                return True
            time.sleep(5)
        client.transport.dispatch("cancel", {})
        return False
    except Exception as exc:
        print(f"Ore gathering error: {exc}")
        return False


def go_to_y_level(client, y: int, timeout: int = 120) -> bool:
    """Navigate to specific Y level for mining."""
    try:
        state = client.transport.dispatch("get_state", {})
        position = state.get("block_position", state.get("position", {}))
        x = int(position.get("x", state.get("x", 0)))
        z = int(position.get("z", state.get("z", 0)))
        client.transport.dispatch("goto", {"x": x, "y": y, "z": z})
        start = time.time()
        while time.time() - start < timeout:
            state = client.transport.dispatch("get_state", {})
            position = state.get("block_position", state.get("position", {}))
            current_y = position.get("y", state.get("y", 0))
            if abs(current_y - y) < 5:
                client.transport.dispatch("cancel", {})
                return True
            time.sleep(2)
        client.transport.dispatch("cancel", {})
        return False
    except Exception as exc:
        print(f"Y level navigation error: {exc}")
        return False


def _default_mine(client, block_id: str, quantity: int) -> bool:
    try:
        client.transport.dispatch("mine", {"blocks": [block_id], "quantity": max(1, quantity)})
        return True
    except Exception as exc:
        print(f"Mine command failed for {block_id}: {exc}")
        return False


DEFAULT_REQUIREMENT_STRATEGIES: Dict[str, Callable[[Any, int], bool]] = {
    "minecraft:oak_log": lambda client, qty: gather_wood(client, count=max(qty, 16)),
    "minecraft:cobblestone": lambda client, qty: gather_stone(client, count=max(qty, 16)),
    "minecraft:iron_ingot": lambda client, qty: gather_ores(client, "iron", count=max(qty * 2, qty + 4)),
    "minecraft:diamond": lambda client, qty: gather_ores(client, "diamond", count=max(qty, 4)),
    "minecraft:gold_ingot": lambda client, qty: gather_ores(client, "gold", count=max(qty, 8)),
    "minecraft:obsidian": lambda client, qty: _default_mine(client, "minecraft:obsidian", qty),
    "minecraft:crafting_table": lambda client, qty: craft(client, "minecraft:crafting_table", qty) or True,
    "minecraft:furnace": lambda client, qty: craft(client, "minecraft:furnace", qty) or True,
    "minecraft:chest": lambda client, qty: craft(client, "minecraft:chest", qty) or True,
    "minecraft:stone_pickaxe": lambda client, qty: craft(client, "minecraft:stone_pickaxe", qty) or True,
    "minecraft:stone_sword": lambda client, qty: craft(client, "minecraft:stone_sword", qty) or True,
    "minecraft:iron_pickaxe": lambda client, qty: craft(client, "minecraft:iron_pickaxe", qty) or True,
    "minecraft:iron_sword": lambda client, qty: craft(client, "minecraft:iron_sword", qty) or True,
    "minecraft:diamond_pickaxe": lambda client, qty: craft(client, "minecraft:diamond_pickaxe", qty) or True,
    "minecraft:diamond_sword": lambda client, qty: craft(client, "minecraft:diamond_sword", qty) or True,
    "minecraft:bow": lambda client, qty: craft(client, "minecraft:bow", qty) or True,
    "minecraft:arrow": lambda client, qty: craft(client, "minecraft:arrow", max(qty, 32)) or True,
}


def _missing_requirements(client, requirements: Dict[str, int]) -> Dict[str, int]:
    missing: Dict[str, int] = {}
    for item_id, required in requirements.items():
        current = count_item(client, item_id)
        if current < required:
            missing[item_id] = required - current
    return missing


def ensure_supplies(
    client,
    requirements: Dict[str, int],
    strategies: Optional[Dict[str, Callable[[Any, int], bool]]] = None,
    poll_interval: float = 5.0,
    timeout: int = 600,
) -> TaskResult:
    """
    Ensure the inventory matches the provided requirement list.

    Args:
        client: Baritone client
        requirements: Mapping of item_id -> minimum amount
        strategies: Optional overrides for how to acquire each item
        poll_interval: Seconds to wait between refreshes
        timeout: Maximum duration before giving up
    """
    strategies_map = dict(DEFAULT_REQUIREMENT_STRATEGIES)
    if strategies:
        strategies_map.update(strategies)

    start = time.time()
    attempts = 0
    operations = []

    missing = _missing_requirements(client, requirements)
    if not missing:
        return TaskResult.ok("All requirements already satisfied", operations=operations)

    while missing and time.time() - start < timeout:
        print(f"Ensuring supplies: {missing} (t={time.time()-start:.0f}s/{timeout}s)")
        for item_id, shortfall in list(missing.items()):
            handler = strategies_map.get(item_id)
            if handler is None:
                print(f"  No handler for {item_id}, skipping")
                continue
            attempts += 1
            print(f"  Gathering {item_id} x{shortfall}...")
            handler(client, shortfall)
            operations.append({"item": item_id, "shortfall": shortfall})
        time.sleep(poll_interval)
        missing = _missing_requirements(client, requirements)

    if missing:
        return TaskResult.fail(
            "Missing required supplies",
            missing=missing,
            operations=operations,
            attempts=attempts,
            timeout=timeout,
        )

    return TaskResult.ok(
        "Requirements satisfied",
        operations=operations,
        attempts=attempts,
        duration=time.time() - start,
    )
