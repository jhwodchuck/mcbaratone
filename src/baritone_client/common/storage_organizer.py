"""Bounded, leased storage maintenance for the fleet Quartermaster role."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from . import harness_ops
from .inventory import count_item, craft, get_inventory
from .storage_catalog import StorageCatalog, catalog_for
from .storage_safety import storage_travel_safe
from .warehouse_planner import (
    ensure_warehouse_layout,
    mark_slot_blocked,
    planned_slot_is_obstructed,
    reserve_category_slot,
)
from .warehouse_placement import create_double_chest_at


Position = Tuple[int, int, int]
MAX_BATCH_STACKS = 12
MANAGED_PURPOSES = {
    "checkpointed_storage",
    "emergency_overflow",
    "general_storage",
    "intake",
    "legacy",
    "legacy_storage",
}


@dataclass(frozen=True)
class StorageJob:
    """One catalog-derived storage action."""

    dimension: str
    category: str
    reason: str
    source: Optional[Position] = None
    destination: Optional[Position] = None
    capacity_only: bool = False
    retire_source_when_empty: bool = False
    corrects_misfiled_items: bool = False


@dataclass(frozen=True)
class QuartermasterCycleResult:
    """Durable evidence returned by one bounded Quartermaster cycle."""

    success: bool
    detail: str
    items_moved: int = 0
    stacks_moved: int = 0
    double_chests_created: int = 0
    free_slots_added: int = 0
    total_items_moved: int = 0


def category_for_item(item_id: str) -> str:
    """Classify an item into one broad, stable warehouse category."""
    item = str(item_id).split(":", 1)[-1]
    if any(
        token in item
        for token in (
            "raw_iron",
            "raw_gold",
            "raw_copper",
            "iron_ingot",
            "gold_ingot",
            "copper_ingot",
            "diamond",
            "emerald",
            "coal",
            "redstone",
            "lapis",
            "quartz",
            "ancient_debris",
            "netherite",
        )
    ):
        return "ores"
    if any(
        token in item
        for token in ("_log", "_wood", "planks", "sapling", "stick", "bamboo")
    ):
        return "wood"
    if any(
        token in item
        for token in (
            "bread",
            "beef",
            "porkchop",
            "chicken",
            "mutton",
            "rabbit",
            "potato",
            "carrot",
            "apple",
            "wheat",
            "beetroot",
            "melon",
            "pumpkin",
            "seeds",
        )
    ):
        return "food"
    if any(
        item.endswith(suffix)
        for suffix in (
            "_pickaxe",
            "_axe",
            "_shovel",
            "_hoe",
            "_sword",
            "_helmet",
            "_chestplate",
            "_leggings",
            "_boots",
        )
    ) or item in {"bow", "crossbow", "shield", "shears", "fishing_rod"}:
        return "tools"
    if any(
        token in item
        for token in (
            "leather",
            "string",
            "bone",
            "rotten_flesh",
            "gunpowder",
            "spider_eye",
            "blaze_",
            "ender_pearl",
            "slime_ball",
        )
    ):
        return "mob_drops"
    if any(
        token in item
        for token in (
            "nether_",
            "end_",
            "chorus",
            "shulker",
            "elytra",
            "dragon_",
            "ghast_",
            "magma_cream",
            "prismarine",
            "heart_of_the_sea",
            "nautilus",
            "totem_of_undying",
        )
    ):
        return "rare"
    if any(
        token in item
        for token in (
            "cobblestone",
            "stone",
            "dirt",
            "sand",
            "gravel",
            "glass",
            "brick",
            "concrete",
            "terracotta",
            "deepslate",
        )
    ):
        return "building"
    return "misc"


def _metadata(record: Mapping[str, Any]) -> Dict[str, Any]:
    raw = record.get("metadata_json", {})
    if isinstance(raw, Mapping):
        return dict(raw)
    try:
        value = json.loads(str(raw or "{}"))
    except (TypeError, ValueError):
        return {}
    return dict(value) if isinstance(value, Mapping) else {}


def _position(record: Mapping[str, Any]) -> Position:
    return tuple(int(record[axis]) for axis in ("x", "y", "z"))


def _container_category(record: Mapping[str, Any]) -> str:
    metadata = _metadata(record)
    category = str(metadata.get("category", "")).strip().lower()
    if category:
        return category
    for value in (record.get("purpose"), record.get("label")):
        text = str(value or "").strip().lower()
        if text.startswith("quartermaster:"):
            return text.split(":", 1)[1]
    return ""


def _fleet_managed(record: Mapping[str, Any]) -> bool:
    metadata = _metadata(record)
    if metadata.get("player_owned") is True:
        return False
    if metadata.get("fleet_managed") is True:
        return True
    tags = {str(value).lower() for value in metadata.get("tags", [])}
    purpose = str(record.get("purpose") or "").lower()
    return bool(
        purpose in MANAGED_PURPOSES
        or purpose.startswith("fleet_")
        or purpose.startswith("quartermaster:")
        or tags.intersection({"fleet", "storage", "home", "intake"})
    )


def _free_slots(record: Mapping[str, Any]) -> int:
    try:
        capacity = max(0, int(record.get("capacity_slots", 0) or 0))
        occupied = max(0, int(record.get("occupied_slots", 0) or 0))
    except (TypeError, ValueError):
        return 0
    return max(0, capacity - occupied)


def plan_storage_job(
    catalog: StorageCatalog,
    *,
    dimension: Optional[str] = None,
) -> Optional[StorageJob]:
    """Choose the highest-value safe job from last-known catalog state."""
    records = [
        record
        for record in catalog.list_containers()
        if _fleet_managed(record)
        and (dimension is None or str(record["dimension"]) == str(dimension))
    ]
    warehouses = catalog.list_warehouses()
    record_dimensions = tuple(dict.fromkeys(str(record["dimension"]) for record in records))
    for dimension in record_dimensions:
        matching_warehouses = [
            warehouse
            for warehouse in warehouses
            if str(warehouse["dimension"]) == dimension
        ]
        if not matching_warehouses:
            return StorageJob(
                dimension,
                "intake",
                "fleet-managed storage exists but the warehouse intake is not established",
                capacity_only=True,
            )
        warehouse = matching_warehouses[0]
        intake = [
            reservation
            for reservation in catalog.list_slot_reservations(
                str(warehouse["warehouse_id"])
            )
            if reservation["zone"] == "intake"
            and reservation["category"] == "intake"
            and reservation["slot_index"] == 0
        ]
        if not intake or intake[0]["state"] in {"planned", "building"}:
            return StorageJob(
                str(warehouse["dimension"]),
                "intake",
                "the warehouse intake pair is reserved but not yet verified",
                capacity_only=True,
            )
    destinations: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    for record in records:
        category = _container_category(record)
        if category and _free_slots(record) > 0:
            destinations.setdefault((str(record["dimension"]), category), []).append(record)

    candidates: list[tuple[int, StorageJob]] = []
    for record in records:
        position = _position(record)
        dimension = str(record["dimension"])
        inventory = catalog.container_inventory(position, dimension=dimension)
        if not inventory:
            continue
        source_category = _container_category(record)
        purpose = str(record.get("purpose") or "").lower()
        movable: dict[str, int] = {}
        for item_id, count in inventory.items():
            category = category_for_item(item_id)
            if source_category and source_category == category:
                continue
            movable[category] = movable.get(category, 0) + int(count)
        if not movable:
            if source_category and _free_slots(record) <= max(
                1, int((record.get("capacity_slots", 0) or 0) * 0.2)
            ):
                candidates.append(
                    (
                        120,
                        StorageJob(
                            dimension,
                            source_category,
                            "managed category storage is above 80 percent occupancy",
                            source=position,
                            capacity_only=True,
                        ),
                    )
                )
            continue

        category = max(movable, key=movable.get)
        destination = next(
            (
                _position(candidate)
                for candidate in destinations.get((dimension, category), [])
                if _position(candidate) != position
            ),
            None,
        )
        priority = 220 if purpose in {"intake", "emergency_overflow"} else 190
        if purpose in {"legacy", "legacy_storage", "checkpointed_storage"}:
            priority += 20
        candidates.append(
            (
                priority + min(50, movable[category] // 16),
                StorageJob(
                    dimension,
                    category,
                    f"{movable[category]} {category} items are in {purpose or 'uncategorized fleet storage'}",
                    source=position,
                    destination=destination,
                    retire_source_when_empty=purpose in {"legacy", "legacy_storage"},
                    corrects_misfiled_items=bool(
                        source_category and source_category != category
                    ),
                ),
            )
        )
    return max(candidates, key=lambda item: item[0])[1] if candidates else None


def quartermaster_work_available(client: Any, state: Any) -> bool:
    """Return whether the shared catalog currently advertises useful work."""
    try:
        live = client.transport.dispatch("get_state", {})
        dimension = str(live.get("dimension") or "minecraft:overworld")
        return plan_storage_job(
            catalog_for(client, state),
            dimension=dimension,
        ) is not None
    except Exception:
        return False


def _totals(slots: Sequence[Mapping[str, Any]], container_slots: int) -> Dict[str, int]:
    totals: Dict[str, int] = {}
    for item in slots:
        slot = int(item.get("slot", -1))
        item_id = str(item.get("id") or "")
        count = max(0, int(item.get("count", 0) or 0))
        if 0 <= slot < container_slots and item_id and item_id != "minecraft:air" and count:
            totals[item_id] = totals.get(item_id, 0) + count
    return totals


def _open_snapshot(client: Any, position: Position) -> tuple[dict[str, Any], int]:
    live = client.transport.dispatch("get_state", {})
    if not storage_travel_safe(live):
        raise RuntimeError("storage work is below the health or hunger margin")
    if not harness_ops.move_near(client, *position, timeout=30.0):
        raise RuntimeError(f"could not reach storage at {position}")
    if not harness_ops.open_container(client, position, timeout=5.0, attempts=3):
        raise RuntimeError(f"could not open storage at {position}")
    screen = client.transport.dispatch("get_screen", {})
    data = screen.get("data", screen)
    data = dict(data)
    if "sync_id" not in data and isinstance(screen, Mapping):
        data["sync_id"] = screen.get("sync_id")
    slots = list(data.get("slots", []))
    total = int(data.get("total_slots") or len(slots))
    container_slots = total - 36
    if container_slots not in (27, 54):
        harness_ops.close_container(client)
        raise RuntimeError(f"unsupported container layout at {position}: {total}")
    return data, container_slots


def _observe(
    catalog: StorageCatalog,
    position: Position,
    dimension: str,
    data: Mapping[str, Any],
    container_slots: int,
    *,
    category: str = "",
) -> Dict[str, int]:
    slots = list(data.get("slots", []))
    catalog.observe_inventory(
        position,
        [item for item in slots if int(item.get("slot", -1)) < container_slots],
        dimension=dimension,
        capacity_slots=container_slots,
        label=f"quartermaster:{category}" if category else None,
        purpose=f"quartermaster:{category}" if category else None,
    )
    return _totals(slots, container_slots)


def _create_category_storage(
    client: Any,
    state: Any,
    catalog: StorageCatalog,
    category: str,
    dimension: str,
) -> Position:
    warehouse, layout = ensure_warehouse_layout(client, catalog, dimension)
    reservation = reserve_category_slot(catalog, warehouse, layout, category)
    first, second = tuple(reservation["paired_coordinates"])
    reservation_metadata = dict(reservation["metadata"])
    confirmed = {
        tuple(position)
        for position in reservation_metadata.get("confirmed_coordinates", ())
        if isinstance(position, (list, tuple)) and len(position) == 3
    }
    runtime_confirmed = set(getattr(state, "_warehouse_partial_coordinates", ()))
    confirmed &= runtime_confirmed
    for position in sorted(confirmed):
        if "chest" not in harness_ops._block_at(client, *position):
            continue
        data, container_slots = _open_snapshot(client, position)
        contents = _totals(list(data.get("slots", [])), container_slots)
        harness_ops.close_container(client)
        if container_slots != 27 or contents:
            mark_slot_blocked(
                catalog, warehouse, reservation,
                "confirmed partial chest was replaced, joined, or populated",
                metadata=reservation_metadata,
            )
            raise RuntimeError("the confirmed partial warehouse chest is not empty")
    if planned_slot_is_obstructed(
        client, (first, second), allowed_existing=tuple(confirmed)
    ):
        mark_slot_blocked(
            catalog, warehouse, reservation,
            "obstructed or unsupported exact coordinates",
            metadata=reservation_metadata,
        )
        raise RuntimeError("the planned warehouse double chest position is blocked")

    def record_placed(position: Position) -> None:
        confirmed.add(tuple(position))
        runtime_confirmed.add(tuple(position))
        setattr(state, "_warehouse_partial_coordinates", runtime_confirmed)
        reservation_metadata.update(
            {
                "confirmed_coordinates": [list(value) for value in sorted(confirmed)],
                "placement_owner": str(
                    getattr(state, "checkpoint_dir", "quartermaster-controller")
                ),
            }
        )
        catalog.update_slot_reservation_state(
            str(warehouse["warehouse_id"]),
            str(reservation["zone"]),
            category,
            int(reservation["slot_index"]),
            "building",
            metadata=reservation_metadata,
        )

    required_chests = sum(
        "chest" not in harness_ops._block_at(client, *position)
        for position in (first, second)
    )
    carried = count_item(client, "minecraft:chest")
    if carried < required_chests and not craft(
        client, "minecraft:chest", required_chests - carried
    ):
        raise RuntimeError("could not craft the chests needed for warehouse expansion")
    created = create_double_chest_at(
        client,
        first,
        second,
        allowed_existing=tuple(confirmed),
        on_placed=record_placed,
    )
    if not created:
        if planned_slot_is_obstructed(
            client, (first, second), allowed_existing=tuple(confirmed)
        ):
            mark_slot_blocked(
                catalog, warehouse, reservation,
                "obstructed or unsupported exact coordinates",
                metadata=reservation_metadata,
            )
        raise RuntimeError("could not place or resume the planned warehouse double chest")
    data, container_slots = _open_snapshot(client, first)
    if container_slots != 54:
        harness_ops.close_container(client)
        mark_slot_blocked(
            catalog, warehouse, reservation,
            "placed pair did not open as 54 slots",
            metadata=reservation_metadata,
        )
        raise RuntimeError("placed chest pair did not verify as a 54-slot container")
    logical_id = (
        f"{warehouse['warehouse_id']}:{category}:{reservation['slot_index']}"
    )
    metadata = {
        "fleet_managed": True,
        "category": category,
        "warehouse_id": str(warehouse["warehouse_id"]),
        "warehouse_zone": str(reservation["zone"]),
        "warehouse_row": int(reservation["metadata"]["row"]),
        "warehouse_slot_index": int(reservation["slot_index"]),
        "logical_container_id": logical_id,
        "paired_coordinates": [list(first), list(second)],
        "canonical_coordinate": list(first),
    }
    for position in (first, second):
        catalog.register_container(
            position,
            dimension=dimension,
            label=f"quartermaster:{category}",
            purpose=f"quartermaster:{category}",
            metadata=metadata,
        )
    _observe(catalog, first, dimension, data, container_slots, category=category)
    catalog.record_event(
        first,
        "storage_created",
        dimension=dimension,
        details={"category": category, "capacity_slots": 54, "paired": list(second)},
    )
    catalog.update_slot_reservation_state(
        str(warehouse["warehouse_id"]),
        str(reservation["zone"]),
        category,
        int(reservation["slot_index"]),
        "verified",
        metadata={**reservation_metadata, "logical_container_id": logical_id},
    )
    harness_ops.close_container(client)
    return first


def _transfer_category_batch(
    client: Any,
    catalog: StorageCatalog,
    job: StorageJob,
    destination: Position,
) -> tuple[int, int, bool]:
    if job.source is None:
        return 0, 0, False
    player_before = get_inventory(client)
    destination_data, destination_slots = _open_snapshot(client, destination)
    destination_before = _observe(
        catalog,
        destination,
        job.dimension,
        destination_data,
        destination_slots,
        category=job.category,
    )
    destination_free = destination_slots - sum(
        1
        for item in destination_data.get("slots", [])
        if 0 <= int(item.get("slot", -1)) < destination_slots
        and int(item.get("count", 0) or 0) > 0
    )
    harness_ops.close_container(client)
    if destination_free <= 0:
        raise RuntimeError("category destination has no free slots")

    source_data, source_slots = _open_snapshot(client, job.source)
    source_before = _observe(
        catalog, job.source, job.dimension, source_data, source_slots
    )
    candidates = [
        item
        for item in source_data.get("slots", [])
        if 0 <= int(item.get("slot", -1)) < source_slots
        and int(item.get("count", 0) or 0) > 0
        and category_for_item(str(item.get("id") or "")) == job.category
    ][: min(MAX_BATCH_STACKS, destination_free)]
    if not candidates:
        harness_ops.close_container(client)
        raise RuntimeError("source no longer contains the planned category")
    sync_id = source_data.get("sync_id")
    selected_ids = {str(item.get("id")) for item in candidates}
    for item in candidates:
        payload = {"slot": int(item["slot"]), "type": "QUICK_MOVE", "button": 0}
        if sync_id is not None:
            payload["sync_id"] = sync_id
        client.transport.dispatch("inventory_click", payload)
        time.sleep(0.05)
    source_final = client.transport.dispatch("get_screen", {})
    source_final_data = source_final.get("data", source_final)
    source_after = _observe(
        catalog, job.source, job.dimension, source_final_data, source_slots
    )
    harness_ops.close_container(client)

    destination_data, destination_slots = _open_snapshot(client, destination)
    sync_id = destination_data.get("sync_id")
    for item in destination_data.get("slots", []):
        slot = int(item.get("slot", -1))
        if slot < destination_slots or str(item.get("id")) not in selected_ids:
            continue
        if int(item.get("count", 0) or 0) <= 0:
            continue
        payload = {"slot": slot, "type": "QUICK_MOVE", "button": 0}
        if sync_id is not None:
            payload["sync_id"] = sync_id
        client.transport.dispatch("inventory_click", payload)
        time.sleep(0.05)
    destination_final = client.transport.dispatch("get_screen", {})
    destination_final_data = destination_final.get("data", destination_final)
    destination_after = _observe(
        catalog,
        destination,
        job.dimension,
        destination_final_data,
        destination_slots,
        category=job.category,
    )
    harness_ops.close_container(client)
    player_after = get_inventory(client)

    for item_id in selected_ids:
        before_total = (
            source_before.get(item_id, 0)
            + destination_before.get(item_id, 0)
            + player_before.get(item_id, 0)
        )
        after_total = (
            source_after.get(item_id, 0)
            + destination_after.get(item_id, 0)
            + player_after.get(item_id, 0)
        )
        if before_total != after_total:
            raise RuntimeError(f"item conservation failed for {item_id}")
        if player_after.get(item_id, 0) > player_before.get(item_id, 0):
            raise RuntimeError(f"destination could not accept the full {item_id} batch")
    source_loss = sum(
        max(0, source_before.get(item_id, 0) - source_after.get(item_id, 0))
        for item_id in selected_ids
    )
    destination_gain = sum(
        max(0, destination_after.get(item_id, 0) - destination_before.get(item_id, 0))
        for item_id in selected_ids
    )
    if source_loss <= 0 or destination_gain < source_loss:
        raise RuntimeError("storage transfer produced no verified destination delta")
    emptied = sum(source_after.values()) == 0
    catalog.record_event(
        job.source,
        "migration_committed",
        dimension=job.dimension,
        details={
            "destination": list(destination),
            "category": job.category,
            "items_moved": source_loss,
            "stacks_moved": len(candidates),
        },
    )
    return source_loss, len(candidates), emptied


def _metrics(state: Any) -> Dict[str, int]:
    custom = getattr(state, "custom_data", None)
    if not isinstance(custom, dict):
        custom = {}
        state.custom_data = custom
    metrics = custom.setdefault("quartermaster", {})
    if not isinstance(metrics, dict):
        metrics = {}
        custom["quartermaster"] = metrics
    return metrics


def _add(metrics: Dict[str, int], key: str, amount: int = 1) -> None:
    metrics[key] = max(0, int(metrics.get(key, 0) or 0)) + max(0, int(amount))


def _container_lease_key(dimension: str, position: Position) -> str:
    return f"container:{dimension}:{position[0]}:{position[1]}:{position[2]}"


def run_quartermaster_cycle(client: Any, state: Any) -> QuartermasterCycleResult:
    """Run one leased capacity or verified item-migration transaction."""
    from ..automator.end_readiness import bot_name

    catalog = catalog_for(client, state)
    owner = bot_name(state) or "quartermaster-controller"
    if not catalog.acquire_lease("quartermaster:cycle", owner, ttl_seconds=180.0):
        return QuartermasterCycleResult(False, "another controller owns the Quartermaster lease")
    metrics = _metrics(state)
    try:
        live = client.transport.dispatch("get_state", {})
        dimension = str(live.get("dimension") or "minecraft:overworld")
        job = plan_storage_job(catalog, dimension=dimension)
        if job is None:
            return QuartermasterCycleResult(False, "shared catalog has no actionable storage job")
        _add(metrics, "containers_audited")
        destination = job.destination
        created = 0
        free_slots = 0
        if destination is None or job.capacity_only:
            destination = _create_category_storage(
                client, state, catalog, job.category, job.dimension
            )
            created = 1
            free_slots = 54
            _add(metrics, "double_chests_created")
            _add(metrics, "free_slots_added", 54)
            _add(metrics, "catalog_entries_refreshed", 2)
        if job.capacity_only:
            _add(metrics, "cycles")
            return QuartermasterCycleResult(
                True,
                f"created and verified 54 slots of {job.category} capacity",
                double_chests_created=created,
                free_slots_added=free_slots,
                total_items_moved=int(metrics.get("items_moved", 0) or 0),
            )

        positions = [position for position in (job.source, destination) if position]
        lease_keys = sorted(
            {_container_lease_key(job.dimension, position) for position in positions}
        )
        acquired: list[str] = []
        try:
            for lease_key in lease_keys:
                if not catalog.acquire_lease(lease_key, owner, ttl_seconds=180.0):
                    raise RuntimeError(
                        "source or destination storage is leased by another controller"
                    )
                acquired.append(lease_key)
            items_moved, stacks_moved, emptied = _transfer_category_batch(
                client, catalog, job, destination
            )
        finally:
            for lease_key in reversed(acquired):
                catalog.release_lease(lease_key, owner)
        _add(metrics, "cycles")
        _add(metrics, "items_moved", items_moved)
        _add(metrics, "stacks_moved", stacks_moved)
        _add(metrics, "catalog_entries_refreshed", 2)
        if job.corrects_misfiled_items:
            _add(metrics, "misfiled_items_corrected", items_moved)
        if emptied and job.retire_source_when_empty and job.source is not None:
            catalog.register_container(
                job.source,
                dimension=job.dimension,
                status="legacy_empty",
                metadata={
                    "fleet_managed": True,
                    "retired_by": owner,
                    "retired_at": time.time(),
                },
            )
            catalog.record_event(
                job.source,
                "legacy_empty",
                dimension=job.dimension,
                details={"destination": list(destination)},
            )
            _add(metrics, "legacy_chests_emptied")
        return QuartermasterCycleResult(
            True,
            f"moved {items_moved} {job.category} items in {stacks_moved} stacks",
            items_moved=items_moved,
            stacks_moved=stacks_moved,
            double_chests_created=created,
            free_slots_added=free_slots,
            total_items_moved=int(metrics.get("items_moved", 0) or 0),
        )
    except Exception as exc:
        _add(metrics, "migration_failures")
        return QuartermasterCycleResult(False, f"{type(exc).__name__}: {exc}")
    finally:
        catalog.release_lease("quartermaster:cycle", owner)


__all__ = [
    "QuartermasterCycleResult",
    "StorageJob",
    "category_for_item",
    "plan_storage_job",
    "quartermaster_work_available",
    "run_quartermaster_cycle",
]
