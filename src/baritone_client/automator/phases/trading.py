"""Phase 8: librarian rolling, purchasing, and verified tool perfection."""

from __future__ import annotations

import time
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Set, Tuple

from ..phase_executor import PhaseHandler
from ..resource_manager import ResourceManager
from ..state_manager import Phase, StateManager
from ...common import TaskResult
from ...common.base import robust_place
from ...common.combat import entity_position
from ...common.inventory import count_item, craft
from ...common.iron_farm import get_nearby_entities
from ...common.navigation import goto


REQUIRED_ENCHANTMENTS = {"mending", "efficiency", "unbreaking", "fortune"}
TOOL_IDS = {"minecraft:diamond_pickaxe", "minecraft:netherite_pickaxe"}
AIR = {"", "minecraft:air", "minecraft:cave_air", "minecraft:void_air"}


def _dispatch_payload(response: Any) -> Dict[str, Any]:
    if not isinstance(response, dict):
        return {}
    nested = response.get("data")
    return nested if isinstance(nested, dict) else response


def _inventory_items(payload: Mapping[str, Any]) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for section in ("inventory", "armor", "offhand"):
        values = payload.get(section, [])
        if isinstance(values, list):
            items.extend(dict(value) for value in values if isinstance(value, Mapping))
    return items


def _enchantment_names(value: Any) -> Set[str]:
    names: Set[str] = set()
    if isinstance(value, str):
        names.add(value.split(":")[-1].lower())
    elif isinstance(value, Mapping):
        for key, nested in value.items():
            key_name = str(key).split(":")[-1].lower()
            if key_name in REQUIRED_ENCHANTMENTS:
                names.add(key_name)
            names.update(_enchantment_names(nested))
    elif isinstance(value, list):
        for nested in value:
            names.update(_enchantment_names(nested))
    return names & REQUIRED_ENCHANTMENTS


def _item_enchantments(item: Mapping[str, Any]) -> Set[str]:
    result: Set[str] = set()
    for field in ("stored_enchantments", "enchantments", "components"):
        result.update(_enchantment_names(item.get(field)))
    return result


def _verified_inventory_enchantments(
    items: Iterable[Mapping[str, Any]],
    *,
    item_ids: Set[str],
) -> Set[str]:
    verified: Set[str] = set()
    for item in items:
        if str(item.get("id", "")) in item_ids:
            verified.update(_item_enchantments(item))
    return verified


def _best_item_enchantments(
    items: Iterable[Mapping[str, Any]],
    *,
    item_ids: Set[str],
) -> Set[str]:
    """Return enchantments from one item, never a union across several tools."""
    candidates = [
        _item_enchantments(item)
        for item in items
        if str(item.get("id", "")) in item_ids
    ]
    return max(candidates, key=len, default=set())


