from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class IncomingMessage:
    conversation_id: str
    message_id: str
    text: str
    chat_id: str
    chat_type: str
    sender_open_id: str
    thread_root_id: str = ""

