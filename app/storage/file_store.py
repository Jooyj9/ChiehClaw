from __future__ import annotations

from pathlib import Path

from app.storage.naming import conversation_storage_name


class ConversationFileStore:
    def __init__(self, files_dir: Path) -> None:
        self.files_dir = files_dir
        self.files_dir.mkdir(parents=True, exist_ok=True)

    def conversation_dir(self, conversation_id: str) -> Path:
        target = self.files_dir / conversation_storage_name(conversation_id)
        target.mkdir(parents=True, exist_ok=True)
        return target
