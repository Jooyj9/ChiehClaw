from __future__ import annotations

import uuid
from typing import Callable, Iterable

from app.ai.base import LLMClient
from app.ai.message import AssistantMessage, ChatMessage, ToolCall


class MockLLMClient(LLMClient):
    """Local fallback client so the minimal agent can run without external API access."""

    def generate(
        self,
        messages: list[ChatMessage],
        tools: Iterable[object] | None = None,
        system_prompt: str | None = None,
    ) -> AssistantMessage:
        if not messages:
            return AssistantMessage(content="[mock] No messages received.")

        last_message = messages[-1]
        if last_message.role == "tool":
            return AssistantMessage(content=f"[mock] Tool result:\n{last_message.content}")

        if last_message.role != "user":
            return AssistantMessage(content="[mock] Waiting for user input.")

        text = last_message.content.strip()
        if text.startswith("ls"):
            return AssistantMessage(
                tool_calls=[
                    ToolCall(
                        id=f"call_{uuid.uuid4().hex[:8]}",
                        name="ls",
                        arguments={"path": text[2:].strip() or "."},
                    )
                ]
            )
        if text.startswith("read "):
            return AssistantMessage(
                tool_calls=[
                    ToolCall(
                        id=f"call_{uuid.uuid4().hex[:8]}",
                        name="read_file",
                        arguments={"path": text[5:].strip()},
                    )
                ]
            )
        if text.startswith("shell "):
            return AssistantMessage(
                tool_calls=[
                    ToolCall(
                        id=f"call_{uuid.uuid4().hex[:8]}",
                        name="run_shell",
                        arguments={"command": text[6:].strip()},
                    )
                ]
            )
        return AssistantMessage(
            content=(
                "[mock] Minimal agent is running. "
                "Use `ls`, `read <path>`, or `shell <command>` to exercise tools, "
                f"or configure OpenAI env vars for a real model.\n\nYou said: {text}"
            )
        )

    def stream(
        self,
        messages: list[ChatMessage],
        tools: Iterable[object] | None = None,
        system_prompt: str | None = None,
        on_content_delta: Callable[[str], None] | None = None,
        on_reasoning_delta: Callable[[str], None] | None = None,
        on_tool_call_delta: Callable[[str], None] | None = None,
    ) -> AssistantMessage:
        message = self.generate(messages=messages, tools=tools, system_prompt=system_prompt)
        if on_tool_call_delta:
            for tool_call in message.tool_calls:
                on_tool_call_delta(tool_call.name)
        if on_reasoning_delta and message.reasoning_content:
            on_reasoning_delta(message.reasoning_content)
        if on_content_delta and message.content:
            on_content_delta(message.content)
        return message
