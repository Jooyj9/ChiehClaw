from __future__ import annotations

import uuid
from pathlib import Path

from app.ai.client_mock import MockLLMClient
from app.ai.client_openai import OpenAICompatibleClient
from app.ai.message import AssistantMessage, ToolCall
from app.ai.models import ModelConfig
from app.agent.loop import AgentLoop
from app.agent.session import Session
from app.agent.skills import SkillRouter
from app.agent.tool_executor import ToolExecutor
from app.agent.tools import Tool, build_default_tools
from app.config import Settings, load_settings
from app.runtime.context_manager import ContextManager
from app.runtime.event_log import EventLog, TurnEventSink
from app.runtime.prompt_builder import build_system_prompt
from app.runtime.project_memory import load_project_memory
from app.runtime.recovery import RecoveryManager, UnfinishedTurn
from app.runtime.workspace_context import use_workspace_dir
from app.schemas import TurnResult
from app.storage.naming import conversation_storage_name
from app.storage.session_store import JsonSessionStore
from app.storage.session_tree_store import JsonSessionTreeStore
from app.utils.logger import append_jsonl


class Runner:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.tools: list[Tool] = build_default_tools(settings)
        self.skill_router = SkillRouter(
            self.tools,
            history_window_messages=settings.skill_history_window_messages,
        )
        self.context_manager = ContextManager(
            max_tokens=settings.context_max_tokens,
            recent_tokens=settings.context_recent_tokens,
            older_memory_tokens=settings.context_older_memory_tokens,
            tool_result_max_chars=settings.tool_result_max_chars,
            assistant_reasoning_max_chars=settings.assistant_reasoning_max_chars,
            enable_runtime_context=settings.feature_runtime_context,
            enable_older_memory=settings.feature_older_memory,
        )
        self.session_store = JsonSessionStore(settings.sessions_dir)
        self.session_tree_store = JsonSessionTreeStore(settings.session_trees_dir)
        self.event_log = EventLog(settings.events_dir)
        self.recovery_manager = RecoveryManager(self.event_log)
        self.llm_client = self._build_llm_client()
        self.agent_loop = AgentLoop(
            llm_client=self.llm_client,
            tool_executor=ToolExecutor(
                self.tools,
                workspace_root_dir=settings.workspace_root_dir,
                audit_log_path=settings.logs_dir / "tool_audit.jsonl",
                max_result_chars=settings.tool_result_max_chars,
            ),
            max_rounds=settings.max_tool_rounds,
        )
        self._tools_by_name = {tool.name: tool for tool in self.tools}

    @classmethod
    def from_env(cls) -> "Runner":
        return cls(load_settings())

    def run_turn(self, conversation_id: str, user_input: str) -> TurnResult:
        return self._dispatch_request(workspace_id=conversation_id, user_input=user_input)

    def run_turn_stream(
        self,
        conversation_id: str,
        user_input: str,
        on_content_delta=None,
        on_reasoning_delta=None,
        on_tool_call_delta=None,
    ) -> TurnResult:
        return self._dispatch_request(
            workspace_id=conversation_id,
            user_input=user_input,
            on_content_delta=on_content_delta,
            on_reasoning_delta=on_reasoning_delta,
            on_tool_call_delta=on_tool_call_delta,
        )

    def scan_unfinished_tasks(self) -> list[UnfinishedTurn]:
        if not self.settings.feature_recovery:
            return []
        return self.recovery_manager.scan_unfinished()

    def recover_unfinished_tasks(self) -> list[UnfinishedTurn]:
        if not self.settings.feature_recovery:
            return []
        unfinished_turns = self.scan_unfinished_tasks()
        for unfinished in unfinished_turns:
            self._recover_turn(unfinished)
        return unfinished_turns

    def _recover_turn(self, unfinished: UnfinishedTurn) -> None:
        session = self.session_store.load(conversation_id=unfinished.conversation_id, system_prompt="")
        workspace_id = str(session.metadata.get("workspace_id") or unfinished.conversation_id)
        workspace_dir = self._workspace_for_conversation(workspace_id)
        with use_workspace_dir(workspace_dir):
            self._recover_turn_in_workspace(unfinished=unfinished, workspace_dir=workspace_dir)

    def _recover_turn_in_workspace(self, unfinished: UnfinishedTurn, workspace_dir: Path) -> None:
        if not unfinished.interrupted_tools:
            return

        session = self.session_store.load(conversation_id=unfinished.conversation_id, system_prompt="")
        session.metadata["workspace_dir"] = str(workspace_dir)
        session.metadata["shell_execution_mode"] = self.settings.shell_execution_mode
        self._attach_project_memory(session)
        recovered_count = 0
        skipped_count = 0

        for interrupted in unfinished.interrupted_tools:
            tool = self._tools_by_name.get(interrupted.tool_name)
            if tool and tool.recoverable:
                tool_call = ToolCall(
                    id=interrupted.tool_call_id,
                    name=interrupted.tool_name,
                    arguments=interrupted.arguments,
                )
                result = self.agent_loop.tool_executor.execute(tool_call)
                event_type = "tool_call_succeeded" if result.get("ok") else "tool_call_failed"
                self.event_log.append(
                    unfinished.conversation_id,
                    {
                        "turn_id": unfinished.turn_id,
                        "type": event_type,
                        "tool_call_id": interrupted.tool_call_id,
                        "tool_name": interrupted.tool_name,
                        "arguments": interrupted.arguments,
                        "result": result,
                        "recovered": True,
                    },
                )
                session.add_tool_result(
                    tool_call_id=interrupted.tool_call_id,
                    tool_name=interrupted.tool_name,
                    content=self.agent_loop.tool_executor.serialize_result(result),
                )
                self.event_log.append(
                    unfinished.conversation_id,
                    {
                        "turn_id": unfinished.turn_id,
                        "type": "tool_result_appended",
                        "tool_call_id": interrupted.tool_call_id,
                        "tool_name": interrupted.tool_name,
                        "ok": bool(result.get("ok")),
                        "recovered": True,
                    },
                )
                recovered_count += 1
                continue

            notice = (
                f"Previous turn was interrupted while running tool `{interrupted.tool_name}`.\n"
                "It was not replayed automatically because this tool may have side effects.\n"
                "Please confirm whether to continue."
            )
            session.add_assistant_message(AssistantMessage(content=notice))
            self.event_log.append(
                unfinished.conversation_id,
                {
                    "turn_id": unfinished.turn_id,
                    "type": "tool_call_skipped",
                    "tool_call_id": interrupted.tool_call_id,
                    "tool_name": interrupted.tool_name,
                    "arguments": interrupted.arguments,
                    "reason": "non_recoverable_tool",
                },
            )
            skipped_count += 1

        self.context_manager.ensure_message_token_counts(session.messages)
        self.session_store.save(session)
        self.event_log.append(
            unfinished.conversation_id,
            {
                "turn_id": unfinished.turn_id,
                "type": "turn_recovered",
                "recovered_tool_count": recovered_count,
                "skipped_tool_count": skipped_count,
            },
        )
        self._compact_event_log(unfinished.conversation_id)

    def _dispatch_request(
        self,
        workspace_id: str,
        user_input: str,
        on_content_delta=None,
        on_reasoning_delta=None,
        on_tool_call_delta=None,
    ) -> TurnResult:
        command_result = self._handle_session_command(workspace_id=workspace_id, user_input=user_input)
        if command_result is not None:
            return command_result

        tree = self.session_tree_store.get_or_create(workspace_id)
        session_id = tree.active_session_id
        workspace_dir = self._workspace_for_conversation(workspace_id)
        with use_workspace_dir(workspace_dir):
            return self._run_turn_in_workspace(
                session_id=session_id,
                workspace_id=workspace_id,
                user_input=user_input,
                workspace_dir=workspace_dir,
                on_content_delta=on_content_delta,
                on_reasoning_delta=on_reasoning_delta,
                on_tool_call_delta=on_tool_call_delta,
            )

    def _run_turn_in_workspace(
        self,
        session_id: str,
        workspace_id: str,
        user_input: str,
        workspace_dir: Path,
        on_content_delta=None,
        on_reasoning_delta=None,
        on_tool_call_delta=None,
    ) -> TurnResult:
        turn_id = str(uuid.uuid4())
        event_sink = TurnEventSink(self.event_log, conversation_id=session_id, turn_id=turn_id)
        session = self.session_store.load(conversation_id=session_id, system_prompt="")
        session.metadata["workspace_id"] = workspace_id
        session.metadata["workspace_dir"] = str(workspace_dir)
        session.metadata["shell_execution_mode"] = self.settings.shell_execution_mode
        self._attach_project_memory(session)
        event_sink.emit("turn_started")
        active_skills, active_tools = self.skill_router.select_tools(session=session, user_input=user_input)
        session.system_prompt = build_system_prompt(active_tools, active_skills)
        session.add_user_message(user_input)
        event_sink.emit("user_message_appended", content_chars=len(user_input))
        self.context_manager.update_pinned_context(session)
        self.context_manager.ensure_message_token_counts(session.messages)

        final_status = "failed"
        try:
            assistant_message = self.agent_loop.run(
                session=session,
                tools=active_tools,
                message_builder=self.context_manager.build_messages,
                event_sink=event_sink,
                on_content_delta=on_content_delta,
                on_reasoning_delta=on_reasoning_delta,
                on_tool_call_delta=on_tool_call_delta,
            )
            event_sink.emit("turn_succeeded", reply_chars=len(assistant_message.content))
            final_status = "succeeded"
        except Exception as exc:
            event_sink.emit("turn_failed", error=str(exc))
            final_status = "failed"
            raise
        finally:
            self.context_manager.ensure_message_token_counts(session.messages)
            self.session_store.save(session)
            self.session_tree_store.touch(workspace_id, session_id, title=user_input)
            context_stats = session.metadata.get("context_stats", {})
            append_jsonl(
                self.settings.logs_dir / f"{conversation_storage_name(session_id)}.jsonl",
                {
                    "turn_id": turn_id,
                    "conversation_id": session_id,
                    "workspace_id": workspace_id,
                    "user_input": user_input,
                    "selected_skills": session.metadata.get("selected_skills", []),
                    "workspace_dir": str(workspace_dir),
                    "trimmed_message_count": context_stats.get("trimmed_message_count", 0),
                    "context_stats": context_stats,
                },
            )
            self._compact_event_log(session_id)

        return TurnResult(
            conversation_id=session_id,
            reply=assistant_message.content,
            messages=session.messages,
        )

    def _handle_session_command(self, workspace_id: str, user_input: str) -> TurnResult | None:
        parts = user_input.strip().split(maxsplit=1)
        command = parts[0].lower() if parts else ""
        argument = parts[1].strip() if len(parts) > 1 else ""
        if command not in {"/new", "/clear", "/resume"}:
            return None

        if command in {"/new", "/clear"}:
            if argument:
                return self._command_result(workspace_id, "用法：`/new` 或 `/clear`。")

            node = self.session_tree_store.create_session(workspace_id)
            workspace_dir = self._workspace_for_conversation(workspace_id)
            session = Session(conversation_id=node.session_id, system_prompt="")
            session.metadata["workspace_id"] = workspace_id
            session.metadata["workspace_dir"] = str(workspace_dir)
            session.metadata["shell_execution_mode"] = self.settings.shell_execution_mode
            self.session_store.save(session)
            return TurnResult(
                conversation_id=node.session_id,
                reply=f"已创建并切换到新会话。\nsession_id: `{node.session_id}`",
            )

        if not argument:
            active_session_id, sessions = self.session_tree_store.list_sessions(workspace_id)
            workspace_dir = self._workspace_for_conversation(workspace_id)
            lines = [f"当前工作区：`{workspace_dir}`", "可恢复会话："]
            for index, node in enumerate(sessions, start=1):
                marker = " [当前]" if node.session_id == active_session_id else ""
                lines.append(f"{index}. {node.title}{marker}")
                lines.append(f"   `{node.session_id}` · {node.updated_at}")
            lines.append("使用 `/resume <序号|session_id>` 切换。")
            return TurnResult(conversation_id=active_session_id, reply="\n".join(lines))

        selected = self.session_tree_store.switch_session(workspace_id, argument)
        if selected is None:
            return self._command_result(
                workspace_id,
                "未找到该会话。请先使用 `/resume` 查看当前工作区的会话列表。",
            )
        return TurnResult(
            conversation_id=selected.session_id,
            reply=f"已切换到会话：{selected.title}\nsession_id: `{selected.session_id}`",
        )

    def _command_result(self, workspace_id: str, reply: str) -> TurnResult:
        tree = self.session_tree_store.get_or_create(workspace_id)
        return TurnResult(conversation_id=tree.active_session_id, reply=reply)

    def _workspace_for_conversation(self, conversation_id: str) -> Path:
        workspace_dir = self.settings.workspace_root_dir
        if self.settings.feature_workspace_isolation:
            workspace_dir = workspace_dir / conversation_storage_name(conversation_id)
        workspace_dir.mkdir(parents=True, exist_ok=True)
        return workspace_dir.resolve()

    def _compact_event_log(self, conversation_id: str) -> None:
        self.event_log.compact_completed_turns(
            conversation_id=conversation_id,
            keep_completed_turns=self.settings.event_log_retained_completed_turns,
        )

    def _attach_project_memory(self, session: Session) -> None:
        project_memory = load_project_memory(
            self.settings.project_memory_file,
            max_chars=self.settings.project_memory_max_chars,
        )
        if project_memory:
            session.metadata["project_memory"] = project_memory
            session.metadata["project_memory_file"] = str(self.settings.project_memory_file)
            return

        session.metadata.pop("project_memory", None)
        session.metadata.pop("project_memory_file", None)

    def _build_llm_client(self):
        if self.settings.model_provider == "mock":
            return MockLLMClient()

        if self.settings.model_provider != "openai":
            raise ValueError(f"Unsupported model provider: {self.settings.model_provider}")
        if not self.settings.openai_api_key:
            raise ValueError("OPENAI_API_KEY is required when XXXCLAW_MODEL_PROVIDER=openai")

        return OpenAICompatibleClient(
            config=ModelConfig(provider="openai", model=self.settings.model_name),
            base_url=self.settings.openai_base_url,
            api_key=self.settings.openai_api_key,
            request_timeout_seconds=self.settings.llm_timeout_seconds,
            max_retries=self.settings.llm_max_retries,
        )
