from __future__ import annotations

import argparse
import importlib
import json
import sys
import threading
from concurrent.futures import Executor, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.adapters.base import IncomingMessage
from app.adapters.feishu.client import FeishuAPIClient, FeishuConfig
from app.adapters.feishu.formatter import FeishuFormatter
from app.adapters.feishu.router import FeishuRouter
from app.runtime.runner import Runner
from app.utils.logger import append_jsonl

# load lark SDK: enire moduel, websocket module,refer event module
def load_lark_oapi():
    try:
        lark_module = importlib.import_module("lark_oapi")
        ws_module = importlib.import_module("lark_oapi.ws")
        event_module = importlib.import_module("lark_oapi.event.dispatcher_handler")
    except ImportError as exc:
        raise RuntimeError(
            "Feishu long connection requires `lark-oapi`. Run `uv sync` "
            "or `uv add lark-oapi` in this project first."
        ) from exc

    ws_client_cls = getattr(ws_module, "Client", None)
    if ws_client_cls is None:
        raise RuntimeError("Installed `lark-oapi` does not expose `lark_oapi.ws.Client`.")
    event_dispatcher_handler = getattr(lark_module, "EventDispatcherHandler", None) or getattr(
        event_module,
        "EventDispatcherHandler",
        None,
    )
    if event_dispatcher_handler is None:
        raise RuntimeError("Installed `lark-oapi` does not expose `EventDispatcherHandler`.")
    if not hasattr(lark_module, "JSON"):
        raise RuntimeError("Installed `lark-oapi` does not expose `JSON` helpers.")
    if not hasattr(lark_module, "LogLevel"):
        raise RuntimeError("Installed `lark-oapi` does not expose `LogLevel`.")

    return lark_module, ws_client_cls, event_dispatcher_handler

# get log
def resolve_lark_log_level(lark_module, level_name: str):
    normalized = (level_name or "INFO").strip().upper()
    return getattr(lark_module.LogLevel, normalized, lark_module.LogLevel.INFO)


@dataclass(slots=True)
class ConsoleStreamState:
    label: str
    streamed: bool = False
    content_seen: bool = False
    tool_status_started: bool = False
    buffered_reasoning: list[str] = field(default_factory=list)
    shown_tools: set[str] = field(default_factory=set)


