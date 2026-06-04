from __future__ import annotations

from dataclasses import dataclass

from app.agent.session import Session
from app.agent.tools import Tool


@dataclass(slots=True)
class Skill:
    name: str
    description: str
    keywords: tuple[str, ...]

# 新的skill在这里注册路由
class SkillRouter:
    def __init__(self, tools: list[Tool], history_window_messages: int = 8) -> None:
        self.tools = tools
        self.history_window_messages = history_window_messages
        self._tool_by_name = {tool.name: tool for tool in tools}
        self.skills = [
            Skill(
                name="workspace",
                description="Inspect and edit workspace files and directories.",
                keywords=(
                    "file",
                    "read",
                    "write",
                    "edit",
                    "create",
                    "directory",
                    "folder",
                    "代码",
                    "文件",
                    "目录",
                    "创建",
                    "修改",
                    ".py",
                    ".md",
                    ".json",
                ),
            ),
            Skill(
                name="shell",
                description="Run shell commands for testing, execution, and diagnostics.",
                keywords=(
                    "shell",
                    "command",
                    "run",
                    "test",
                    "pytest",
                    "python -m",
                    "npm",
                    "uv",
                    "执行",
                    "命令",
                    "运行",
                    "测试",
                ),
            ),
        ]

    def select_tools(self, session: Session, user_input: str) -> tuple[list[Skill], list[Tool]]:
        # active_skills save refer skill's name. Two func's merge result
        active_skills = self._infer_from_input(user_input) | self._infer_from_history(session)
        # 默认加入workspace本地文件操作的skill
        if not active_skills:
            active_skills = {"workspace"}

        selected_skills = [skill for skill in self.skills if skill.name in active_skills]
        selected_tools = [tool for tool in self.tools if tool.skill_name in active_skills]
        session.metadata["selected_skills"] = [skill.name for skill in selected_skills]
        return selected_skills, selected_tools

    def _infer_from_input(self, user_input: str) -> set[str]:
        text = user_input.lower()
        # set存储refer active skill
        active: set[str] = set()
        for skill in self.skills:
            if any(keyword.lower() in text for keyword in skill.keywords):
                active.add(skill.name)
        return active

    def _infer_from_history(self, session: Session) -> set[str]:
        active: set[str] = set()
        # 从最近的Windows_count = 8条Msg中找相关性
        for message in session.messages[-self.history_window_messages :]:
            # (role:tool)tool-result has filed name : ls , read_file... 
            if message.name:
                tool = self._tool_by_name.get(message.name)
                if tool:
                    active.add(tool.skill_name)
            # Assisatnt's name in filed tool-calls
            for tool_call in message.tool_calls:
                tool = self._tool_by_name.get(tool_call.name)
                if tool:
                    active.add(tool.skill_name)
        return active
