"""Conservative, read-only screening before committing to a new homestead.

This is a starter-site screen, not proof of a built home or renewable food.
Unknown terrain is rejected; exploration must not overwrite the old home.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import hypot
from typing import Any, Mapping, Optional

from .escape_recovery import _passable, destination_safe

SOIL = ["minecraft:grass_block", "minecraft:dirt", "minecraft:farmland"]
LOGS = [f"minecraft:{wood}_log" for wood in (
    "oak", "birch", "spruce", "acacia", "dark_oak", "jungle", "mangrove", "cherry",
)]
STONE = ["minecraft:stone", "minecraft:cobblestone", "minecraft:deepslate"]
ORES = [
    "minecraft:coal_ore", "minecraft:deepslate_coal_ore",
    "minecraft:iron_ore", "minecraft:deepslate_iron_ore",
    "minecraft:copper_ore", "minecraft:deepslate_copper_ore",
]

USEFUL_HOME_BIOMES = {
    "plains", "sunflower_plains", "forest", "flower_forest", "birch_forest",
    "meadow", "cherry_grove", "savanna", "taiga", "river", "beach",
}


@dataclass(frozen=True)
class HomeSiteAssessment:
    """A durable, human-readable home candidate score."""

    position: tuple[int, int, int]
    score: int
    eligible: bool
    factors: Mapping[str, Any]
    reasons: tuple[str, ...]
    dimension: str = "minecraft:overworld"

    def to_dict(self) -> dict[str, Any]:
        return {
            "position": list(self.position),
            "dimension": self.dimension,
            "score": self.score,
            "eligible": self.eligible,
            "factors": dict(self.factors),
            "reasons": list(self.reasons),
        }


def supported_home_ground(block_id: str) -> bool:
    """Reject unknown, liquid, unsupported and damaging anchor floors."""
    from .terraform_verify import AIR, LIQUIDS, NON_SOLID, REGROWTH, classify_block

    name = classify_block(block_id)
    return name not in AIR | LIQUIDS | NON_SOLID | REGROWTH | {
        "unknown", "powder_snow", "magma_block", "cactus", "campfire",
        "soul_campfire", "pointed_dripstone", "sweet_berry_bush",
    } and not name.endswith(("_leaves", "_sapling"))


def assess_home_site(
    client: Any,
    position: tuple[int, int, int],
    *,
    state: Optional[Any] = None,
    include_context: bool = True,
) -> HomeSiteAssessment:
    """Score a candidate without loading chunks or moving the player.

    A 3x3 pad is only room for initial workstations. Actual house construction
    still needs its own footprint verification. Resource scans are centered on
    the candidate, with vertical bounds so a cave below a mountain is not
    mistaken for nearby surface resources.
    """
    x, y, z = map(int, position)
    factors: dict[str, Any] = {
        "supported_work_pad": False,
        "surface_soil": 0,
        "nearby_water": 0,
        "nearby_wood": 0,
        "nearby_stone": 0,
        "nearby_ores": 0,
        "expansion_space": 0,
        "terrain_flatness": 0,
        "useful_biomes": [],
        "valuable_landmarks": [],
    }
    reasons: list[str] = []
    dimension = "minecraft:overworld"
    try:
        pad_ok = True
        for dx in (-1, 0, 1):
            for dz in (-1, 0, 1):
                column = [client.transport.dispatch("get_block", {
                    "x": x + dx, "y": level, "z": z + dz,
                }).get("id", "") for level in (y - 1, y, y + 1)]
                if not supported_home_ground(column[0]):
                    pad_ok = False
                if any(not block or block in {"minecraft:void_air", "void_air"}
                       or not _passable(block) or "wither_rose" in block
                       for block in column[1:]):
                    pad_ok = False
        factors["supported_work_pad"] = pad_ok
        if not pad_ok:
            reasons.append("the supported clear 3x3 work pad is not verified")

        for blocks, radius, key in (
            (SOIL, 20, "surface_soil"),
            (["minecraft:water"], 24, "nearby_water"),
            (LOGS, 32, "nearby_wood"),
            (STONE, 32, "nearby_stone"),
            (ORES, 48, "nearby_ores"),
        ):
            result = client.transport.dispatch("find_blocks", {
                "blocks": blocks, "radius": radius, "limit": 128,
                "center": {"x": x, "y": y, "z": z},
            })
            found: list[tuple[int, int, int]] = []
            for block in result.get("found", []):
                bx, by, bz = (int(block[key]) for key in ("x", "y", "z"))
                if abs(by - y) > 4 or max(abs(bx - x), abs(bz - z)) > radius:
                    continue
                # Soil must have standing room; buried dirt is not farmland.
                if blocks == SOIL and not destination_safe(client, bx, by + 1, bz):
                    continue
                found.append((bx, by, bz))
            factors[key] = len(found)
            if not found and key in {"surface_soil", "nearby_water", "nearby_wood"}:
                reasons.append(f"no verified {key.replace('_', ' ')} within {radius} blocks")

        soil_count = int(factors["surface_soil"])
        factors["expansion_space"] = min(15, soil_count // 2)
        factors["terrain_flatness"] = 10 if pad_ok and soil_count >= 9 else 5 if pad_ok else 0

        if include_context:
            try:
                snapshot = client.transport.dispatch("get_state", {})
                current = snapshot.get("block_position", snapshot.get("position", {}))
                if all(axis in current for axis in ("x", "z")) and hypot(
                    float(current["x"]) - x, float(current["z"]) - z
                ) <= 8:
                    dimension = str(snapshot.get("dimension", dimension))
                    scan = client.transport.dispatch("scan_biomes", {"radius": 128, "step": 16})
                    biome_ids = {
                        str(entry.get("id", "")).split(":")[-1]
                        for entry in scan.get("biomes", [])
                        if isinstance(entry, Mapping)
                    }
                    current_biome = str(scan.get("current_biome", "")).split(":")[-1]
                    if current_biome:
                        biome_ids.add(current_biome)
                    factors["useful_biomes"] = sorted(biome_ids & USEFUL_HOME_BIOMES)
            except Exception:
                pass

            custom = getattr(state, "custom_data", {}) if state is not None else {}
            locations = custom.get("locations", {}) if isinstance(custom, Mapping) else {}
            if isinstance(locations, Mapping):
                valuable = []
                for category in ("village", "nether_portal", "cave", "mineshaft", "road"):
                    entries = locations.get(category, [])
                    if not isinstance(entries, list):
                        continue
                    for entry in entries:
                        if not isinstance(entry, Mapping):
                            continue
                        try:
                            if hypot(float(entry["x"]) - x, float(entry["z"]) - z) <= 256:
                                valuable.append(category)
                                break
                        except (KeyError, TypeError, ValueError):
                            continue
                factors["valuable_landmarks"] = sorted(set(valuable))
    except (AttributeError, KeyError, TypeError, ValueError, RuntimeError, OSError):
        reasons.append("terrain survey was incomplete")

    hard_requirements = (
        bool(factors["supported_work_pad"])
        and int(factors["surface_soil"]) > 0
        and int(factors["nearby_water"]) > 0
        and int(factors["nearby_wood"]) > 0
    )
    score = 0
    score += 20 if factors["supported_work_pad"] else 0
    score += 8 if factors["surface_soil"] else 0
    score += 8 if factors["nearby_water"] else 0
    score += 8 if factors["nearby_wood"] else 0
    score += 6 if factors["nearby_stone"] else 0
    score += 8 if factors["nearby_ores"] else 0
    score += int(factors["expansion_space"])
    score += int(factors["terrain_flatness"])
    score += min(9, len(factors["useful_biomes"]) * 2)
    score += min(8, len(factors["valuable_landmarks"]) * 3)
    if hard_requirements:
        reasons.append(f"candidate satisfies all mandatory home resources ({score}/100)")
    return HomeSiteAssessment(
        position=(x, y, z),
        score=min(100, score),
        eligible=hard_requirements,
        factors=factors,
        reasons=tuple(reasons),
        dimension=dimension,
    )


def suitable_home_site(client: Any, position: tuple[int, int, int]) -> bool:
    """Require a supported work pad and nearby soil, water and wood."""
    return assess_home_site(client, position, include_context=False).eligible
