"""Phase 8: inspect supported librarian/tool-perfection evidence."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Set

from ..phase_executor import PhaseHandler
from ..resource_manager import ResourceManager
from ..state_manager import Phase, StateManager
from ...common import TaskResult
from ...common.iron_farm import get_nearby_entities


REQUIRED_ENCHANTMENTS = {"mending", "efficiency", "unbreaking", "fortune"}


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
    """Parse future bridge component shapes without guessing from item count."""
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
    return names


def _verified_inventory_enchantments(items: Iterable[Mapping[str, Any]]) -> Set[str]:
    verified: Set[str] = set()
    for item in items:
        if str(item.get("id", "")) != "minecraft:enchanted_book":
            continue
        for field in ("stored_enchantments", "enchantments", "components"):
            verified.update(_enchantment_names(item.get(field)))
    return verified & REQUIRED_ENCHANTMENTS


def _librarians(entities: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    return [
        dict(entity)
        for entity in entities
        if str(entity.get("type", "")) == "minecraft:villager"
        and str(entity.get("profession", "")) == "minecraft:librarian"
        and not bool(entity.get("is_baby", False))
    ]


def _inspect_nearby_librarian(client: Any, librarians: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Open and read an already-near librarian screen using registered routes."""
    close_needed = False
    try:
        target = next(
            (
                entity
                for entity in librarians
                if float(entity.get("distance", 999.0) or 999.0) <= 5.0
                and isinstance(entity.get("id"), int)
            ),
            None,
        )
        if target is None:
            return {"trade_screen_opened": False, "trade_screen_type": ""}
        interaction = _dispatch_payload(
            client.transport.dispatch(
                "entity_interact",
                {
                    "action": "interact",
                    "entity_id": target["id"],
                    "max_distance": 5.0,
                },
            )
        )
        close_needed = bool(interaction.get("accepted") or interaction.get("success"))
        if not close_needed:
            return {"trade_screen_opened": False, "trade_screen_type": ""}
        screen = _dispatch_payload(client.transport.dispatch("get_screen", {}))
        slots = screen.get("slots", [])
        return {
            "trade_screen_opened": "merchant" in str(screen.get("type", "")).lower(),
            "trade_screen_type": str(screen.get("type", "")),
            "trade_screen_slot_ids": [
                str(slot.get("id", ""))
                for slot in slots
                if isinstance(slot, Mapping) and slot.get("id") != "minecraft:air"
            ],
        }
    except Exception as exc:
        return {
            "trade_screen_opened": False,
            "trade_screen_type": "",
            "trade_screen_error": str(exc),
        }
    finally:
        if close_needed:
            try:
                client.transport.dispatch("close_screen", {})
            except Exception:
                pass


class ToolPerfectionHandler(PhaseHandler):
    """Verify librarians/books and stop at the current bridge's evidence limit."""

    def get_name(self) -> str:
        return "Trading Empire (Hour 7-8)"

    def execute(
        self,
        client: Any,
        resources: ResourceManager,
        state: StateManager,
    ) -> TaskResult:
        # A generic enchanted-book count cannot prove any required enchantment.
        # Do not use the inventory-only ResourceManager phase-ready shortcut.
        try:
            entities = get_nearby_entities(client, radius=32)
            librarians = _librarians(entities)
            raw_inventory = _dispatch_payload(client.transport.dispatch("get_inventory", {}))
        except Exception as exc:
            return TaskResult.fail(f"Could not capture librarian trading evidence: {exc}")

        items = _inventory_items(raw_inventory)
        enchantments = _verified_inventory_enchantments(items)
        inspection = _inspect_nearby_librarian(client, librarians)
        payload: Dict[str, Any] = {
            "verification_version": 1,
            "librarian_count": len(librarians),
            "librarian_ids": [int(entity["id"]) for entity in librarians if isinstance(entity.get("id"), int)],
            "librarian_offer_counts": [int(entity.get("offers_count", 0) or 0) for entity in librarians],
            "enchanted_book_count": sum(
                int(item.get("count", 0) or 0)
                for item in items
                if item.get("id") == "minecraft:enchanted_book"
            ),
            "verified_enchantments": sorted(enchantments),
            **inspection,
        }

        missing = sorted(REQUIRED_ENCHANTMENTS - enchantments)
        blockers = []
        if not librarians:
            blockers.append(
                "no adult librarian is visible; the current bridge has no survival "
                "villager transport or workstation-assignment workflow"
            )
        if missing:
            blockers.append(
                "cannot verify required books "
                f"{', '.join(missing)}: current get_inventory/get_screen responses omit "
                "stored enchantment components and merchant offers"
            )

        if blockers:
            payload["implementation_blocker"] = "; ".join(blockers)
            state.record_phase_payload(Phase.TOOL_PERFECTION, payload)
            return TaskResult.fail(payload["implementation_blocker"], **payload)

        payload["implementation_blocker"] = None
        state.record_phase_payload(Phase.TOOL_PERFECTION, payload)
        return TaskResult.ok("Required librarian books verified", **payload)

    # Compatibility hooks remain explicitly unsupported instead of dispatching
    # fictitious breed/trade/cure commands.
    def _breed_villagers(self, client: Any) -> bool:
        return False

    def _roll_mending(self, client: Any) -> bool:
        return False

    def _roll_efficiency(self, client: Any) -> bool:
        return False

    def _roll_unbreaking(self, client: Any) -> bool:
        return False

    def _roll_fortune(self, client: Any) -> bool:
        return False

    def _cure_villagers(self, client: Any) -> bool:
        return False
