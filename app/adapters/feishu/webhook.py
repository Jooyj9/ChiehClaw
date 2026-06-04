from __future__ import annotations

import argparse
import json
import threading
from concurrent.futures import Executor, ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from app.adapters.base import IncomingMessage
from app.adapters.feishu.client import FeishuAPIClient, FeishuConfig
from app.adapters.feishu.formatter import FeishuFormatter
from app.adapters.feishu.router import FeishuRouter
from app.runtime.runner import Runner
from app.utils.logger import append_jsonl


class FeishuWebhookApp:
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
        self.executor = executor or ThreadPoolExecutor(max_workers=2, thread_name_prefix="feishu-webhook")
        self._conversation_locks: dict[str, threading.Lock] = {}
        self._conversation_locks_guard = threading.Lock()

    def handle_payload(self, payload: dict[str, object]) -> tuple[int, dict[str, object]]:
        routed = self.router.route(payload)
        if routed.kind == "challenge":
            return 200, routed.response_body or {"challenge": ""}

        if routed.kind == "ignored":
            return 200, {"ok": True, "ignored": routed.reason}

        incoming = routed.incoming_message
        if incoming is None:
            return 200, {"ok": True, "ignored": "missing_message"}

        self.executor.submit(self._process_message, incoming)
        return 200, {"ok": True, "accepted": True, "conversation_id": incoming.conversation_id}

    def _process_message(self, incoming: IncomingMessage) -> None:
        reply_text = ""
        try:
            lock = self._lock_for(incoming.conversation_id)
            with lock:
                result = self.runner.run_turn(
                    conversation_id=incoming.conversation_id,
                    user_input=incoming.text,
                )
            reply_text = result.reply
        except Exception as exc:
            reply_text = "处理消息时出错，请稍后重试。"
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


def build_handler(app: FeishuWebhookApp, webhook_path: str):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if urlsplit(self.path).path == "/healthz":
                self._send_json(200, {"ok": True})
                return
            self._send_json(404, {"ok": False, "error": "not_found"})

        def do_POST(self) -> None:
            if urlsplit(self.path).path != webhook_path:
                self._send_json(404, {"ok": False, "error": "not_found"})
                return

            try:
                content_length = int(self.headers.get("Content-Length", "0"))
                raw_body = self.rfile.read(content_length) if content_length > 0 else b""
                payload = json.loads(raw_body.decode("utf-8") or "{}")
                status_code, response_body = app.handle_payload(payload)
                self._send_json(status_code, response_body)
            except json.JSONDecodeError:
                self._send_json(400, {"ok": False, "error": "invalid_json"})
            except ValueError as exc:
                self._send_json(400, {"ok": False, "error": str(exc)})
            except Exception as exc:
                app._log({"server_error": str(exc)})
                self._send_json(500, {"ok": False, "error": "internal_error"})

        def log_message(self, format: str, *args) -> None:
            return

        def _send_json(self, status_code: int, payload: dict[str, object]) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the Feishu IM webhook for xxxclaw.")
    parser.add_argument("--host", help="Webhook host. Defaults to XXXCLAW_FEISHU_HOST or 0.0.0.0.")
    parser.add_argument("--port", type=int, help="Webhook port. Defaults to XXXCLAW_FEISHU_PORT or 8000.")
    parser.add_argument("--path", help="Webhook path. Defaults to XXXCLAW_FEISHU_WEBHOOK_PATH or /feishu/webhook.")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    config = FeishuConfig.from_env()
    if args.host:
        config.host = args.host
    if args.port:
        config.port = args.port
    if args.path:
        config.webhook_path = args.path
    config.validate()

    runner = Runner.from_env()
    runner.recover_unfinished_tasks()
    app = FeishuWebhookApp(
        runner=runner,
        router=FeishuRouter(
            verification_token=config.verification_token,
            bot_open_id=config.bot_open_id,
        ),
        formatter=FeishuFormatter(max_reply_chars=config.reply_max_chars),
        client=FeishuAPIClient(config),
        log_path=runner.settings.logs_dir / "feishu-webhook.jsonl",
        executor=ThreadPoolExecutor(
            max_workers=max(1, config.worker_count),
            thread_name_prefix="feishu-webhook",
        ),
    )
    server = ThreadingHTTPServer((config.host, config.port), build_handler(app, config.webhook_path))
    print(f"Feishu webhook listening on http://{config.host}:{config.port}{config.webhook_path}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
