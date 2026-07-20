from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional
import re


WORKFLOW_TOPIC = "workflow"
BUILD_PLAN_TOPIC = "build-plan"
BUILDSITE_TOPIC = "buildsite"

WORKFLOW_RESOURCE_URI = "baritone://guidance/workflow"
BUILD_PLAN_RESOURCE_URI = "baritone://guidance/build-plan"
BUILDSITE_RESOURCE_URI = "baritone://guidance/buildsite"

WORKFLOW_GUIDE_ALIAS = "workflow"
BUILD_PLAN_GUIDE_ALIAS = "build-plan"
BUILDSITE_GUIDE_ALIAS = "buildsite"


SURVIVAL_FIRST_GUIDES: Mapping[str, str] = {
    WORKFLOW_TOPIC: """Model-agnostic survival-first workflow guidance.
- Start with low-risk reconnaissance and inventory readiness before any build action.
- Preserve safety: avoid hazards, check terrain, and validate fallback paths.
- Plan for material, tool, and time constraints so execution can fail gracefully.
- Do not proceed to world mutation until explicit previews are reviewed.""",
    BUILD_PLAN_TOPIC: """Model-agnostic survival-first build-plan guidance (buildplan).
- Produce a deterministic build sequence first, then request a full preview.
- Keep plans conservative: least invasive materials first, then structural fills.
- Preserve your current resources and avoid irreversible operations before preview approval.""",
    BUILDSITE_TOPIC: """Model-agnostic survival-first buildsite guidance.
- Inspect terrain, height variation, biomes, and access routes.
- Avoid build plans that depend on fragile blocks unless a replacement plan exists.
- Prefer stable ground and safe re-entry/egress before committing actions.""",
}


_AGENT_GUIDE_ALIASES = {
    "workflow": WORKFLOW_TOPIC,
    "workflows": WORKFLOW_TOPIC,
    "agent-workflow": WORKFLOW_TOPIC,
    "agent_workflow": WORKFLOW_TOPIC,
    "guide-workflow": WORKFLOW_TOPIC,
    "buildplan": BUILD_PLAN_TOPIC,
    "build-plan": BUILD_PLAN_TOPIC,
    "build_plan": BUILD_PLAN_TOPIC,
    "build-plans": BUILD_PLAN_TOPIC,
    "build-sites": BUILDSITE_TOPIC,
    "build-site": BUILDSITE_TOPIC,
    "build_site": BUILDSITE_TOPIC,
    "buildsite": BUILDSITE_TOPIC,
    "site": BUILDSITE_TOPIC,
}


def _normalize(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.strip().lower())
    normalized = re.sub(r"-+", "-", normalized).strip("-")
    return normalized


@dataclass(frozen=True)
class ToolDescription:
    name: str
    aliases: List[str]
    description: str
    required_inputs: List[str]
    examples: List[str]
    safety: Mapping[str, str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "aliases": list(self.aliases),
            "description": self.description,
            "required_inputs": list(self.required_inputs),
            "examples": list(self.examples),
            "safety": dict(self.safety),
        }


_TOOL_DESCRIPTIONS: Mapping[str, ToolDescription] = {
    "inspect_build_site": ToolDescription(
        name="inspect_build_site",
        aliases=["inspect_build_site", "minecraft_inspect_build_site"],
        description=(
            "Inspect a candidate build location and terrain context before planning."
        ),
        required_inputs=["area_bounds", "reference_coordinates", "terrain_constraints"],
        examples=[
            "minecraft_inspect_build_site area_bounds=(x1,y1,z1,x2,y2,z2)",
            "inspect_build_site reference_coordinates=(x,y,z)",
        ],
        safety={
            "mutation": "No world mutation in inspection phase.",
            "execution": "Preview should gate all world-writing actions.",
        },
    ),
    "preview_build_plan": ToolDescription(
        name="preview_build_plan",
        aliases=["preview_build_plan", "minecraft_preview_build_plan"],
        description=(
            "Generate a full build plan preview and risk analysis before committing "
            "world edits."
        ),
        required_inputs=["schematic", "build_site", "material_inventory", "risk_checks"],
        examples=[
            "minecraft_preview_build_plan schematic='modern_house' build_site=(x,y,z)",
            "preview_build_plan schematic='compact_farm' allow_surface_only=true",
        ],
        safety={
            "mutation": "No world mutation before explicit execute confirmation.",
            "execution": "Execute only after the preview is accepted.",
        },
    ),
}

