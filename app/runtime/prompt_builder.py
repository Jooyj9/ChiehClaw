from __future__ import annotations

from app.agent.skills import Skill
from app.agent.tools import Tool


def build_system_prompt(tools: list[Tool], skills: list[Skill] | None = None) -> str:
    tool_lines = "\n".join(f"- {tool.name}: {tool.description}" for tool in tools)
    skill_lines = "\n".join(f"- {skill.name}: {skill.description}" for skill in skills or [])
    tools_section = tool_lines or "- No external tools are active for this turn."
    skills_section = skill_lines or "- No skill bundle is active for this turn."
    return (
        "You are xxxclaw, a minimal coding agent.\n"
        "Decide when to call tools, do not assume tool results, and keep answers concise.\n"
        "When a tool is needed, call the most specific one and continue after tool results.\n"
        "Active skills:\n"
        f"{skills_section}\n"
        "Available tools for this turn:\n"
        f"{tools_section}"
    )
