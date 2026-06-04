from __future__ import annotations

import json
import re
from functools import cached_property
from typing import Any

from app.ai.message import ChatMessage


class TokenCounter:
    def __init__(self, encoding_name: str = "cl100k_base") -> None:
        self.encoding_name = encoding_name

    def count_message(self, message: ChatMessage) -> int:
        # Approximate chat-style token accounting instead of counting serialized JSON keys.
        # This keeps short user messages from being heavily overestimated.
        tokens = 3  # per-message framing overhead (chat-format approximation)
        tokens += self.count_text(message.role)
        if message.content:
            tokens += self.count_text(message.content)
        if message.reasoning_content:
            tokens += self.count_text(message.reasoning_content)
        if message.name:
            tokens += self.count_text(message.name)
        if message.tool_call_id:
            tokens += self.count_text(message.tool_call_id)
        if message.tool_calls:
            for tool_call in message.tool_calls:
                tokens += self.count_text(tool_call.id)
                tokens += self.count_text(tool_call.name)
                tokens += self.count_text(json.dumps(tool_call.arguments, ensure_ascii=False, sort_keys=True))
        return max(1, tokens)

    def count_text(self, text: str) -> int:
        encoding = self._encoding
        if encoding is not None:
            return len(encoding.encode(text))

        # Fallback is deterministic, but not model-exact. It is used only when
        # no tokenizer package is installed in the local environment.
        return len(re.findall(r"[\u4e00-\u9fff]|[A-Za-z0-9_]+|[^\s]", text))

    @cached_property
    def _encoding(self) -> Any | None:
        try:
            import tiktoken  # type: ignore[import-not-found]
        except ImportError:
            return None

        try:
            return tiktoken.get_encoding(self.encoding_name)
        except Exception:
            return tiktoken.get_encoding("cl100k_base")
