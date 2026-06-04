from __future__ import annotations

import json
import unittest
from concurrent.futures import Future
from io import StringIO
from unittest.mock import patch

from app.adapters.feishu.formatter import FeishuFormatter
from app.adapters.feishu.client import FeishuConfig
from app.adapters.feishu.long_connection import FeishuLongConnectionApp
from app.adapters.feishu.router import FeishuRouter
from app.agent.session import Session
from app.ai.message import ChatMessage
from app.runtime.context_manager import ContextManager
from app.schemas import TurnResult
from app.storage.file_store import ConversationFileStore
from app.storage.naming import conversation_storage_name


class InlineExecutor:
    def submit(self, fn, *args, **kwargs):
        future = Future()
        try:
            result = fn(*args, **kwargs)
        except Exception as exc:
            future.set_exception(exc)
        else:
            future.set_result(result)
        return future


class FakeRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def run_turn(self, conversation_id: str, user_input: str) -> TurnResult:
        self.calls.append((conversation_id, user_input))
        return TurnResult(conversation_id=conversation_id, reply=f"echo: {user_input}")

    def run_turn_stream(
        self,
        conversation_id: str,
        user_input: str,
        on_content_delta=None,
        on_reasoning_delta=None,
        on_tool_call_delta=None,
    ) -> TurnResult:
        self.calls.append((conversation_id, user_input))
        reply = f"echo: {user_input}"
        if on_content_delta:
            on_content_delta(reply)
        return TurnResult(conversation_id=conversation_id, reply=reply)


class FakeFeishuClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.config = FeishuConfig(app_id="app", app_secret="secret")

    def reply_message(self, message_id: str, payload: dict[str, object]) -> dict[str, object]:
        self.calls.append((message_id, payload))
        return {"code": 0, "msg": "success"}


