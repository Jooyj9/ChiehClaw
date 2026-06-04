from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Literal

from app.adapters.base import IncomingMessage


@dataclass(slots=True)
class FeishuRouteResult:
    kind: Literal["challenge", "message", "ignored"]
    response_body: dict[str, Any] | None = None
    incoming_message: IncomingMessage | None = None
    reason: str = ""


class FeishuRouter:
    def __init__(
        self,
        verification_token: str | None = None,
        bot_open_id: str | None = None,
        group_session_scope: str = "chat_user",
    ) -> None:
        self.verification_token = verification_token or ""
        self.bot_open_id = bot_open_id or ""
        self.group_session_scope = group_session_scope

    def route(self, payload: dict[str, Any]) -> FeishuRouteResult:
        encrypt_text = str(payload.get("encrypt") or "").strip()
        if encrypt_text:
            raise ValueError("Encrypted Feishu events are not supported yet. Leave Encrypt Key empty for now.")

        payload_type = str(payload.get("type") or "")
        if payload_type == "url_verification":
            self._validate_token(str(payload.get("token") or ""))
            return FeishuRouteResult(
                kind="challenge",
                response_body={"challenge": payload.get("challenge", "")},
            )

        header = payload.get("header") or {}
        self._validate_token(str(header.get("token") or ""))
        return self.route_event_payload(payload)

    def route_event_payload(self, payload: dict[str, Any]) -> FeishuRouteResult:
        header = payload.get("header") or {}

        event_type = str(header.get("event_type") or "")
        if event_type and event_type != "im.message.receive_v1":
            return FeishuRouteResult(kind="ignored", reason=f"unsupported_event:{event_type}")

        incoming = self.parse_message_event(payload)
        if incoming is None:
            return FeishuRouteResult(kind="ignored", reason="ignored_message")
        return FeishuRouteResult(kind="message", incoming_message=incoming)

    def parse_message_event(self, payload: dict[str, Any]) -> IncomingMessage | None:
        event = payload.get("event") or {}
        message = event.get("message") or {}
        sender = event.get("sender") or {}

        if str(sender.get("sender_type") or "user") != "user":
            return None
        if str(message.get("message_type") or "") != "text":
            return None

        text = self._extract_text(message)
        if not text:
            return None

        sender_open_id = str((sender.get("sender_id") or {}).get("open_id") or "")
        chat_id = str(message.get("chat_id") or "")
        chat_type = str(message.get("chat_type") or "unknown")
        thread_root_id = str(message.get("root_id") or message.get("parent_id") or "")

        return IncomingMessage(
            conversation_id=self._build_conversation_id(
                chat_type=chat_type,
                chat_id=chat_id,
                sender_open_id=sender_open_id,
                thread_root_id=thread_root_id,
            ),
            message_id=str(message.get("message_id") or ""),
            text=text,
            chat_id=chat_id,
            chat_type=chat_type,
            sender_open_id=sender_open_id,
            thread_root_id=thread_root_id,
        )

    def _validate_token(self, token: str) -> None:
        if not self.verification_token:
            return
        if token != self.verification_token:
            raise ValueError("Feishu verification token mismatch.")

    def _extract_text(self, message: dict[str, Any]) -> str:
        content = message.get("content") or ""
        if isinstance(content, str):
            try:
                payload = json.loads(content)
            except json.JSONDecodeError:
                text = content
            else:
                text = str(payload.get("text") or "")
        else:
            text = str(content)

        mentions = message.get("mentions") or []
        for mention in mentions:
            key = str(mention.get("key") or "")
            mentioned_open_id = str((mention.get("id") or {}).get("open_id") or "")
            if not key:
                continue
            should_strip = mentioned_open_id == self.bot_open_id or (
                not self.bot_open_id and text.lstrip().startswith(key)
            )
            if should_strip:
                text = text.replace(key, "", 1)

        return " ".join(text.split())

    def _build_conversation_id(
        self,
        chat_type: str,
        chat_id: str,
        sender_open_id: str,
        thread_root_id: str,
    ) -> str:
        short_chat = self._short_id(chat_id, default_prefix="chat")
        short_sender = self._short_id(sender_open_id, default_prefix="user")
        short_thread = self._short_id(thread_root_id, default_prefix="thread")

        if chat_type == "p2p":
            return f"feishu-p2p-{short_sender or short_chat}"

        if self.group_session_scope == "thread" and thread_root_id:
            return f"feishu-{chat_type}-{short_chat}-thread-{short_thread}"
        if self.group_session_scope == "thread":
            return f"feishu-{chat_type}-{short_chat}"

        if self.group_session_scope == "chat":
            return f"feishu-{chat_type}-{short_chat}"

        return f"feishu-{chat_type}-{short_chat}-user-{short_sender or 'unknown'}"

    @staticmethod
    def _short_id(value: str, default_prefix: str) -> str:
        if not value:
            return default_prefix

        known_prefixes = {
            "ou_": "u",
            "oc_": "c",
            "om_": "m",
            "on_": "n",
            "ob_": "b",
            "cli_": "app",
        }
        compact_prefix = default_prefix
        compact_body = value
        for prefix, alias in known_prefixes.items():
            if value.startswith(prefix):
                compact_prefix = alias
                compact_body = value[len(prefix) :]
                break

        digest = hashlib.sha1(value.encode("utf-8")).hexdigest()[:4]
        return f"{compact_prefix}{compact_body[:8]}-{digest}"
