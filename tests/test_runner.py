from __future__ import annotations

import shutil
import unittest
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.agent.tool_executor import ToolExecutor
from app.agent.loop import AgentLoop
from app.agent.tools import Tool, build_default_tools
from app.agent.session import Session
from app.agent.skills import SkillRouter
from app.ai.client_openai import OpenAICompatibleClient
from app.ai.message import AssistantMessage, ChatMessage, ToolCall
from app.ai.models import ModelConfig
from app.config import Settings
from app.runtime.context_manager import ContextManager
from app.runtime.event_log import EventLog
from app.runtime.recovery import RecoveryManager
from app.runtime.runner import Runner
from app.runtime.token_counter import TokenCounter
from app.runtime.workspace_context import use_workspace_dir
from app.storage.naming import conversation_storage_name


class RunnerTestCase(unittest.TestCase):
    def make_settings(self, root: Path) -> Settings:
        return Settings(
            model_provider="mock",
            model_name="mock",
            openai_base_url="",
            openai_api_key=None,
            data_dir=root / "data",
            sessions_dir=root / "data" / "sessions",
            logs_dir=root / "data" / "logs",
            files_dir=root / "data" / "files",
            workspace_root_dir=root / "workspaces",
        )

    def make_workspace(self, settings: Settings, conversation_id: str) -> Path:
        workspace = settings.workspace_root_dir / conversation_storage_name(conversation_id)
        workspace.mkdir(parents=True, exist_ok=True)
        return workspace

    def test_mock_runner_persists_and_uses_tools(self) -> None:
        root = Path.cwd() / "data" / "test-runner-case"
        shutil.rmtree(root, ignore_errors=True)
        root.mkdir(parents=True, exist_ok=True)
        try:
            settings = self.make_settings(root)
            settings.ensure_directories()
            self.make_workspace(settings, "case-1").joinpath("sample.txt").write_text("hello", encoding="utf-8")

            runner = Runner(settings)
            result = runner.run_turn("case-1", "read sample.txt")

            self.assertIn("[mock] Tool result:", result.reply)
            self.assertIn("sample.txt", result.reply)
            self.assertTrue((settings.sessions_dir / "case-1.json").exists())
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_runner_loads_project_memory_file_each_turn(self) -> None:
        root = Path.cwd() / "data" / "test-runner-project-memory"
        shutil.rmtree(root, ignore_errors=True)
        root.mkdir(parents=True, exist_ok=True)
        try:
            settings = self.make_settings(root)
            settings.project_memory_file = root / "XAGENTS.md"
            settings.project_memory_file.write_text("Always mention project-memory-demo.", encoding="utf-8")
            settings.ensure_directories()

            runner = Runner(settings)
            runner.run_turn("memory-case", "hello")
            session = runner.session_store.load("memory-case", "")

            self.assertIn("project-memory-demo", session.metadata["project_memory"])
            self.assertEqual(session.metadata["project_memory_file"], str(settings.project_memory_file))
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_runner_uses_isolated_workspace_per_conversation(self) -> None:
        root = Path.cwd() / "data" / "test-runner-workspace-isolation"
        shutil.rmtree(root, ignore_errors=True)
        root.mkdir(parents=True, exist_ok=True)
        try:
            settings = self.make_settings(root)
            settings.ensure_directories()
            runner = Runner(settings)

            runner.run_turn("case-a", "shell echo A > marker.txt")
            runner.run_turn("case-b", "shell echo B > marker.txt")

            workspace_a = self.make_workspace(settings, "case-a")
            workspace_b = self.make_workspace(settings, "case-b")
            self.assertIn("A", (workspace_a / "marker.txt").read_text(encoding="utf-8"))
            self.assertIn("B", (workspace_b / "marker.txt").read_text(encoding="utf-8"))
            self.assertNotEqual(workspace_a, workspace_b)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_runner_creates_and_resumes_sessions_in_same_workspace(self) -> None:
        root = Path.cwd() / "data" / "test-runner-session-tree"
        shutil.rmtree(root, ignore_errors=True)
        root.mkdir(parents=True, exist_ok=True)
        try:
            settings = self.make_settings(root)
            settings.ensure_directories()
            runner = Runner(settings)
            workspace_id = "feishu-p2p-session-tree"

            first_result = runner.run_turn(workspace_id, "shell echo shared > marker.txt")
            first_session_id = first_result.conversation_id
            new_result = runner.run_turn(workspace_id, "/new")
            second_session_id = new_result.conversation_id

            self.assertNotEqual(first_session_id, second_session_id)
            self.assertEqual(runner.session_store.load(second_session_id, "").messages, [])
            self.assertFalse((settings.workspace_root_dir / conversation_storage_name(second_session_id)).exists())

            runner.run_turn(workspace_id, "read marker.txt")
            second_session = runner.session_store.load(second_session_id, "")
            self.assertIn("shared", second_session.messages[-2].content)
            self.assertNotIn("/new", [message.content for message in second_session.messages])

            list_result = runner.run_turn(workspace_id, "/resume")
            self.assertIn(first_session_id, list_result.reply)
            self.assertIn(second_session_id, list_result.reply)
            self.assertNotIn("/resume", [message.content for message in second_session.messages])

            switch_result = runner.run_turn(workspace_id, "/resume 2")
            self.assertEqual(switch_result.conversation_id, first_session_id)
            resumed_result = runner.run_turn(workspace_id, "hello again")
            self.assertEqual(resumed_result.conversation_id, first_session_id)
            self.assertIn("hello again", [message.content for message in resumed_result.messages])

            other_session_id = runner.run_turn("other-workspace", "/new").conversation_id
            denied_result = runner.run_turn(workspace_id, f"/resume {other_session_id}")
            self.assertEqual(denied_result.conversation_id, first_session_id)
            self.assertIn("未找到", denied_result.reply)

            restarted_runner = Runner(settings)
            restarted_result = restarted_runner.run_turn(workspace_id, "after restart")
            self.assertEqual(restarted_result.conversation_id, first_session_id)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_session_commands_do_not_emit_stream_content(self) -> None:
        root = Path.cwd() / "data" / "test-runner-session-command-stream"
        shutil.rmtree(root, ignore_errors=True)
        root.mkdir(parents=True, exist_ok=True)
        try:
            settings = self.make_settings(root)
            settings.ensure_directories()
            runner = Runner(settings)
            chunks: list[str] = []

            result = runner.run_turn_stream("stream-command", "/new", on_content_delta=chunks.append)

            self.assertEqual(chunks, [])
            self.assertEqual(runner.session_store.load(result.conversation_id, "").messages, [])
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_new_session_recovery_uses_parent_workspace(self) -> None:
        root = Path.cwd() / "data" / "test-runner-session-recovery-workspace"
        shutil.rmtree(root, ignore_errors=True)
        root.mkdir(parents=True, exist_ok=True)
        try:
            settings = self.make_settings(root)
            settings.ensure_directories()
            workspace_id = "recovery-workspace"
            self.make_workspace(settings, workspace_id).joinpath("sample.txt").write_text("shared", encoding="utf-8")
            runner = Runner(settings)
            session_id = runner.run_turn(workspace_id, "/new").conversation_id
            runner.event_log.append(session_id, {"turn_id": "turn-1", "type": "turn_started"})
            runner.event_log.append(
                session_id,
                {
                    "turn_id": "turn-1",
                    "type": "tool_call_started",
                    "tool_call_id": "call-read",
                    "tool_name": "read_file",
                    "arguments": {"path": "sample.txt"},
                },
            )

            restarted_runner = Runner(settings)
            restarted_runner.recover_unfinished_tasks()

            session = restarted_runner.session_store.load(session_id, "")
            self.assertTrue(any(message.role == "tool" and "shared" in message.content for message in session.messages))
            self.assertFalse((settings.workspace_root_dir / conversation_storage_name(session_id)).exists())
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_workspace_isolation_flag_can_use_shared_workspace_baseline(self) -> None:
        root = Path.cwd() / "data" / "test-runner-workspace-baseline"
        shutil.rmtree(root, ignore_errors=True)
        root.mkdir(parents=True, exist_ok=True)
        try:
            settings = self.make_settings(root)
            settings.feature_workspace_isolation = False
            settings.ensure_directories()
            runner = Runner(settings)

            runner.run_turn("case-a", "shell echo shared > marker.txt")
            runner.run_turn("case-b", "read marker.txt")

            self.assertTrue((settings.workspace_root_dir / "marker.txt").exists())
            self.assertFalse((settings.workspace_root_dir / "case-a").exists())
            self.assertIn("shared", runner.session_store.load("case-b", "").messages[-2].content)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_shell_tool_can_run_in_docker_sandbox(self) -> None:
        root = Path.cwd() / "data" / "test-shell-docker-sandbox"
        shutil.rmtree(root, ignore_errors=True)
        root.mkdir(parents=True, exist_ok=True)
        try:
            settings = self.make_settings(root)
            settings.shell_execution_mode = "docker"
            settings.shell_sandbox_image = "xxxclaw-sandbox:latest"
            settings.shell_docker_network = "none"
            settings.ensure_directories()
            workspace = self.make_workspace(settings, "docker-case")
            shell_tool = next(tool for tool in build_default_tools(settings) if tool.name == "run_shell")

            with patch("app.agent.tools.subprocess.run") as run_mock:
                run_mock.return_value = SimpleNamespace(returncode=0, stdout="ok\n", stderr="")
                with use_workspace_dir(workspace):
                    result = shell_tool.execute(command="echo ok", cwd=".")

            docker_command = run_mock.call_args.args[0]
            self.assertEqual(result["execution_mode"], "docker")
            self.assertEqual(docker_command[0], settings.shell_docker_binary)
            self.assertIn("--rm", docker_command)
            self.assertIn("--network", docker_command)
            self.assertIn(f"{workspace.resolve()}:/workspace", docker_command)
            self.assertEqual(docker_command[-4:], [settings.shell_sandbox_image, "sh", "-lc", "echo ok"])
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_runner_writes_tool_events(self) -> None:
        root = Path.cwd() / "data" / "test-runner-events"
        shutil.rmtree(root, ignore_errors=True)
        root.mkdir(parents=True, exist_ok=True)
        try:
            settings = self.make_settings(root)
            settings.ensure_directories()
            self.make_workspace(settings, "event-case").joinpath("sample.txt").write_text("hello", encoding="utf-8")

            runner = Runner(settings)
            runner.run_turn("event-case", "read sample.txt")

            events = EventLog(settings.events_dir).read("event-case")
            event_types = [event["type"] for event in events]
            self.assertIn("turn_started", event_types)
            self.assertIn("llm_call_started", event_types)
            self.assertIn("tool_call_started", event_types)
            self.assertIn("tool_call_succeeded", event_types)
            self.assertIn("tool_result_appended", event_types)
            self.assertIn("turn_succeeded", event_types)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_tool_executor_rejects_escaping_path_before_handler(self) -> None:
        root = Path.cwd() / "data" / "test-tool-executor-before-hook"
        shutil.rmtree(root, ignore_errors=True)
        root.mkdir(parents=True, exist_ok=True)
        try:
            workspace = root / "workspace"
            workspace.mkdir(parents=True, exist_ok=True)
            handler_called = False

            def read_file(path: str) -> dict[str, str]:
                nonlocal handler_called
                handler_called = True
                return {"path": path}

            executor = ToolExecutor(
                [
                    Tool(
                        name="read_file",
                        skill_name="workspace",
                        description="test",
                        parameters_schema={"type": "object"},
                        handler=read_file,
                    )
                ],
                workspace_root_dir=workspace,
            )

            with use_workspace_dir(workspace):
                result = executor.execute(ToolCall(id="call-escape", name="read_file", arguments={"path": "../x.txt"}))

            self.assertFalse(result["ok"])
            self.assertFalse(handler_called)
            self.assertIn("path escapes workspace", result["error"])
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_tool_executor_sanitizes_truncates_and_keeps_summary_out_of_result(self) -> None:
        root = Path.cwd() / "data" / "test-tool-executor-after-hook"
        shutil.rmtree(root, ignore_errors=True)
        root.mkdir(parents=True, exist_ok=True)
        try:
            audit_log_path = root / "logs" / "tool_audit.jsonl"
            long_result_log_path = root / "tool_result.jsonl"

            def run_shell() -> dict[str, str | int]:
                return {
                    "execution_mode": "local",
                    "returncode": 0,
                    "stdout": "OPENAI_API_KEY=secret-value\n" + ("x" * 2600),
                    "api_key": "secret-value",
                }

            executor = ToolExecutor(
                [
                    Tool(
                        name="run_shell",
                        skill_name="shell",
                        description="test",
                        parameters_schema={"type": "object"},
                        handler=run_shell,
                    )
                ],
                audit_log_path=audit_log_path,
                long_result_log_path=long_result_log_path,
                max_result_chars=600,
                max_field_chars=2000,
            )

            result = executor.execute(ToolCall(id="call-shell", name="run_shell", arguments={}))

            serialized = executor.serialize_result(result)
            audit_log = audit_log_path.read_text(encoding="utf-8")
            self.assertTrue(result["ok"])
            self.assertIn("tool_result_ref", result)
            self.assertNotIn("truncated", result)
            self.assertNotIn("summary", result)
            self.assertNotIn("audit", result)
            self.assertIn("stdout", result["result"])
            self.assertLessEqual(len(result["result"]["stdout"]), 2050)
            self.assertNotIn("execution_mode", result["result"])
            self.assertNotIn("returncode", result["result"])
            self.assertIn("[REDACTED]", serialized)
            self.assertIn("[truncated", serialized)
            self.assertNotIn("secret-value", serialized)
            self.assertIn("call-shell", audit_log)
            self.assertIn("stdout_chars", audit_log)
            self.assertIn("result_truncated", audit_log)
            self.assertNotIn("secret-value", audit_log)
            long_result_log = long_result_log_path.read_text(encoding="utf-8")
            self.assertIn(result["tool_result_ref"], long_result_log)
            self.assertIn("x" * 2200, long_result_log)
            self.assertNotIn("secret-value", long_result_log)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_tool_executor_keeps_read_file_content_without_summary_bloat(self) -> None:
        def read_file() -> dict[str, str]:
            return {"path": "sample.txt", "content": "hello from file"}

        executor = ToolExecutor(
            [
                Tool(
                    name="read_file",
                    skill_name="workspace",
                    description="test",
                    parameters_schema={"type": "object"},
                    handler=read_file,
                )
            ],
            max_result_chars=1200,
        )

        result = executor.execute(ToolCall(id="call-read", name="read_file", arguments={}))

        self.assertTrue(result["ok"])
        self.assertEqual(result["result"]["content"], "hello from file")
        self.assertNotIn("summary", result)

    def test_tool_executor_uses_50000_chars_as_single_result_gate(self) -> None:
        root = Path.cwd() / "data" / "test-tool-executor-single-gate"
        shutil.rmtree(root, ignore_errors=True)
        root.mkdir(parents=True, exist_ok=True)
        try:
            def read_file() -> dict[str, str]:
                return {"path": "large.txt", "content": "x" * 51000}

            executor = ToolExecutor(
                [
                    Tool(
                        name="read_file",
                        skill_name="workspace",
                        description="test",
                        parameters_schema={"type": "object"},
                        handler=read_file,
                    )
                ],
                long_result_log_path=root / "tool_result.jsonl",
            )

            result = executor.execute(ToolCall(id="call-large", name="read_file", arguments={}))

            self.assertIn("tool_result_ref", result)
            self.assertEqual(result["result"]["path"], "large.txt")
            self.assertLessEqual(len(result["result"]["content"]), 2050)
            self.assertIn("x" * 50000, (root / "tool_result.jsonl").read_text(encoding="utf-8"))
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_agent_loop_applies_turn_level_tool_result_budget(self) -> None:
        root = Path.cwd() / "data" / "test-agent-loop-turn-budget"
        shutil.rmtree(root, ignore_errors=True)
        root.mkdir(parents=True, exist_ok=True)
        try:
            class MultiToolClient:
                def __init__(self) -> None:
                    self.calls = 0

                def generate(self, messages, tools=None, system_prompt=None):
                    self.calls += 1
                    if self.calls == 1:
                        return AssistantMessage(
                            tool_calls=[
                                ToolCall(id=f"call-{index}", name=f"tool_{index}", arguments={})
                                for index in range(5)
                            ]
                        )
                    return AssistantMessage(content="done")

                def stream(self, *args, **kwargs):
                    return self.generate(*args, **kwargs)

            tools = [
                Tool(
                    name=f"tool_{index}",
                    skill_name="test",
                    description="test",
                    parameters_schema={"type": "object"},
                    handler=lambda index=index: {"path": f"{index}.txt", "content": str(index) * 45000},
                )
                for index in range(5)
            ]
            executor = ToolExecutor(
                tools,
                long_result_log_path=root / "tool_result.jsonl",
                max_result_chars=50000,
                turn_result_budget_chars=200000,
            )
            session = Session(conversation_id="turn-budget", system_prompt="")
            session.add_user_message("run multiple tools")

            AgentLoop(MultiToolClient(), executor, max_rounds=2).run(session=session, tools=tools)

            tool_messages = [message for message in session.messages if message.role == "tool"]
            payloads = [json.loads(message.content) for message in tool_messages]
            ref_count = sum(1 for payload in payloads if payload.get("tool_result_ref"))
            total_chars = sum(len(message.content) for message in tool_messages)

            self.assertEqual(len(tool_messages), 5)
            self.assertEqual(ref_count, 1)
            self.assertLessEqual(total_chars, 200000)
            self.assertTrue((root / "tool_result.jsonl").exists())
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_tool_executor_long_ls_preview_omits_is_dir(self) -> None:
        root = Path.cwd() / "data" / "test-tool-executor-long-ls"
        shutil.rmtree(root, ignore_errors=True)
        root.mkdir(parents=True, exist_ok=True)
        try:
            def ls() -> dict[str, object]:
                return {
                    "path": "workspace",
                    "entries": [
                        {"name": f"file-{index}.py", "path": f"workspace/file-{index}.py", "is_dir": False}
                        for index in range(100)
                    ],
                }

            executor = ToolExecutor(
                [
                    Tool(
                        name="ls",
                        skill_name="workspace",
                        description="test",
                        parameters_schema={"type": "object"},
                        handler=ls,
                    )
                ],
                long_result_log_path=root / "tool_result.jsonl",
                max_result_chars=500,
            )

            result = executor.execute(ToolCall(id="call-ls", name="ls", arguments={}))

            self.assertIn("tool_result_ref", result)
            self.assertNotIn("truncated", result)
            self.assertEqual(result["result"]["path"], "workspace")
            self.assertIn("file-0.py", result["result"]["entries_preview"])
            self.assertNotIn("is_dir", executor.serialize_result(result))
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_event_log_compacts_completed_turns_and_keeps_unfinished(self) -> None:
        root = Path.cwd() / "data" / "test-event-compact"
        shutil.rmtree(root, ignore_errors=True)
        root.mkdir(parents=True, exist_ok=True)
        try:
            event_log = EventLog(root / "events")
            for index in range(4):
                turn_id = f"done-{index}"
                event_log.append("compact-case", {"turn_id": turn_id, "type": "turn_started"})
                event_log.append("compact-case", {"turn_id": turn_id, "type": "turn_succeeded"})
            event_log.append("compact-case", {"turn_id": "unfinished", "type": "turn_started"})
            event_log.append(
                "compact-case",
                {
                    "turn_id": "unfinished",
                    "type": "tool_call_started",
                    "tool_call_id": "call-1",
                    "tool_name": "read_file",
                },
            )

            removed = event_log.compact_completed_turns("compact-case", keep_completed_turns=2)
            events = event_log.read("compact-case")
            turn_ids = {event["turn_id"] for event in events}

            self.assertGreater(removed, 0)
            self.assertNotIn("done-0", turn_ids)
            self.assertNotIn("done-1", turn_ids)
            self.assertIn("done-2", turn_ids)
            self.assertIn("done-3", turn_ids)
            self.assertIn("unfinished", turn_ids)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_mock_runner_stream_emits_output(self) -> None:
        root = Path.cwd() / "data" / "test-runner-stream"
        shutil.rmtree(root, ignore_errors=True)
        root.mkdir(parents=True, exist_ok=True)
        try:
            settings = self.make_settings(root)
            settings.ensure_directories()
            runner = Runner(settings)
            chunks: list[str] = []

            result = runner.run_turn_stream(
                conversation_id="stream-case",
                user_input="hello",
                on_content_delta=chunks.append,
            )

            self.assertEqual("".join(chunks), result.reply)
            self.assertIn("[mock] Minimal agent is running.", result.reply)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_runner_persists_feishu_conversation_id_with_safe_paths(self) -> None:
        root = Path.cwd() / "data" / "test-runner-feishu-session"
        shutil.rmtree(root, ignore_errors=True)
        root.mkdir(parents=True, exist_ok=True)
        try:
            settings = self.make_settings(root)
            settings.ensure_directories()
            runner = Runner(settings)
            conversation_id = "feishu:group:oc_123:user:ou_456"

            result = runner.run_turn(conversation_id, "hello")

            self.assertIn("[mock] Minimal agent is running.", result.reply)
            storage_name = conversation_storage_name(conversation_id)
            self.assertTrue((settings.sessions_dir / f"{storage_name}.json").exists())
            self.assertTrue((settings.logs_dir / f"{storage_name}.jsonl").exists())
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_assistant_tool_call_preserves_reasoning_content(self) -> None:
        message = ChatMessage.from_assistant(
            AssistantMessage(
                content="",
                reasoning_content="Need to inspect the file before editing.",
                tool_calls=[
                    ToolCall(
                        id="call_1",
                        name="read_file",
                        arguments={"path": "README.md"},
                    )
                ],
            )
        )

        payload = message.to_openai_dict()

        self.assertEqual(payload["role"], "assistant")
        self.assertEqual(payload["reasoning_content"], "Need to inspect the file before editing.")
        self.assertEqual(payload["tool_calls"][0]["function"]["name"], "read_file")

    def test_context_manager_trims_by_token_budget_and_large_tool_results(self) -> None:
        session = Session(conversation_id="ctx", system_prompt="")
        session.messages = [
            ChatMessage(role="user", content="initial task"),
            ChatMessage(role="tool", name="read_file", content="x" * 200),
            ChatMessage(role="assistant", content="filler " * 100),
            ChatMessage(role="assistant", content="mid", reasoning_content="r" * 80),
            ChatMessage(role="user", content="latest"),
        ]

        manager = ContextManager(
            max_tokens=120,
            recent_tokens=60,
            older_memory_tokens=40,
            tool_result_max_chars=50,
            assistant_reasoning_max_chars=30,
        )
        messages = manager.build_messages(session)

        self.assertEqual(messages[0].name, "pinned_context")
        self.assertTrue(any(message.name == "older_memory" for message in messages))
        self.assertTrue(any("Older memory:" in message.content for message in messages))
        self.assertGreater(session.metadata["context_stats"]["trimmed_message_count"], 0)
        self.assertIn("Older memory:", session.metadata["older_memory"])
        self.assertTrue(any("[tool result truncated]" in message.content for message in messages))
        self.assertTrue(any("[reasoning truncated]" in message.reasoning_content for message in messages))

    def test_context_manager_uses_token_budget_and_older_memory(self) -> None:
        session = Session(conversation_id="ctx-token", system_prompt="")
        session.messages = [
            ChatMessage(role="user", content="Please inspect app/runtime/runner.py and remember turn recovery."),
        ]
        for index in range(8):
            session.messages.append(ChatMessage(role="assistant", content=f"filler response {index} " * 20))
        session.messages.append(ChatMessage(role="user", content="What did we decide about runner.py recovery?"))

        manager = ContextManager(max_tokens=260, recent_tokens=80, older_memory_tokens=120)
        messages = manager.build_messages(session)

        self.assertEqual(messages[0].name, "pinned_context")
        self.assertTrue(any(message.name == "older_memory" for message in messages))
        self.assertGreater(session.metadata["context_stats"]["older_memory_tokens"], 0)
        self.assertTrue(any("runner.py" in message.content for message in messages))
        self.assertTrue(all(message.estimated_token_count is not None for message in session.messages))

    def test_context_manager_injects_current_workspace_over_stale_history(self) -> None:
        session = Session(conversation_id="ctx-workspace", system_prompt="")
        session.metadata["workspace_dir"] = r"D:\agentzero\xxxclaw-workspace\ctx-workspace"
        session.metadata["shell_execution_mode"] = "docker"
        session.messages = [
            ChatMessage(role="user", content="当前目录？"),
            ChatMessage(role="assistant", content="当前目录是 /app"),
            ChatMessage(role="user", content="现在当前目录是？"),
        ]

        messages = ContextManager(max_tokens=32000).build_messages(session)

        self.assertEqual(messages[0].role, "system")
        self.assertIn("current conversation workspace", messages[0].content)
        self.assertIn("xxxclaw-workspace", messages[0].content)
        self.assertIn("Older /app paths", messages[0].content)
        self.assertEqual(messages[-1].content, "现在当前目录是？")

    def test_runtime_context_flag_can_disable_workspace_injection(self) -> None:
        session = Session(conversation_id="ctx-runtime-off", system_prompt="")
        session.metadata["workspace_dir"] = r"D:\agentzero\xxxclaw-workspace\ctx-runtime-off"
        session.messages = [ChatMessage(role="user", content="现在当前目录是？")]

        messages = ContextManager(enable_runtime_context=False).build_messages(session)

        self.assertEqual(messages[0].role, "user")
        self.assertEqual(session.metadata["context_stats"]["runtime_tokens"], 0)

    def test_context_manager_injects_project_memory_before_chat_history(self) -> None:
        session = Session(conversation_id="ctx-project-memory", system_prompt="")
        session.metadata["project_memory"] = "Project rule: prefer pytest only when the repo uses pytest."
        session.messages = [ChatMessage(role="user", content="What test command should I use?")]

        messages = ContextManager(enable_runtime_context=False).build_messages(session)

        self.assertEqual(messages[0].role, "system")
        self.assertIn("Project long-term memory", messages[0].content)
        self.assertIn("prefer pytest", messages[0].content)
        self.assertEqual(messages[1].role, "user")
        self.assertGreater(session.metadata["context_stats"]["project_memory_tokens"], 0)

    def test_older_memory_flag_can_disable_history_recall(self) -> None:
        session = Session(conversation_id="ctx-older-off", system_prompt="")
        session.messages = [
            ChatMessage(role="user", content="Remember app/runtime/runner.py recovery decision."),
        ]
        for index in range(8):
            session.messages.append(ChatMessage(role="assistant", content=f"filler response {index} " * 20))
        session.messages.append(ChatMessage(role="user", content="What was the runner.py recovery decision?"))

        manager = ContextManager(
            max_tokens=260,
            recent_tokens=80,
            older_memory_tokens=120,
            enable_older_memory=False,
        )
        messages = manager.build_messages(session)

        self.assertFalse(any(message.name == "older_memory" for message in messages))
        self.assertEqual(session.metadata["context_stats"]["older_memory_tokens"], 0)

    def test_context_manager_preserves_early_important_long_context(self) -> None:
        session = Session(conversation_id="ctx-important", system_prompt="")
        long_but_important = (
            "Critical decision: when debugging payment_timeout in app/runtime/runner.py, "
            "keep the WAL event before tool execution and do not rely only on recent chat. "
            + "irrelevant filler " * 120
        )
        session.messages = [ChatMessage(role="user", content=long_but_important)]
        for index in range(30):
            session.messages.append(ChatMessage(role="assistant", content=f"ok {index}"))
        session.messages.append(ChatMessage(role="user", content="How should we handle payment_timeout in runner.py?"))

        manager = ContextManager(max_tokens=320, recent_tokens=120, older_memory_tokens=120)
        messages = manager.build_messages(session)

        older_memory = [message for message in messages if message.name == "older_memory"]
        self.assertTrue(older_memory)
        self.assertTrue(any("payment_timeout" in message.content for message in older_memory))
        self.assertTrue(any("runner.py" in message.content for message in older_memory))

    def test_context_manager_builds_pinned_context_metadata(self) -> None:
        session = Session(conversation_id="ctx-pinned", system_prompt="")
        session.messages = [
            ChatMessage(role="user", content="Initial goal: edit app/runtime/runner.py. 必须保持架构简单。"),
            ChatMessage(role="assistant", content="ok " * 80),
            ChatMessage(role="user", content="Now continue runner.py recovery work"),
        ]

        manager = ContextManager(max_tokens=80, recent_tokens=20, older_memory_tokens=20)
        messages = manager.build_messages(session)

        pinned = session.metadata["pinned_context"]
        self.assertIn("initial_goal", pinned)
        self.assertIn("app/runtime/runner.py", pinned["key_files"])
        self.assertNotIn("open_tasks", pinned)
        self.assertNotIn("constraints", pinned)
        self.assertEqual(messages[0].name, "pinned_context")
        self.assertNotIn("constraints:", messages[0].content)
        self.assertNotIn("open tasks:", messages[0].content)

    def test_context_manager_uses_persisted_estimated_message_token_count(self) -> None:
        session = Session(conversation_id="ctx-token-count", system_prompt="")
        session.messages = [
            ChatMessage(role="user", content="important runner.py decision", estimated_token_count=5),
            ChatMessage(role="assistant", content="x" * 200, estimated_token_count=999),
            ChatMessage(role="user", content="runner.py?", estimated_token_count=5),
        ]

        manager = ContextManager(max_tokens=120, recent_tokens=40, older_memory_tokens=80)
        messages = manager.build_messages(session)

        self.assertEqual(session.messages[0].estimated_token_count, 5)
        self.assertEqual(session.messages[1].estimated_token_count, 999)
        self.assertTrue(any(message.name in {"pinned_context", "older_memory"} for message in messages))

    def test_token_counter_does_not_overestimate_short_chinese_user_text(self) -> None:
        counter = TokenCounter()
        message = ChatMessage(role="user", content="查询榆林天气")
        tokens = counter.count_message(message)
        self.assertLess(tokens, 22)
        self.assertGreater(tokens, 0)

    def test_recovery_manager_finds_interrupted_tool(self) -> None:
        root = Path.cwd() / "data" / "test-recovery-manager"
        shutil.rmtree(root, ignore_errors=True)
        root.mkdir(parents=True, exist_ok=True)
        try:
            event_log = EventLog(root / "events")
            event_log.append("recover-case", {"turn_id": "turn-1", "type": "turn_started"})
            event_log.append(
                "recover-case",
                {
                    "turn_id": "turn-1",
                    "type": "tool_call_started",
                    "tool_call_id": "call-1",
                    "tool_name": "read_file",
                    "arguments": {"path": "README.md"},
                },
            )

            unfinished = RecoveryManager(event_log).scan_unfinished()

            self.assertEqual(len(unfinished), 1)
            self.assertEqual(unfinished[0].turn_id, "turn-1")
            self.assertEqual(unfinished[0].interrupted_tools[0].tool_name, "read_file")
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_runner_recovers_idempotent_tools_and_skips_non_recoverable(self) -> None:
        root = Path.cwd() / "data" / "test-runner-recovery"
        shutil.rmtree(root, ignore_errors=True)
        root.mkdir(parents=True, exist_ok=True)
        try:
            settings = self.make_settings(root)
            settings.ensure_directories()
            runner = Runner(settings)
            conversation_id = "recover-exec-case"
            self.make_workspace(settings, conversation_id).joinpath("sample.txt").write_text("hello", encoding="utf-8")

            runner.event_log.append(conversation_id, {"turn_id": "turn-1", "type": "turn_started"})
            runner.event_log.append(
                conversation_id,
                {
                    "turn_id": "turn-1",
                    "type": "tool_call_started",
                    "tool_call_id": "call-read",
                    "tool_name": "read_file",
                    "arguments": {"path": "sample.txt"},
                },
            )
            runner.event_log.append(
                conversation_id,
                {
                    "turn_id": "turn-1",
                    "type": "tool_call_started",
                    "tool_call_id": "call-shell",
                    "tool_name": "run_shell",
                    "arguments": {"command": "echo hi"},
                },
            )

            unfinished = runner.recover_unfinished_tasks()

            self.assertEqual(len(unfinished), 1)
            session = runner.session_store.load(conversation_id=conversation_id, system_prompt="")
            self.assertTrue(any(message.role == "tool" and message.name == "read_file" for message in session.messages))
            self.assertTrue(
                any(
                    message.role == "assistant"
                    and "not replayed automatically" in message.content
                    and "run_shell" in message.content
                    for message in session.messages
                )
            )
            events = runner.event_log.read(conversation_id)
            self.assertTrue(any(event["type"] == "tool_call_succeeded" and event.get("recovered") for event in events))
            self.assertTrue(any(event["type"] == "tool_call_skipped" and event["tool_name"] == "run_shell" for event in events))
            self.assertTrue(any(event["type"] == "turn_recovered" for event in events))
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_recovery_flag_can_disable_unfinished_scan(self) -> None:
        root = Path.cwd() / "data" / "test-runner-recovery-off"
        shutil.rmtree(root, ignore_errors=True)
        root.mkdir(parents=True, exist_ok=True)
        try:
            settings = self.make_settings(root)
            settings.feature_recovery = False
            settings.ensure_directories()
            runner = Runner(settings)
            runner.event_log.append("recover-off", {"turn_id": "turn-1", "type": "turn_started"})

            self.assertEqual(runner.scan_unfinished_tasks(), [])
            self.assertEqual(runner.recover_unfinished_tasks(), [])
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_skill_router_selects_relevant_tools(self) -> None:
        root = Path.cwd() / "data" / "test-skill-router"
        shutil.rmtree(root, ignore_errors=True)
        root.mkdir(parents=True, exist_ok=True)
        try:
            settings = self.make_settings(root)
            tools = build_default_tools(settings)
            router = SkillRouter(tools)
            session = Session(conversation_id="skill", system_prompt="")

            skills, active_tools = router.select_tools(session, "请运行pytest并读取README.md")

            self.assertEqual([skill.name for skill in skills], ["workspace", "shell"])
            self.assertEqual(
                sorted(tool.name for tool in active_tools),
                ["edit_file", "ls", "read_file", "run_shell", "write_file"],
            )
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_openai_client_timeout_is_wrapped(self) -> None:
        client = OpenAICompatibleClient(
            config=ModelConfig(provider="openai", model="kimi-k2.6"),
            base_url="https://api.moonshot.cn/v1/chat/completions",
            api_key="test-key",
            request_timeout_seconds=3,
            max_retries=0,
        )

        with patch("urllib.request.urlopen", side_effect=TimeoutError("The read operation timed out")):
            with self.assertRaises(RuntimeError) as ctx:
                client.generate(messages=[ChatMessage(role="user", content="hello")])

        self.assertIn("timed out after 3s", str(ctx.exception))

    def test_openai_client_stream_parses_content_reasoning_and_tool_calls(self) -> None:
        client = OpenAICompatibleClient(
            config=ModelConfig(provider="openai", model="kimi-k2.6"),
            base_url="https://api.moonshot.cn/v1/chat/completions",
            api_key="test-key",
            request_timeout_seconds=3,
            max_retries=0,
        )

        class FakeStreamingResponse:
            def __init__(self, lines: list[str]) -> None:
                self.lines = [line.encode("utf-8") for line in lines]

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def __iter__(self):
                return iter(self.lines)

        chunks = [
            "data: " + '{"choices":[{"delta":{"reasoning_content":"think ","content":"hel"}}]}' + "\n",
            "data: " + '{"choices":[{"delta":{"content":"lo","tool_calls":[{"index":0,"id":"call_1","function":{"name":"read_file","arguments":"{\\"path\\": \\"REA"}}]}}]}' + "\n",
            "data: " + '{"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"DME.md\\"}"}}]}}]}' + "\n",
            "data: [DONE]\n",
        ]

        streamed_chunks: list[str] = []
        tool_names: list[str] = []
        with patch("urllib.request.urlopen", return_value=FakeStreamingResponse(chunks)):
            message = client.stream(
                messages=[ChatMessage(role="user", content="hello")],
                on_content_delta=streamed_chunks.append,
                on_tool_call_delta=tool_names.append,
            )

        self.assertEqual("".join(streamed_chunks), "hello")
        self.assertEqual(message.content, "hello")
        self.assertEqual(message.reasoning_content, "think ")
        self.assertEqual(message.tool_calls[0].name, "read_file")
        self.assertEqual(message.tool_calls[0].arguments, {"path": "README.md"})
        self.assertEqual(tool_names, ["read_file"])


if __name__ == "__main__":
    unittest.main()
