from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Callable, Iterable

from app.ai.message import AssistantMessage, ChatMessage

if TYPE_CHECKING:
    from app.agent.tools import Tool


class LLMClient(ABC):
    @abstractmethod
    def generate(
        self,
        messages: list[ChatMessage],
        tools: Iterable["Tool"] | None = None,
        system_prompt: str | None = None,
    ) -> AssistantMessage:
        raise NotImplementedError

    @abstractmethod
    def stream(
        self,
        messages: list[ChatMessage],
        tools: Iterable["Tool"] | None = None,
        system_prompt: str | None = None,
        on_content_delta: Callable[[str], None] | None = None,
        on_reasoning_delta: Callable[[str], None] | None = None,
        on_tool_call_delta: Callable[[str], None] | None = None,
    ) -> AssistantMessage:
        raise NotImplementedError
