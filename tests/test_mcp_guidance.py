from __future__ import annotations

from baritone_client.mcp.guidance import (
    build_build_plan_prompt,
    build_buildsite_prompt,
    get_agent_guide,
    get_tool_description,
    WORKFLOW_TOPIC,
)
import importlib


def test_get_agent_guide_aliases() -> None:
    assert "workflow" in get_agent_guide("workflow")
    assert "survival-first" in get_agent_guide("agent-workflow")
    assert "buildplan" in get_agent_guide("buildplan").lower()
    assert "build-plan" in get_agent_guide("build_plan")
    assert "buildsite" in get_agent_guide("build-site")
    assert "buildsite" in get_agent_guide("site")


def test_get_agent_guide_unknown_fallback() -> None:
    unknown = get_agent_guide("not-a-real-topic")
    default = get_agent_guide(WORKFLOW_TOPIC)
    assert unknown == default
    assert "safety" in unknown.lower()


def test_get_tool_description_inspect_aliases() -> None:
    base = get_tool_description("inspect_build_site")
    alias = get_tool_description("minecraft_inspect_build_site")
    assert base["canonical_name"] == "inspect_build_site"
    assert alias["canonical_name"] == "inspect_build_site"
    assert "minecraft_inspect_build_site" in base["aliases"]
    assert "No world mutation" in base["safety"]["mutation"]


def test_get_tool_description_preview_aliases() -> None:
    base = get_tool_description("preview_build_plan")
    alias = get_tool_description("minecraft_preview_build_plan")
    assert base["canonical_name"] == "preview_build_plan"
    assert alias["canonical_name"] == "preview_build_plan"
    assert "No world mutation before explicit execute confirmation." in base["safety"]["mutation"]
    assert "preview" in base["examples"][0].lower()


def test_get_tool_description_unknown() -> None:
    payload = get_tool_description("mystery_tool_for_tests")
    assert payload["canonical_name"] == "unknown_tool"
    assert payload["safe_by_default"] is True


def test_prompt_task_injection() -> None:
    task = "repair house shell and reinforce foundation"
    prompt = build_build_plan_prompt(task)
    site_prompt = build_buildsite_prompt(task)
    assert task in prompt
    assert task in site_prompt


def test_prompt_safety_language() -> None:
    prompt = build_build_plan_prompt("repair base")
    assert "no world mutation" in prompt.lower()
    assert "preview before execute" in prompt.lower()


def test_guidance_module_import_and_tool_alias_lookup() -> None:
    guidance = importlib.reload(__import__("baritone_client.mcp.guidance", fromlist=["*"]))
    inspect_tool = guidance.get_tool_description("minecraft_inspect_build_site")
    preview_tool = guidance.get_tool_description("minecraft-preview-build-plan")
    assert inspect_tool["canonical_name"] == "inspect_build_site"
    assert preview_tool["canonical_name"] == "preview_build_plan"