_FALLBACK_TOOL_DESCRIPTION = ToolDescription(
    name="unknown_tool",
    aliases=[],
    description=(
        "Unknown tool request. Use inspect_build_site and preview_build_plan for "
        "safe reconnaissance and plan review before execution."
    ),
    required_inputs=[],
    examples=[
        "Use inspect_build_plan to validate context first.",
        "Use preview_build_plan to produce a no-mutation dry-run plan.",
    ],
    safety={
        "mutation": "No world mutation by default.",
        "execution": "Confirm a preview before executing build actions.",
    },
)

_TOOL_ALIAS_INDEX: Mapping[str, str] = {
    canonical: canonical for canonical in _TOOL_DESCRIPTIONS
}
for canonical, desc in _TOOL_DESCRIPTIONS.items():
    for alias in desc.aliases:
        _TOOL_ALIAS_INDEX[_normalize(alias)] = canonical
    _TOOL_ALIAS_INDEX[_normalize(canonical)] = canonical


def get_agent_guide(topic: Optional[str]) -> str:
    """
    Return the survival-first agent guide for a topic alias, with fallback.
    """

    if topic is None:
        return SURVIVAL_FIRST_GUIDES[WORKFLOW_TOPIC]
    key = _normalize(topic)
    canonical_topic = _AGENT_GUIDE_ALIASES.get(key, WORKFLOW_TOPIC)
    return SURVIVAL_FIRST_GUIDES[canonical_topic]


def get_tool_description(name: str) -> Dict[str, Any]:
    """
    Return structured tool guidance with stable keys and fallback handling.
    """

    normalized = _normalize(name)
    if normalized.startswith("minecraft-"):
        normalized = normalized.removeprefix("minecraft-")
    canonical = _TOOL_ALIAS_INDEX.get(normalized)
    if canonical is not None:
        data = _TOOL_DESCRIPTIONS[canonical]
        payload = data.to_dict()
        payload["canonical_name"] = canonical
        payload["safe_by_default"] = True
        return payload

    fallback = _FALLBACK_TOOL_DESCRIPTION.to_dict()
    fallback["canonical_name"] = "unknown_tool"
    fallback["safe_by_default"] = True
    return fallback


def _build_prompt(base: str, task: Optional[str]) -> str:
    task_line = ""
    if task:
        task_clean = task.strip()
        if task_clean:
            task_line = f"\nTask: {task_clean}\n"
    return (
        "Safety mode: no world mutation until explicit confirmation.\n"
        "Protocol: preview before execute.\n"
        f"{task_line}"
        f"{base}"
    )


def build_workflow_prompt(task: Optional[str] = None) -> str:
    return _build_prompt(
        get_agent_guide(WORKFLOW_TOPIC),
        task,
    )


def build_build_plan_prompt(task: Optional[str] = None) -> str:
    return _build_prompt(
        get_agent_guide(BUILD_PLAN_TOPIC),
        task,
    )


def build_buildsite_prompt(task: Optional[str] = None) -> str:
    return _build_prompt(
        get_agent_guide(BUILDSITE_TOPIC),
        task,
    )


__all__ = [
    "WORKFLOW_TOPIC",
    "BUILD_PLAN_TOPIC",
    "BUILDSITE_TOPIC",
    "WORKFLOW_RESOURCE_URI",
    "BUILD_PLAN_RESOURCE_URI",
    "BUILDSITE_RESOURCE_URI",
    "WORKFLOW_GUIDE_ALIAS",
    "BUILD_PLAN_GUIDE_ALIAS",
    "BUILDSITE_GUIDE_ALIAS",
    "SURVIVAL_FIRST_GUIDES",
    "ToolDescription",
    "get_agent_guide",
    "get_tool_description",
    "build_workflow_prompt",
    "build_build_plan_prompt",
    "build_buildsite_prompt",
]