class FeishuAdapterTestCase(unittest.TestCase):
    def test_conversation_storage_name_is_windows_safe(self) -> None:
        conversation_id = "feishu:group:oc_c021:user:ou_30e4"

        storage_name = conversation_storage_name(conversation_id)

        self.assertTrue(storage_name.startswith("conv_"))
        self.assertNotIn(":", storage_name)
        self.assertNotIn("/", storage_name)

    def test_conversation_storage_name_keeps_existing_safe_ids(self) -> None:
        self.assertEqual(conversation_storage_name("local-cli"), "local-cli")

    def test_older_memory_preserves_initial_user_goal_when_trimmed(self) -> None:
        session = Session(conversation_id="ctx-summary", system_prompt="")
        session.messages = [ChatMessage(role="user", content="Build a Feishu adapter for this agent.")]
        for index in range(1, 16):
            if index % 2 == 0:
                session.messages.append(ChatMessage(role="assistant", content=f"assistant-{index}"))
            else:
                session.messages.append(ChatMessage(role="user", content=f"user-{index}"))

        manager = ContextManager(max_tokens=45, recent_tokens=20, older_memory_tokens=10)
        messages = manager.build_messages(session)

        self.assertEqual(messages[0].name, "pinned_context")
        self.assertIn("Build a Feishu adapter for this agent.", messages[0].content)

    def test_feishu_router_handles_url_verification(self) -> None:
        router = FeishuRouter(verification_token="token-123")
        result = router.route(
            {
                "type": "url_verification",
                "token": "token-123",
                "challenge": "challenge-value",
            }
        )

        self.assertEqual(result.kind, "challenge")
        self.assertEqual(result.response_body, {"challenge": "challenge-value"})

    def test_feishu_router_routes_text_message(self) -> None:
        router = FeishuRouter(verification_token="token-123", bot_open_id="ou_bot")
        result = router.route_event_payload(
            {
                "schema": "2.0",
                "header": {
                    "event_type": "im.message.receive_v1",
                },
                "event": {
                    "sender": {
                        "sender_id": {"open_id": "ou_user"},
                        "sender_type": "user",
                    },
                    "message": {
                        "message_id": "om_message",
                        "chat_id": "oc_group",
                        "chat_type": "group",
                        "message_type": "text",
                        "content": json.dumps({"text": "@_user_1 help me inspect README.md"}, ensure_ascii=False),
                        "mentions": [
                            {
                                "key": "@_user_1",
                                "id": {"open_id": "ou_bot"},
                            }
                        ],
                    },
                },
            }
        )

        self.assertEqual(result.kind, "message")
        self.assertIsNotNone(result.incoming_message)
        self.assertEqual(result.incoming_message.text, "help me inspect README.md")
        self.assertTrue(result.incoming_message.conversation_id.startswith("feishu-group-"))
        self.assertIn("-user-", result.incoming_message.conversation_id)

    def test_feishu_router_supports_group_chat_scope(self) -> None:
        router = FeishuRouter(group_session_scope="chat")

        result = router.route_event_payload(
            {
                "header": {"event_type": "im.message.receive_v1"},
                "event": {
                    "sender": {"sender_id": {"open_id": "ou_user"}, "sender_type": "user"},
                    "message": {
                        "message_id": "om_message",
                        "chat_id": "oc_group",
                        "chat_type": "group",
                        "message_type": "text",
                        "content": json.dumps({"text": "hello"}, ensure_ascii=False),
                    },
                },
            }
        )

        self.assertEqual(result.kind, "message")
        self.assertEqual(result.incoming_message.conversation_id.count("-user-"), 0)
        self.assertTrue(result.incoming_message.conversation_id.startswith("feishu-group-"))

    def test_feishu_long_connection_app_runs_runner_and_replies(self) -> None:
        runner = FakeRunner()
        client = FakeFeishuClient()
        app = FeishuLongConnectionApp(
            runner=runner,
            router=FeishuRouter(bot_open_id="ou_bot"),
            formatter=FeishuFormatter(max_reply_chars=200),
            client=client,
            executor=InlineExecutor(),
        )

        handled = app.handle_event_payload(
            {
                "schema": "2.0",
                "header": {
                    "event_type": "im.message.receive_v1",
                },
                "event": {
                    "sender": {
                        "sender_id": {"open_id": "ou_user"},
                        "sender_type": "user",
                    },
                    "message": {
                        "message_id": "om_message",
                        "chat_id": "oc_group",
                        "chat_type": "group",
                        "message_type": "text",
                        "content": json.dumps({"text": "summarize this project"}, ensure_ascii=False),
                    },
                },
            }
        )

        self.assertTrue(handled)
        self.assertEqual(len(runner.calls), 1)
        self.assertTrue(runner.calls[0][0].startswith("feishu-group-"))
        self.assertEqual(client.calls[0][0], "om_message")
        self.assertEqual(json.loads(client.calls[0][1]["content"])["text"], "echo: summarize this project")

    def test_feishu_long_connection_app_prints_live_console_output(self) -> None:
        runner = FakeRunner()
        client = FakeFeishuClient()
        app = FeishuLongConnectionApp(
            runner=runner,
            router=FeishuRouter(bot_open_id="ou_bot"),
            formatter=FeishuFormatter(max_reply_chars=200),
            client=client,
            executor=InlineExecutor(),
        )

        captured = StringIO()
        with patch("sys.stdout", captured):
            handled = app.handle_event_payload(
                {
                    "schema": "2.0",
                    "header": {"event_type": "im.message.receive_v1"},
                    "event": {
                        "sender": {"sender_id": {"open_id": "ou_user"}, "sender_type": "user"},
                        "message": {
                            "message_id": "om_message",
                            "chat_id": "oc_group",
                            "chat_type": "group",
                            "message_type": "text",
                            "content": json.dumps({"text": "hello terminal"}, ensure_ascii=False),
                        },
                    },
                }
            )

        self.assertTrue(handled)
        output = captured.getvalue()
        self.assertIn("[feishu][user][", output)
        self.assertIn("hello terminal", output)
        self.assertIn("[feishu][assistant][", output)

    def test_conversation_file_store_uses_safe_directory_name(self) -> None:
        from pathlib import Path
        import shutil

        root = Path.cwd() / "data" / "test-feishu-file-store"
        shutil.rmtree(root, ignore_errors=True)
        root.mkdir(parents=True, exist_ok=True)
        try:
            store = ConversationFileStore(root)

            directory = store.conversation_dir("feishu:p2p:ou_30e40ac6314908ffe47ad4bdd8773c38")

            self.assertTrue(directory.exists())
            self.assertEqual(directory.name, conversation_storage_name("feishu:p2p:ou_30e40ac6314908ffe47ad4bdd8773c38"))
        finally:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
