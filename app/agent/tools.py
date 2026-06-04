from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from app.config import Settings
from app.utils.paths import resolve_workspace_path


ToolHandler = Callable[..., Any]


@dataclass(slots=True)
class Tool:
    name: str
    skill_name: str
    description: str
    parameters_schema: dict[str, Any]
    handler: ToolHandler
    idempotent: bool = False
    recoverable: bool = False

    def execute(self, **kwargs: Any) -> Any:
        return self.handler(**kwargs)

    # Core:compatible open-ai tool-schema
    def to_openai_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters_schema,
            },
        }


def build_default_tools(settings: Settings) -> list[Tool]:
    from app.runtime.workspace_context import get_workspace_dir

    workspace_root_dir = settings.workspace_root_dir
    timeout_seconds = settings.shell_timeout_seconds

    def current_workspace_dir() -> Path:
        workspace = get_workspace_dir(workspace_root_dir)
        workspace.mkdir(parents=True, exist_ok=True)
        return workspace

    def read_file(path: str) -> dict[str, Any]:
        target = resolve_workspace_path(current_workspace_dir(), path)
        return {"path": str(target), "content": target.read_text(encoding="utf-8")}

    def write_file(path: str, content: str) -> dict[str, Any]:
        target = resolve_workspace_path(current_workspace_dir(), path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return {"path": str(target), "bytes_written": len(content.encode("utf-8"))}

    def edit_file(path: str, old_text: str, new_text: str) -> dict[str, Any]:
        target = resolve_workspace_path(current_workspace_dir(), path)
        current = target.read_text(encoding="utf-8")
        if old_text not in current:
            raise ValueError("old_text was not found in target file")
        updated = current.replace(old_text, new_text, 1)
        target.write_text(updated, encoding="utf-8")
        return {"path": str(target), "updated": True}

    def list_path(path: str = ".") -> dict[str, Any]:
        target = resolve_workspace_path(current_workspace_dir(), path)
        entries = []
        for item in sorted(target.iterdir(), key=lambda current: (not current.is_dir(), current.name.lower())):
            entries.append(
                {
                    "name": item.name,
                    "path": str(item),
                    "is_dir": item.is_dir(),
                }
            )
        return {"path": str(target), "entries": entries}

    def run_shell(command: str, cwd: str = ".") -> dict[str, Any]:
        workspace_dir = current_workspace_dir()
        cwd_path = resolve_workspace_path(workspace_dir, cwd)
        if settings.shell_execution_mode == "docker":
            return run_shell_in_docker(command=command, workspace_dir=workspace_dir, cwd_path=cwd_path)
        if settings.shell_execution_mode != "local":
            raise ValueError(f"unsupported shell execution mode: {settings.shell_execution_mode}")

        completed = subprocess.run(
            command,
            cwd=str(cwd_path),
            capture_output=True,
            text=True,
            shell=True,
            timeout=timeout_seconds,
        )
        return {
            "execution_mode": "local",
            "cwd": str(cwd_path),
            "command": command,
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        }

    def run_shell_in_docker(command: str, workspace_dir: Path, cwd_path: Path) -> dict[str, Any]:
        relative_cwd = cwd_path.relative_to(workspace_dir)
        container_cwd = "/workspace"
        if str(relative_cwd) != ".":
            container_cwd = f"/workspace/{relative_cwd.as_posix()}"

        docker_command = [
            settings.shell_docker_binary,
            "run",
            "--rm",
            "-v",
            f"{workspace_dir}:/workspace",
            "-w",
            container_cwd,
        ]
        if settings.shell_docker_network:
            docker_command.extend(["--network", settings.shell_docker_network])
        if settings.shell_docker_memory:
            docker_command.extend(["--memory", settings.shell_docker_memory])
        if settings.shell_docker_cpus:
            docker_command.extend(["--cpus", settings.shell_docker_cpus])
        docker_command.extend([settings.shell_sandbox_image, "sh", "-lc", command])

        completed = subprocess.run(
            docker_command,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
        return {
            "execution_mode": "docker",
            "workspace": str(workspace_dir),
            "container_cwd": container_cwd,
            "sandbox_image": settings.shell_sandbox_image,
            "command": command,
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        }

    return [
        Tool(
            name="read_file",
            skill_name="workspace",
            description="Read a UTF-8 text file from the workspace.",
            parameters_schema={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
            handler=read_file,
            idempotent=True,
            recoverable=True,
        ),
        Tool(
            name="write_file",
            skill_name="workspace",
            description="Write a UTF-8 text file into the workspace, creating parent directories if needed.",
            parameters_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
            handler=write_file,
        ),
        Tool(
            name="edit_file",
            skill_name="workspace",
            description="Replace the first occurrence of old_text in a UTF-8 file.",
            parameters_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_text": {"type": "string"},
                    "new_text": {"type": "string"},
                },
                "required": ["path", "old_text", "new_text"],
            },
            handler=edit_file,
        ),
        Tool(
            name="ls",
            skill_name="workspace",
            description="List files and directories under a workspace path.",
            parameters_schema={
                "type": "object",
                "properties": {"path": {"type": "string", "default": "."}},
            },
            handler=list_path,
            idempotent=True,
            recoverable=True,
        ),
        Tool(
            name="run_shell",
            skill_name="shell",
            description="Run a shell command inside the workspace and return stdout, stderr, and exit code.",
            parameters_schema={
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "cwd": {"type": "string", "default": "."},
                },
                "required": ["command"],
            },
            handler=run_shell,
        ),
    ]
