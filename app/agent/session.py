from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.ai.message import AssistantMessage, ChatMessage

# 即session状态,ChatMessage为session的最小unit
@dataclass(slots=True)
class Session:
    conversation_id: str
    system_prompt: str
    messages: list[ChatMessage] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def add_user_message(self, content: str) -> None:
        self.messages.append(ChatMessage(role="user", content=content))

    def add_assistant_message(self, message: AssistantMessage) -> None:
        self.messages.append(ChatMessage.from_assistant(message))

    def add_tool_result(self, tool_call_id: str, tool_name: str, content: str) -> None:
        self.messages.append(
            ChatMessage(
                role="tool",
                name=tool_name,
                tool_call_id=tool_call_id,
                content=content,
            )
        )
    # 写的时候，返回session的dict形式
    def to_dict(self) -> dict[str, Any]:
        return {
            "conversation_id": self.conversation_id,
            "system_prompt": self.system_prompt,
            "messages": [message.to_dict() for message in self.messages],
            "metadata": self.metadata,
        }

    # 读的时候将存的json(dict) - > session对象
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Session":
        return cls(
            conversation_id=data["conversation_id"],
            system_prompt=data.get("system_prompt", ""),
            messages=[ChatMessage.from_dict(item) for item in data.get("messages", [])],
            metadata=dict(data.get("metadata", {})),
        )

