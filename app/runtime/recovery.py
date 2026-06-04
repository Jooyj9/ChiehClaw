from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.runtime.event_log import EventLog


TERMINAL_TURN_EVENTS = {"turn_succeeded", "turn_failed", "turn_recovered"}
TERMINAL_TOOL_EVENTS = {"tool_call_succeeded", "tool_call_failed", "tool_call_skipped"}


@dataclass(slots=True)
class InterruptedToolCall:
    tool_call_id: str
    tool_name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class UnfinishedTurn:
    conversation_id: str
    turn_id: str
    interrupted_tools: list[InterruptedToolCall] = field(default_factory=list)


class RecoveryManager:
    def __init__(self, event_log: EventLog) -> None:
        self.event_log = event_log

    def scan_unfinished(self) -> list[UnfinishedTurn]:
        unfinished: list[UnfinishedTurn] = []
        for conversation_id in self.event_log.iter_conversations():
            unfinished.extend(self._scan_conversation(conversation_id))
        return unfinished

    def _scan_conversation(self, conversation_id: str) -> list[UnfinishedTurn]:
        turns: dict[str, list[dict[str, Any]]] = {}
        for event in self.event_log.read(conversation_id):
            turn_id = str(event.get("turn_id") or "")
            if not turn_id:
                continue
            turns.setdefault(turn_id, []).append(event)

        unfinished: list[UnfinishedTurn] = []
        for turn_id, events in turns.items():
            event_types = {str(event.get("type") or "") for event in events}
            if event_types & TERMINAL_TURN_EVENTS:
                continue

            unfinished.append(
                UnfinishedTurn(
                    conversation_id=conversation_id,
                    turn_id=turn_id,
                    interrupted_tools=self._find_interrupted_tools(events),
                )
            )
        return unfinished

    def _find_interrupted_tools(self, events: list[dict[str, Any]]) -> list[InterruptedToolCall]:
        started: dict[str, dict[str, Any]] = {}
        finished: set[str] = set()

        for event in events:
            tool_call_id = str(event.get("tool_call_id") or "")
            if not tool_call_id:
                continue

            event_type = str(event.get("type") or "")
            if event_type == "tool_call_started":
                started[tool_call_id] = event
            elif event_type in TERMINAL_TOOL_EVENTS:
                finished.add(tool_call_id)

        interrupted: list[InterruptedToolCall] = []
        for tool_call_id, event in started.items():
            if tool_call_id in finished:
                continue
            interrupted.append(
                InterruptedToolCall(
                    tool_call_id=tool_call_id,
                    tool_name=str(event.get("tool_name") or ""),
                    arguments=dict(event.get("arguments") or {}),
                )
            )
        return interrupted

