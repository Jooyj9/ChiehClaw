from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.storage.naming import conversation_storage_name
from app.utils.json_io import read_jsonl


TERMINAL_TURN_EVENTS = {"turn_succeeded", "turn_failed", "turn_recovered"}


class EventLog:
    def __init__(self, events_dir: Path) -> None:
        self.events_dir = events_dir
        self.events_dir.mkdir(parents=True, exist_ok=True)

    def append(self, conversation_id: str, event: dict[str, Any]) -> None:
        payload = {
            "event_id": str(uuid.uuid4()),
            "conversation_id": conversation_id,
            "ts": datetime.now(UTC).isoformat(),
            **event,
        }
        path = self._path_for(conversation_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def read(self, conversation_id: str) -> list[dict[str, Any]]:
        return self._read_path(self._path_for(conversation_id))

    def compact_completed_turns(self, conversation_id: str, keep_completed_turns: int) -> int:
        if keep_completed_turns <= 0:
            return 0

        path = self._path_for(conversation_id)
        events = self._read_path(path)
        if not events:
            return 0

        turn_order: list[str] = []
        turn_events: dict[str, list[dict[str, Any]]] = {}
        keep_events_without_turn: list[dict[str, Any]] = []

        for event in events:
            turn_id = str(event.get("turn_id") or "")
            if not turn_id:
                keep_events_without_turn.append(event)
                continue
            if turn_id not in turn_events:
                turn_order.append(turn_id)
                turn_events[turn_id] = []
            turn_events[turn_id].append(event)

        completed_turns = [
            turn_id
            for turn_id in turn_order
            if any(str(event.get("type") or "") in TERMINAL_TURN_EVENTS for event in turn_events[turn_id])
        ]
        retained_completed = set(completed_turns[-keep_completed_turns:])

        retained: list[dict[str, Any]] = [*keep_events_without_turn]
        for turn_id in turn_order:
            is_completed = turn_id in completed_turns
            if not is_completed or turn_id in retained_completed:
                retained.extend(turn_events[turn_id])

        removed_count = len(events) - len(retained)
        if removed_count <= 0:
            return 0

        temp_path = path.with_suffix(".jsonl.tmp")
        with temp_path.open("w", encoding="utf-8") as file:
            for event in retained:
                file.write(json.dumps(event, ensure_ascii=False) + "\n")
        temp_path.replace(path)
        return removed_count

    def iter_conversations(self) -> Iterator[str]:
        for path in sorted(self.events_dir.glob("*.jsonl")):
            events = self._read_path(path)
            if events:
                yield str(events[0].get("conversation_id") or path.stem)
            else:
                yield path.stem

    def _path_for(self, conversation_id: str) -> Path:
        return self.events_dir / f"{conversation_storage_name(conversation_id)}.jsonl"

    @staticmethod
    def _read_path(path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []

        return read_jsonl(path)


class TurnEventSink:
    def __init__(self, event_log: EventLog, conversation_id: str, turn_id: str) -> None:
        self.event_log = event_log
        self.conversation_id = conversation_id
        self.turn_id = turn_id

    def emit(self, event_type: str, **payload: Any) -> None:
        self.event_log.append(
            self.conversation_id,
            {
                "turn_id": self.turn_id,
                "type": event_type,
                **payload,
            },
        )
