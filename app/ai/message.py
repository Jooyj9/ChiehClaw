from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

MessageRole = Literal["system", "user", "assistant", "tool"]

# base unit:A Tool-call request,as a member of tool-calls in AssistantMsg
@dataclass(slots=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "arguments": self.arguments}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ToolCall":
        return cls(
            id=str(data["id"]),
            name=str(data["name"]),
            arguments=dict(data.get("arguments", {})),
        )

    def to_openai_dict(self) -> dict[str, Any]:
        import json

        return {
            "id": self.id,
            "type": "function",
            "function": {
                "name": self.name,
                "arguments": json.dumps(self.arguments, ensure_ascii=False),
            },
        }

# Base unit, a kind of ChatMsg
@dataclass(slots=True)
class AssistantMessage:
    content: str = ""
    reasoning_content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)


@dataclass(slots=True)
class ChatMessage:
    role: MessageRole
    content: str = ""
    reasoning_content: str = ""
    name: str | None = None
    estimated_token_count: int | None = None
    # Only Tool-result
    tool_call_id: str | None = None
    # Only AssitantMsg maybe have
    tool_calls: list[ToolCall] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        # in need
        payload: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.reasoning_content:
            payload["reasoning_content"] = self.reasoning_content
        # optional
        if self.name:
            payload["name"] = self.name
        if self.estimated_token_count is not None:
            payload["estimated_token_count"] = self.estimated_token_count
        if self.tool_call_id:
            payload["tool_call_id"] = self.tool_call_id
        if self.tool_calls:
            payload["tool_calls"] = [tool_call.to_dict() for tool_call in self.tool_calls]
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ChatMessage":
        return cls(
            role=data["role"],
            content=data.get("content", ""),
            reasoning_content=data.get("reasoning_content", ""),
            name=data.get("name"),
            estimated_token_count=int(data["estimated_token_count"])
            if data.get("estimated_token_count") is not None
            else None,
            tool_call_id=data.get("tool_call_id"),
            tool_calls=[ToolCall.from_dict(item) for item in data.get("tool_calls", [])],
        )

    def to_openai_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.role == "assistant":
            # Kimi thinking mode requires assistant tool-call history to carry reasoning_content.
            payload["reasoning_content"] = self.reasoning_content
        if self.name:
            payload["name"] = self.name
        if self.tool_call_id:
            payload["tool_call_id"] = self.tool_call_id
        if self.tool_calls:
            payload["tool_calls"] = [tool_call.to_openai_dict() for tool_call in self.tool_calls]
        return payload

    @classmethod
    def from_assistant(cls, message: AssistantMessage) -> "ChatMessage":
        return cls(
            role="assistant",
            content=message.content,
            reasoning_content=message.reasoning_content,
            estimated_token_count=None,
            tool_calls=message.tool_calls,
        )
