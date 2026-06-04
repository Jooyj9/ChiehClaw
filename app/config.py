from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from app.utils.env import load_dotenv_if_exists


def env_flag(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


@dataclass(slots=True)
class Settings:
    model_provider: str
    model_name: str
    openai_base_url: str
    openai_api_key: str | None
    data_dir: Path
    sessions_dir: Path
    logs_dir: Path
    files_dir: Path
    workspace_root_dir: Path
    project_memory_file: Path | None = None
    project_memory_max_chars: int = 4000
    max_tool_rounds: int = 6
    shell_timeout_seconds: int = 30
    shell_execution_mode: str = "local"
    shell_sandbox_image: str = "xxxclaw-sandbox:latest"
    shell_docker_binary: str = "docker"
    shell_docker_network: str = ""
    shell_docker_memory: str = "512m"
    shell_docker_cpus: str = "1"
    llm_timeout_seconds: int = 180
    llm_max_retries: int = 1
    context_max_tokens: int = 32000
    context_recent_tokens: int = 20000
    context_older_memory_tokens: int = 5000
    tool_result_max_chars: int = 4000
    assistant_reasoning_max_chars: int = 2000
    skill_history_window_messages: int = 8
    event_log_retained_completed_turns: int = 200
    feature_runtime_context: bool = True
    feature_older_memory: bool = True
    feature_recovery: bool = True
    feature_workspace_isolation: bool = True

    def ensure_directories(self) -> None:
        for path in (
            self.data_dir,
            self.sessions_dir,
            self.logs_dir,
            self.files_dir,
            self.workspace_root_dir,
            self.events_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)

    @property
    def events_dir(self) -> Path:
        return (self.data_dir / "events").resolve()

    @classmethod
    def from_env(cls, base_dir: Path | None = None) -> "Settings":
        root = (base_dir or Path(__file__).resolve().parents[1]).resolve()
        load_dotenv_if_exists(root)
        data_dir = Path(os.getenv("XXXCLAW_DATA_DIR", root / "data")).resolve()
        workspace_root_dir = Path(os.getenv("XXXCLAW_WORKSPACE_ROOT_DIR", root.parent / "xxxclaw-workspace")).resolve()
        raw_project_memory = os.getenv("XXXCLAW_PROJECT_MEMORY_FILE", "XAGENTS.md").strip()
        project_memory_file = None
        if raw_project_memory:
            project_memory_file = Path(raw_project_memory)
            if not project_memory_file.is_absolute():
                project_memory_file = root / project_memory_file
            project_memory_file = project_memory_file.resolve()
        provider = os.getenv("XXXCLAW_MODEL_PROVIDER")
        api_key = os.getenv("OPENAI_API_KEY") or os.getenv("MOONSHOT_API_KEY")

        if not provider:
            provider = "openai" if api_key else "mock"

        return cls(
            model_provider=provider,
            model_name=os.getenv("XXXCLAW_MODEL_NAME", "kimi-k2.6"),
            openai_base_url=os.getenv(
                "OPENAI_BASE_URL",
                "https://api.moonshot.cn/v1/chat/completions",
            ),
            openai_api_key=api_key,
            data_dir=data_dir,
            sessions_dir=(data_dir / "sessions").resolve(),
            logs_dir=(data_dir / "logs").resolve(),
            files_dir=(data_dir / "files").resolve(),
            workspace_root_dir=workspace_root_dir,
            project_memory_file=project_memory_file,
            project_memory_max_chars=int(os.getenv("XXXCLAW_PROJECT_MEMORY_MAX_CHARS", "4000")),
            max_tool_rounds=int(os.getenv("XXXCLAW_MAX_TOOL_ROUNDS", "6")),
            shell_timeout_seconds=int(os.getenv("XXXCLAW_SHELL_TIMEOUT_SECONDS", "30")),
            shell_execution_mode=os.getenv("XXXCLAW_SHELL_EXECUTION_MODE", "local").strip().lower(),
            shell_sandbox_image=os.getenv("XXXCLAW_SHELL_SANDBOX_IMAGE", "xxxclaw-sandbox:latest"),
            shell_docker_binary=os.getenv("XXXCLAW_SHELL_DOCKER_BINARY", "docker"),
            shell_docker_network=os.getenv("XXXCLAW_SHELL_DOCKER_NETWORK", ""),
            shell_docker_memory=os.getenv("XXXCLAW_SHELL_DOCKER_MEMORY", "512m"),
            shell_docker_cpus=os.getenv("XXXCLAW_SHELL_DOCKER_CPUS", "1"),
            llm_timeout_seconds=int(os.getenv("XXXCLAW_LLM_TIMEOUT_SECONDS", "180")),
            llm_max_retries=int(os.getenv("XXXCLAW_LLM_MAX_RETRIES", "1")),
            context_max_tokens=int(os.getenv("XXXCLAW_CONTEXT_MAX_TOKENS", "32000")),
            context_recent_tokens=int(os.getenv("XXXCLAW_CONTEXT_RECENT_TOKENS", "20000")),
            context_older_memory_tokens=int(os.getenv("XXXCLAW_CONTEXT_OLDER_MEMORY_TOKENS", "5000")),
            tool_result_max_chars=int(os.getenv("XXXCLAW_TOOL_RESULT_MAX_CHARS", "4000")),
            assistant_reasoning_max_chars=int(os.getenv("XXXCLAW_ASSISTANT_REASONING_MAX_CHARS", "2000")),
            skill_history_window_messages=int(os.getenv("XXXCLAW_SKILL_HISTORY_WINDOW_MESSAGES", "8")),
            event_log_retained_completed_turns=int(os.getenv("XXXCLAW_EVENT_LOG_RETAINED_COMPLETED_TURNS", "200")),
            feature_runtime_context=env_flag("XXXCLAW_FEATURE_RUNTIME_CONTEXT", True),
            feature_older_memory=env_flag("XXXCLAW_FEATURE_OLDER_MEMORY", True),
            feature_recovery=env_flag("XXXCLAW_FEATURE_RECOVERY", True),
            feature_workspace_isolation=env_flag("XXXCLAW_FEATURE_WORKSPACE_ISOLATION", True),
        )


def load_settings(base_dir: Path | None = None) -> Settings:
    settings = Settings.from_env(base_dir=base_dir)
    settings.ensure_directories()
    return settings