def _villager_candidates(entities: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    return [
        dict(entity)
        for entity in entities
        if entity.get("type") == "minecraft:villager"
        and not bool(entity.get("is_baby", False))
        and str(entity.get("profession", "minecraft:none")) in {
            "minecraft:none",
            "minecraft:librarian",
        }
    ]


def _block_id(client: Any, position: Sequence[int]) -> str:
    response = _dispatch_payload(
        client.transport.dispatch(
            "get_block",
            {"x": int(position[0]), "y": int(position[1]), "z": int(position[2])},
        )
    )
    return str(response.get("id") or response.get("block") or "")


def _close_screen(client: Any) -> None:
    try:
        client.transport.dispatch("close_screen", {})
    except Exception:
        pass


def _open_merchant(client: Any, villager: Mapping[str, Any]) -> Dict[str, Any]:
    target_id = villager.get("id")
    position = entity_position(villager)
    if not isinstance(target_id, int) or position is None:
        return {}
    if not goto(client, *position, timeout=120, tolerance=4.0):
        return {}
    interaction = _dispatch_payload(
        client.transport.dispatch(
            "entity_interact",
            {"action": "interact", "entity_id": target_id, "max_distance": 6.0},
        )
    )
    if not (interaction.get("accepted") or interaction.get("success")):
        return {}
    screen = _dispatch_payload(client.transport.dispatch("get_screen", {}))
    if "merchant" not in str(screen.get("type", "")).lower():
        _close_screen(client)
        return {}
    return screen


def _offer_for_enchantment(
    screen: Mapping[str, Any],
    enchantment: str,
) -> Dict[str, Any] | None:
    offers = screen.get("merchant_offers", [])
    if not isinstance(offers, list):
        return None
    for offer in offers:
        if not isinstance(offer, Mapping) or bool(offer.get("disabled", False)):
            continue
        result = offer.get("result")
        if not isinstance(result, Mapping) or result.get("id") != "minecraft:enchanted_book":
            continue
        if enchantment in _item_enchantments(result):
            return dict(offer)
    return None


def _lectern_position(villager: Mapping[str, Any]) -> Tuple[int, int, int] | None:
    position = entity_position(villager)
    if position is None:
        return None
    return int(round(position[0])) + 1, int(round(position[1])), int(round(position[2]))


def _place_lectern(client: Any, position: Tuple[int, int, int]) -> bool:
    if _block_id(client, position) == "minecraft:lectern":
        return True
    if _block_id(client, position) not in AIR:
        return False
    if count_item(client, "minecraft:lectern") < 1 and not craft(client, "minecraft:lectern", 1):
        return False
    return robust_place(client, *position, "minecraft:lectern") and _block_id(
        client, position
    ) == "minecraft:lectern"


def _break_lectern(client: Any, position: Tuple[int, int, int]) -> bool:
    if _block_id(client, position) in AIR:
        return True
    response = _dispatch_payload(
        client.transport.dispatch(
            "break_block",
            {"x": position[0], "y": position[1], "z": position[2]},
        )
    )
    return bool(response.get("broken") or response.get("success")) and _block_id(
        client, position
    ) in AIR


def _roll_offer(
    client: Any,
    villager: Dict[str, Any],
    enchantment: str,
    *,
    max_attempts: int = 12,
) -> Tuple[Dict[str, Any] | None, Tuple[int, int, int] | None, int]:
    lectern = _lectern_position(villager)
    if lectern is None:
        return None, None, 0
    for attempt in range(1, max(1, int(max_attempts)) + 1):
        if not _place_lectern(client, lectern):
            return None, lectern, attempt
        time.sleep(0.25)
        screen = _open_merchant(client, villager)
        offer = _offer_for_enchantment(screen, enchantment)
        if offer is not None:
            return offer, lectern, attempt
        _close_screen(client)
        if not _break_lectern(client, lectern):
            return None, lectern, attempt
        time.sleep(0.25)
    return None, lectern, max_attempts


def _purchase_selected_offer(
    client: Any,
    villager: Mapping[str, Any],
    offer: Mapping[str, Any],
    enchantment: str,
) -> Tuple[bool, Dict[str, Any]]:
    screen = _open_merchant(client, villager)
    current = _offer_for_enchantment(screen, enchantment)
    if current is None or int(current.get("index", -1)) != int(offer.get("index", -2)):
        _close_screen(client)
        return False, {"reason": "verified offer changed before purchase"}
    selected = _dispatch_payload(
        client.transport.dispatch(
            "select_trade",
            {"index": int(current["index"]), "count": 1},
        )
    )
    if not selected.get("success"):
        _close_screen(client)
        return False, {"reason": "bridge did not complete merchant offer", "selection": selected}
    before_uses = int(current.get("uses", 0) or 0)
    completed_count = int(selected.get("completed_count", 0) or 0)
    selected_offer = selected.get("selected_offer", {})
    if not isinstance(selected_offer, Mapping):
        selected_offer = {}
    reported_before_uses = int(selected.get("uses_before", -1) or 0)
    after_uses = int(selected.get("uses_after", before_uses) or 0)
    acquired_results = selected.get("acquired_results", [])
    selected_result = (
        acquired_results[0]
        if isinstance(acquired_results, list) and acquired_results
        else {}
    )
    result_verified = (
        isinstance(selected_result, Mapping)
        and selected_result.get("id") == "minecraft:enchanted_book"
        and enchantment in _item_enchantments(selected_result)
    )
    _close_screen(client)
    inventory = _inventory_items(
        _dispatch_payload(client.transport.dispatch("get_inventory", {}))
    )
    books = _verified_inventory_enchantments(
        inventory,
        item_ids={"minecraft:enchanted_book"},
    )
    success = (
        int(selected.get("selected_index", -1)) == int(current["index"])
        and completed_count == 1
        and reported_before_uses == before_uses
        and after_uses > before_uses
        and result_verified
        and enchantment in books
    )
    return success, {
        "selection": selected,
        "offer_index": int(current["index"]),
        "uses_before": before_uses,
        "uses_after": after_uses,
        "completed_count": completed_count,
        "result_verified": result_verified,
        "book_observed": enchantment in books,
    }


def _ensure_anvil(client: Any) -> Tuple[int, int, int] | None:
    found = _dispatch_payload(
        client.transport.dispatch(
            "find_blocks",
            {"blocks": ["minecraft:anvil"], "radius": 24, "limit": 8},
        )
    ).get("found", [])
    if isinstance(found, list) and found:
        first = found[0]
        return int(first["x"]), int(first["y"]), int(first["z"])
    if count_item(client, "minecraft:anvil") < 1 and not craft(client, "minecraft:anvil", 1):
        return None
    state = _dispatch_payload(client.transport.dispatch("get_state", {}))
    position = state.get("block_position", {})
    try:
        target = int(position["x"]) + 1, int(position["y"]) - 1, int(position["z"])
    except (KeyError, TypeError, ValueError):
        return None
    return target if robust_place(client, *target, "minecraft:anvil") else None


def _screen_slot(
    slots: Iterable[Mapping[str, Any]],
    item_ids: Set[str],
    enchantment: str | None = None,
) -> int | None:
    for item in slots:
        if item.get("id") not in item_ids:
            continue
        if enchantment is not None and enchantment not in _item_enchantments(item):
            continue
        if isinstance(item.get("slot"), int):
            return int(item["slot"])
    return None


def _apply_books_to_tool(
    client: Any,
    enchantments: Iterable[str],
) -> Tuple[bool, List[Dict[str, Any]], Set[str]]:
    anvil = _ensure_anvil(client)
    if anvil is None or not goto(client, *anvil, timeout=120, tolerance=4.0):
        return False, [], set()
    applications: List[Dict[str, Any]] = []
    for enchantment in enchantments:
        client.transport.dispatch(
            "interact_block",
            {"x": anvil[0], "y": anvil[1], "z": anvil[2]},
        )
        screen = _dispatch_payload(client.transport.dispatch("get_screen", {}))
        if "anvil" not in str(screen.get("type", "")).lower():
            _close_screen(client)
            return False, applications, set()
        slots = screen.get("slots", [])
        tool_slot = _screen_slot(slots, TOOL_IDS)
        book_slot = _screen_slot(slots, {"minecraft:enchanted_book"}, enchantment)
        if tool_slot is None or book_slot is None:
            _close_screen(client)
            return False, applications, set()
        for source, target in ((tool_slot, 0), (book_slot, 1)):
            client.transport.dispatch(
                "inventory_click", {"slot": source, "type": "PICKUP", "button": 0}
            )
            client.transport.dispatch(
                "inventory_click", {"slot": target, "type": "PICKUP", "button": 0}
            )
        output = _dispatch_payload(client.transport.dispatch("get_screen", {}))
        output_slots = output.get("slots", [])
        output_item = next(
            (
                item
                for item in output_slots
                if isinstance(item, Mapping) and item.get("slot") == 2
            ),
            {},
        )
        if enchantment not in _item_enchantments(output_item):
            _close_screen(client)
            return False, applications, set()
        client.transport.dispatch(
            "inventory_click", {"slot": 2, "type": "QUICK_MOVE", "button": 0}
        )
        applications.append({"enchantment": enchantment, "output": dict(output_item)})
        _close_screen(client)
    final_items = _inventory_items(
        _dispatch_payload(client.transport.dispatch("get_inventory", {}))
    )
    final = _best_item_enchantments(final_items, item_ids=TOOL_IDS)
    return REQUIRED_ENCHANTMENTS.issubset(final), applications, final


class ToolPerfectionHandler(PhaseHandler):
    """Acquire four librarian books and verify them on a pickaxe."""

    def get_name(self) -> str:
        return "Trading Empire (Hour 7-8)"

    def execute(
        self,
        client: Any,
        resources: ResourceManager,
        state: StateManager,
    ) -> TaskResult:
        try:
            inventory = _inventory_items(
                _dispatch_payload(client.transport.dispatch("get_inventory", {}))
            )
            tool_enchantments = _best_item_enchantments(
                inventory,
                item_ids=TOOL_IDS,
            )
            if not any(item.get("id") in TOOL_IDS for item in inventory):
                return TaskResult.fail("A diamond or netherite pickaxe is required")
            book_enchantments = _verified_inventory_enchantments(
                inventory,
                item_ids={"minecraft:enchanted_book"},
            )
            missing_books = sorted(REQUIRED_ENCHANTMENTS - tool_enchantments - book_enchantments)
            candidates = _villager_candidates(get_nearby_entities(client, radius=64))
        except Exception as exc:
            return TaskResult.fail(f"Could not capture trading prerequisites: {exc}")

        trade_evidence: List[Dict[str, Any]] = []
        if len(candidates) < len(missing_books):
            return TaskResult.fail(
                "Not enough adult villagers for independently locked librarian offers",
                required=len(missing_books),
                observed=len(candidates),
            )
        for enchantment, villager in zip(missing_books, candidates):
            offer, lectern, attempts = _roll_offer(client, villager, enchantment)
            if offer is None:
                return TaskResult.fail(
                    f"Could not roll a verified {enchantment} offer within the attempt bound",
                    enchantment=enchantment,
                    attempts=attempts,
                )
            purchased, purchase = _purchase_selected_offer(
                client,
                villager,
                offer,
                enchantment,
            )
            trade_evidence.append(
                {
                    "enchantment": enchantment,
                    "villager_id": villager.get("id"),
                    "lectern": list(lectern) if lectern else None,
                    "attempts": attempts,
                    "offer": offer,
                    "purchase": purchase,
                }
            )
            if not purchased:
                return TaskResult.fail(
                    f"Selected {enchantment} trade did not produce a verified book",
                    trades=trade_evidence,
                )

        inventory = _inventory_items(
            _dispatch_payload(client.transport.dispatch("get_inventory", {}))
        )
        books = _verified_inventory_enchantments(
            inventory,
            item_ids={"minecraft:enchanted_book"},
        )
        to_apply = sorted(REQUIRED_ENCHANTMENTS - tool_enchantments)
        if not set(to_apply).issubset(books):
            return TaskResult.fail(
                "Purchased books are missing before anvil application",
                required=to_apply,
                observed=sorted(books),
            )
        applied, applications, final_enchantments = _apply_books_to_tool(client, to_apply)
        payload = {
            "verification_version": 2,
            "librarian_count": len(candidates),
            "trades": trade_evidence,
            "anvil_applications": applications,
            "verified_enchantments": sorted(final_enchantments),
            "tool_perfected": bool(applied),
            "implementation_blocker": None if applied else "final tool metadata verification failed",
        }
        state.record_phase_payload(Phase.TOOL_PERFECTION, payload)
        if not applied:
            return TaskResult.fail(payload["implementation_blocker"], **payload)
        return TaskResult.ok("Librarian books applied and perfect tool verified", **payload)
