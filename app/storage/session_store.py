from __future__ import annotations

from pathlib import Path

from app.agent.session import Session
from app.storage.naming import conversation_storage_name
from app.utils.json_io import read_json, write_json

# 负责把会话从 JSON 读出来、再写回去。
class JsonSessionStore:
    def __init__(self, sessions_dir: Path) -> None:
        self.sessions_dir = sessions_dir
        self.sessions_dir.mkdir(parents=True, exist_ok=True)

    def load(self, conversation_id: str, system_prompt: str) -> Session:
        path = self._path_for(conversation_id)
        # 不存在session则创建塞sys-prompt
        if not path.exists():
            return Session(conversation_id=conversation_id, system_prompt=system_prompt)
        # 存在则读出session
        payload = read_json(path)
        session = Session.from_dict(payload)
        # 如果该session没有sys_prompt,将此次给的塞入
        if not session.system_prompt:
            session.system_prompt = system_prompt
        return session

    def save(self, session: Session) -> None:
        # 要保存先load，必有session对应的id
        path = self._path_for(session.conversation_id)
        write_json(path, session.to_dict())

    def _path_for(self, conversation_id: str) -> Path:
        return self.sessions_dir / f"{conversation_storage_name(conversation_id)}.json"
