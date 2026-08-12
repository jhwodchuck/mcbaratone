"""Reserve policy for bounded crop and forestry delivery cycles."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Mapping


def _counts(value: Mapping[str, int]) -> dict[str, int]:
    return {str(item): int(count) for item, count in value.items()}


@dataclass(frozen=True)
class RenewableSnapshot:
    """Observed standing crops and item counts at renewable endpoints."""

    mature_crops: dict[str, int]
    food_source: dict[str, int]
    food_working: dict[str, int]
    food_reserve: dict[str, int]
    forestry_source: dict[str, int]
    forestry_working: dict[str, int]
    forestry_reserve: dict[str, int]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class RenewableReservePolicy:
    """Minimum renewable stock that every delivery boundary must retain."""

    mature_crop_minimums: dict[str, int]
    minimum_food_items: int
    minimum_source_logs: int
    minimum_total_logs: int
    minimum_total_saplings: int
    minimum_source_saplings: dict[str, int]
    integrated_food_working: int = 1
    integrated_food_reserve: int = 1
    integrated_managed_logs: int = 32
    integrated_managed_saplings: int = 1


@dataclass(frozen=True)
class RenewableReserveAudit:
    """Machine-readable decision for one renewable reserve snapshot."""

    accepted: bool
    integrated: bool
    totals: dict[str, int]
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "accepted": self.accepted,
            "integrated": self.integrated,
            "totals": dict(self.totals),
            "reasons": list(self.reasons),
        }


def _sum_items(*values: Mapping[str, int], suffix: str | None = None) -> int:
    total = 0
    for value in values:
        for item, count in value.items():
            if suffix is None or str(item).endswith(suffix):
                total += int(count)
    return total


def audit_renewable_reserves(
    snapshot: RenewableSnapshot,
    policy: RenewableReservePolicy,
    *,
    require_integrated: bool = False,
) -> RenewableReserveAudit:
    """Reject a delivery boundary that consumes renewal or reserve stock."""

    mature = _counts(snapshot.mature_crops)
    food_source = _counts(snapshot.food_source)
    food_working = _counts(snapshot.food_working)
    food_reserve = _counts(snapshot.food_reserve)
    forestry_source = _counts(snapshot.forestry_source)
    forestry_working = _counts(snapshot.forestry_working)
    forestry_reserve = _counts(snapshot.forestry_reserve)

    food_total = _sum_items(food_source, food_working, food_reserve)
    source_logs = _sum_items(forestry_source, suffix="_log")
    managed_logs = _sum_items(
        forestry_working, forestry_reserve, suffix="_log"
    )
    total_logs = source_logs + managed_logs
    source_saplings = _sum_items(forestry_source, suffix="_sapling")
    managed_saplings = _sum_items(
        forestry_working, forestry_reserve, suffix="_sapling"
    )
    total_saplings = source_saplings + managed_saplings
    totals = {
        "mature_crops": sum(mature.values()),
        "food_items": food_total,
        "food_working": _sum_items(food_working),
        "food_reserve": _sum_items(food_reserve),
        "source_logs": source_logs,
        "managed_logs": managed_logs,
        "total_logs": total_logs,
        "source_saplings": source_saplings,
        "managed_saplings": managed_saplings,
        "total_saplings": total_saplings,
    }
    reasons: list[str] = []

    for crop, minimum in policy.mature_crop_minimums.items():
        actual = mature.get(crop, 0)
        if actual < int(minimum):
            reasons.append(f"mature {crop} {actual} is below {int(minimum)}")
    if food_total < policy.minimum_food_items:
        reasons.append(
            f"food items {food_total} are below {policy.minimum_food_items}"
        )
    if source_logs < policy.minimum_source_logs:
        reasons.append(
            f"source logs {source_logs} are below {policy.minimum_source_logs}"
        )
    if total_logs < policy.minimum_total_logs:
        reasons.append(
            f"total logs {total_logs} are below {policy.minimum_total_logs}"
        )
    if total_saplings < policy.minimum_total_saplings:
        reasons.append(
            f"total saplings {total_saplings} are below "
            f"{policy.minimum_total_saplings}"
        )
    for sapling, minimum in policy.minimum_source_saplings.items():
        actual = forestry_source.get(sapling, 0)
        if actual < int(minimum):
            reasons.append(f"source {sapling} {actual} is below {int(minimum)}")

    integrated = (
        totals["food_working"] >= policy.integrated_food_working
        and totals["food_reserve"] >= policy.integrated_food_reserve
        and managed_logs >= policy.integrated_managed_logs
        and managed_saplings >= policy.integrated_managed_saplings
    )
    if require_integrated and not integrated:
        reasons.append("managed crop/forestry delivery targets are incomplete")
    return RenewableReserveAudit(
        accepted=not reasons,
        integrated=integrated,
        totals=totals,
        reasons=tuple(reasons),
    )


__all__ = [
    "RenewableReserveAudit",
    "RenewableReservePolicy",
    "RenewableSnapshot",
    "audit_renewable_reserves",
]
