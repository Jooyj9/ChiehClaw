from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from app.storage.naming import conversation_storage_name
from app.utils.json_io import read_json, write_json


@dataclass(slots=True)
class SessionNode:
    session_id: str
    title: str
    created_at: str
    updated_at: str


@dataclass(slots=True)
class SessionTree:
    workspace_id: str
    active_session_id: str
    sessions: list[SessionNode]


class JsonSessionTreeStore:
    """Maps one workspace/channel identity to multiple switchable sessions."""

    def __init__(self, trees_dir: Path) -> None:
        self.trees_dir = trees_dir
        self.trees_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def get_or_create(self, workspace_id: str) -> SessionTree:
        with self._lock:
            tree = self._load(workspace_id)
            if tree is not None:
                return tree

            now = self._now()
            tree = SessionTree(
                workspace_id=workspace_id,
                active_session_id=workspace_id,
                sessions=[
                    SessionNode(
                        session_id=workspace_id,
                        title="Existing session",
                        created_at=now,
                        updated_at=now,
                    )
                ],
            )
            self._save(tree)
            return tree

    def create_session(self, workspace_id: str) -> SessionNode:
        with self._lock:
            tree = self.get_or_create(workspace_id)
            now = self._now()
            node = SessionNode(
                session_id=self._new_session_id(workspace_id),
                title="New session",
                created_at=now,
                updated_at=now,
            )
            tree.sessions.append(node)
            tree.active_session_id = node.session_id
            self._save(tree)
            return node

    def list_sessions(self, workspace_id: str) -> tuple[str, list[SessionNode]]:
        with self._lock:
            tree = self.get_or_create(workspace_id)
            sessions = sorted(tree.sessions, key=lambda node: node.updated_at, reverse=True)
            return tree.active_session_id, sessions

    def switch_session(self, workspace_id: str, selector: str) -> SessionNode | None:
        with self._lock:
            tree = self.get_or_create(workspace_id)
            sessions = sorted(tree.sessions, key=lambda node: node.updated_at, reverse=True)
            selected: SessionNode | None = None

            if selector.isdigit():
                index = int(selector) - 1
                if 0 <= index < len(sessions):
                    selected = sessions[index]
            else:
                selected = next((node for node in tree.sessions if node.session_id == selector), None)

            if selected is None:
                return None

            selected.updated_at = self._now()
            tree.active_session_id = selected.session_id
            self._save(tree)
            return selected

    def touch(self, workspace_id: str, session_id: str, title: str | None = None) -> None:
        with self._lock:
            tree = self.get_or_create(workspace_id)
            node = next((item for item in tree.sessions if item.session_id == session_id), None)
            if node is None:
                now = self._now()
                node = SessionNode(
                    session_id=session_id,
                    title=title or "Existing session",
                    created_at=now,
                    updated_at=now,
                )
                tree.sessions.append(node)

            node.updated_at = self._now()
            if title and node.title in {"Existing session", "New session"}:
                node.title = self._shorten(title, 80)
            tree.active_session_id = session_id
            self._save(tree)

    def _load(self, workspace_id: str) -> SessionTree | None:
        path = self._path_for(workspace_id)
        if not path.exists():
            return None

        payload = read_json(path)
        return SessionTree(
            workspace_id=str(payload.get("workspace_id") or workspace_id),
            active_session_id=str(payload["active_session_id"]),
            sessions=[
                SessionNode(
                    session_id=str(item["session_id"]),
                    title=str(item.get("title") or "Untitled session"),
                    created_at=str(item.get("created_at") or ""),
                    updated_at=str(item.get("updated_at") or ""),
                )
                for item in payload.get("sessions", [])
            ],
        )

    def _save(self, tree: SessionTree) -> None:
        write_json(
            self._path_for(tree.workspace_id),
            {
                "workspace_id": tree.workspace_id,
                "active_session_id": tree.active_session_id,
                "sessions": [
                    {
                        "session_id": node.session_id,
                        "title": node.title,
                        "created_at": node.created_at,
                        "updated_at": node.updated_at,
                    }
                    for node in tree.sessions
                ],
            },
        )

    def _path_for(self, workspace_id: str) -> Path:
        return self.trees_dir / f"{conversation_storage_name(workspace_id)}.json"

    @staticmethod
    def _new_session_id(workspace_id: str) -> str:
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        return f"{workspace_id}--s-{timestamp}-{uuid.uuid4().hex[:6]}"

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    @staticmethod
    def _shorten(text: str, limit: int) -> str:
        compact = " ".join(text.split())
        if len(compact) <= limit:
            return compact
        return compact[: limit - 3] + "..."