class FeishuLongConnectionApp:
    def __init__(
        self,
        runner: Runner,
        router: FeishuRouter,
        formatter: FeishuFormatter,
        client: FeishuAPIClient,
        log_path: Path | None = None,
        executor: Executor | None = None,
    ) -> None:
        self.runner = runner
        self.router = router
        self.formatter = formatter
        self.client = client
        self.log_path = log_path
        self.executor = executor or ThreadPoolExecutor(max_workers=2, thread_name_prefix="feishu-long-connection")
        self._conversation_locks: dict[str, threading.Lock] = {}
        self._conversation_locks_guard = threading.Lock()
        self._console_lock = threading.Lock()
    # handle event
    def handle_event_payload(self, payload: dict[str, object]) -> bool:
        routed = self.router.route_event_payload(payload)
        if routed.kind != "message" or routed.incoming_message is None:
            self._log({"ignored": routed.reason or "ignored_event"})
            return False

        self.executor.submit(self._process_message, routed.incoming_message)
        return True

    # register handler
    def build_event_handler(self, lark_module, event_dispatcher_handler):
        log_level = resolve_lark_log_level(lark_module, getattr(self.client.config, "sdk_log_level", "INFO"))

        def do_p2_im_message_receive_v1(data: Any) -> None:
            # Core data source
            # SDKobj -> JSON(str) -> python-dict
            payload = json.loads(lark_module.JSON.marshal(data))
            self.handle_event_payload(payload)

        return (
            event_dispatcher_handler.builder("", "", log_level)
            .register_p2_im_message_receive_v1(do_p2_im_message_receive_v1)
            .build()
        )

    def _process_message(self, incoming: IncomingMessage) -> None:
        reply_text = ""
        try:
            lock = self._lock_for(incoming.conversation_id)
            with lock:
                state = self._start_console_turn(incoming)
                result = self.runner.run_turn_stream(
                    conversation_id=incoming.conversation_id,
                    user_input=incoming.text,
                    on_content_delta=self._build_content_callback(state),
                    on_reasoning_delta=self._build_reasoning_callback(state),
                    on_tool_call_delta=self._build_tool_call_callback(state),
                )
                self._finish_console_turn(state, result.reply)
            reply_text = result.reply
        except Exception as exc:
            reply_text = "Processing failed. Please try again later."
            self._console_write(f"[feishu][error][{incoming.conversation_id}] {exc}\n")
            self._log(
                {
                    "conversation_id": incoming.conversation_id,
                    "message_id": incoming.message_id,
                    "error": str(exc),
                }
            )

        try:
            self.client.reply_message(
                message_id=incoming.message_id,
                # 给飞书reply LLM's data前进行format统一
                payload=self.formatter.format_reply(reply_text),
            )
        except Exception as exc:
            self._log(
                {
                    "conversation_id": incoming.conversation_id,
                    "message_id": incoming.message_id,
                    "reply_error": str(exc),
                }
            )

    def _lock_for(self, conversation_id: str) -> threading.Lock:
        with self._conversation_locks_guard:
            lock = self._conversation_locks.get(conversation_id)
            if lock is None:
                lock = threading.Lock()
                self._conversation_locks[conversation_id] = lock
            return lock

    def _log(self, payload: dict[str, object]) -> None:
        if not self.log_path:
            return
        append_jsonl(self.log_path, payload)

    def _start_console_turn(self, incoming: IncomingMessage) -> ConsoleStreamState:
        state = ConsoleStreamState(label=incoming.conversation_id)
        if self.client.config.console_stream:
            self._console_write(f"\n[feishu][user][{state.label}] {incoming.text}\n")
        return state

    def _finish_console_turn(self, state: ConsoleStreamState, reply_text: str) -> None:
        if not self.client.config.console_stream:
            return
        # if stream has input, then only \n, avoid duplication
        if state.streamed:
            self._console_write("\n")
            return
        # else no stream, output LLM result
        if reply_text:
            self._console_write(f"[feishu][assistant][{state.label}] {reply_text}\n")

    def _build_content_callback(self, state: ConsoleStreamState):
        def on_content_delta(text: str) -> None:
            if not text or not self.client.config.console_stream:
                return

            state.streamed = True
            if not state.content_seen:
                state.content_seen = True
                self._console_write(f"[feishu][assistant][{state.label}] ")
            self._console_write(text)

        return on_content_delta

    def _build_reasoning_callback(self, state: ConsoleStreamState):
        # 实际传参的流式方法
        def on_reasoning_delta(text: str) -> None:
            if not text or not self.client.config.console_stream or not self.client.config.console_show_tool_status:
                return
            if state.content_seen:
                return
            if state.tool_status_started:
                self._console_write(text)
            else:
                state.buffered_reasoning.append(text)

        # return this stream-output method
        return on_reasoning_delta

    def _build_tool_call_callback(self, state: ConsoleStreamState):
        def on_tool_call_delta(tool_name: str) -> None:
            if not self.client.config.console_stream or not self.client.config.console_show_tool_status:
                return
            if state.content_seen:
                return

            state.streamed = True
            if not state.tool_status_started:
                state.tool_status_started = True
                self._console_write(f"[feishu][tool-status][{state.label}] ")
                if state.buffered_reasoning:
                    self._console_write("".join(state.buffered_reasoning))
                    state.buffered_reasoning.clear()

            if tool_name and tool_name not in state.shown_tools:
                state.shown_tools.add(tool_name)
                self._console_write(f"\n[feishu][tool-call][{state.label}] {tool_name}\n")

        return on_tool_call_delta

    def _console_write(self, text: str) -> None:
        with self._console_lock:
            sys.stdout.write(text)
            sys.stdout.flush()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the Feishu long connection client for xxxclaw.")
    parser.add_argument(
        "--log-level",
        help="SDK log level. Defaults to XXXCLAW_FEISHU_LOG_LEVEL or INFO.",
    )
    parser.add_argument(
        "--group-session-scope",
        choices=["chat_user", "chat", "thread"],
        help="Group chat session scope. Defaults to XXXCLAW_FEISHU_GROUP_SESSION_SCOPE or chat_user.",
    )
    parser.add_argument(
        "--show-tool-status",
        action="store_true",
        help="Print tool reasoning and tool-call status to the terminal during Feishu turns.",
    )
    parser.add_argument(
        "--no-console-stream",
        action="store_true",
        help="Disable live terminal printing for Feishu messages.",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    config = FeishuConfig.from_env()
    if args.log_level:
        config.sdk_log_level = args.log_level
    if args.group_session_scope:
        config.group_session_scope = args.group_session_scope
    if args.show_tool_status:
        config.console_show_tool_status = True
    if args.no_console_stream:
        config.console_stream = False
    config.validate()

    lark_module, ws_client_cls, event_dispatcher_handler = load_lark_oapi()
    runner = Runner.from_env()
    runner.recover_unfinished_tasks()
    app = FeishuLongConnectionApp(
        runner=runner,
        router=FeishuRouter(
            verification_token=config.verification_token,
            bot_open_id=config.bot_open_id,
            group_session_scope=config.group_session_scope,
        ),
        formatter=FeishuFormatter(max_reply_chars=config.reply_max_chars),
        client=FeishuAPIClient(config),
        log_path=runner.settings.logs_dir / "feishu-long-connection.jsonl",
        executor=ThreadPoolExecutor(
            max_workers=max(1, config.worker_count),
            thread_name_prefix="feishu-long-connection",
        ),
    )
    # app用来执行，这里进行事件绑定处理
    # 回调桥梁
    event_handler = app.build_event_handler(lark_module, event_dispatcher_handler)
    # final construct websocket-client
    ws_client = ws_client_cls(
        config.app_id,
        config.app_secret,
        event_handler=event_handler,
        log_level=resolve_lark_log_level(lark_module, config.sdk_log_level),
    )

    print(
        "Feishu long connection client is starting. "
        f"group_session_scope={config.group_session_scope}. Press Ctrl+C to stop."
    )
    try:
        ws_client.start()
    except KeyboardInterrupt:
        return


if __name__ == "__main__":
    main()
