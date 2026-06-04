from __future__ import annotations

from dataclasses import dataclass, field

from app.ai.message import ChatMessage


@dataclass(slots=True)
class TurnResult:
    conversation_id: str
    reply: str
    messages: list[ChatMessage] = field(default_factory=list)

