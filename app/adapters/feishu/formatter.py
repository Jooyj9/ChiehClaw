from __future__ import annotations

import json


class FeishuFormatter:
    def __init__(self, max_reply_chars: int = 4000) -> None:
        self.max_reply_chars = max_reply_chars
    # convert to feishu-dict obj, when sending req convert to json finally
    def format_reply(self, text: str) -> dict[str, str]:
        normalized = (text or "").strip() or "已处理，但当前没有可返回的文本结果。"
        suffix = "\n...[reply truncated]"
        if len(normalized) > self.max_reply_chars:
            normalized = normalized[: max(0, self.max_reply_chars - len(suffix))] + suffix

        return {
            "msg_type": "text",
            "content": json.dumps({"text": normalized}, ensure_ascii=False),
        }
