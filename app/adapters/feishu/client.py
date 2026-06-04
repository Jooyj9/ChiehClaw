from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass


@dataclass(slots=True)
class FeishuConfig:
    app_id: str
    app_secret: str
    verification_token: str = ""
    bot_open_id: str = ""
    base_url: str = "https://open.feishu.cn/open-apis/"
    host: str = "0.0.0.0"
    port: int = 8000
    webhook_path: str = "/feishu/webhook"
    request_timeout_seconds: int = 15
    reply_max_chars: int = 4000
    worker_count: int = 2
    sdk_log_level: str = "INFO"
    group_session_scope: str = "chat_user"
    console_stream: bool = True
    console_show_tool_status: bool = False

    @classmethod
    def from_env(cls) -> "FeishuConfig":
        import os
        from pathlib import Path

        from app.utils.env import load_dotenv_if_exists

        load_dotenv_if_exists(Path(__file__).resolve().parents[3])

        return cls(
            app_id=os.getenv("XXXCLAW_FEISHU_APP_ID", ""),
            app_secret=os.getenv("XXXCLAW_FEISHU_APP_SECRET", ""),
            verification_token=os.getenv("XXXCLAW_FEISHU_VERIFICATION_TOKEN", ""),
            bot_open_id=os.getenv("XXXCLAW_FEISHU_BOT_OPEN_ID", ""),
            base_url=os.getenv("XXXCLAW_FEISHU_BASE_URL", "https://open.feishu.cn/open-apis/"),
            host=os.getenv("XXXCLAW_FEISHU_HOST", "0.0.0.0"),
            port=int(os.getenv("XXXCLAW_FEISHU_PORT", "8000")),
            webhook_path=os.getenv("XXXCLAW_FEISHU_WEBHOOK_PATH", "/feishu/webhook"),
            request_timeout_seconds=int(os.getenv("XXXCLAW_FEISHU_TIMEOUT_SECONDS", "15")),
            reply_max_chars=int(os.getenv("XXXCLAW_FEISHU_REPLY_MAX_CHARS", "4000")),
            worker_count=int(os.getenv("XXXCLAW_FEISHU_WORKER_COUNT", "2")),
            sdk_log_level=os.getenv("XXXCLAW_FEISHU_LOG_LEVEL", "INFO"),
            group_session_scope=os.getenv("XXXCLAW_FEISHU_GROUP_SESSION_SCOPE", "chat_user"),
            console_stream=os.getenv("XXXCLAW_FEISHU_CONSOLE_STREAM", "1").lower() not in {"0", "false", "no"},
            console_show_tool_status=os.getenv("XXXCLAW_FEISHU_SHOW_TOOL_STATUS", "0").lower()
            in {"1", "true", "yes"},
        )

    def validate(self) -> None:
        if not self.app_id:
            raise ValueError("XXXCLAW_FEISHU_APP_ID is required.")
        if not self.app_secret:
            raise ValueError("XXXCLAW_FEISHU_APP_SECRET is required.")
        if self.webhook_path and not self.webhook_path.startswith("/"):
            raise ValueError("XXXCLAW_FEISHU_WEBHOOK_PATH must start with '/'.")
        if self.group_session_scope not in {"chat_user", "chat", "thread"}:
            raise ValueError("XXXCLAW_FEISHU_GROUP_SESSION_SCOPE must be one of: chat_user, chat, thread.")

# 用来给飞书发请求回信息
class FeishuAPIClient:
    def __init__(self, config: FeishuConfig) -> None:
        self.config = config
        self._tenant_access_token = ""
        self._tenant_access_token_expires_at = 0.0

    def reply_message(self, message_id: str, payload: dict[str, object]) -> dict[str, object]:
        token = self._get_tenant_access_token()
        return self._post(
            path=f"im/v1/messages/{message_id}/reply",
            body=payload,
            access_token=token,
        )

    def _get_tenant_access_token(self) -> str:
        now = time.time()
        if self._tenant_access_token and now < self._tenant_access_token_expires_at:
            return self._tenant_access_token

        payload = self._post(
            path="auth/v3/tenant_access_token/internal",
            body={
                "app_id": self.config.app_id,
                "app_secret": self.config.app_secret,
            },
        )
        self._tenant_access_token = str(payload.get("tenant_access_token") or "")
        if not self._tenant_access_token:
            raise RuntimeError("Feishu API request failed: tenant_access_token missing in response.")
        expire_seconds = int(payload.get("expire") or 7200)
        self._tenant_access_token_expires_at = now + max(60, expire_seconds - 60)
        return self._tenant_access_token

    def _post(
        self,
        path: str,
        body: dict[str, object],
        access_token: str | None = None,
    ) -> dict[str, object]:
        headers = {"Content-Type": "application/json; charset=utf-8"}
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"

        request = urllib.request.Request(
            url=self._build_url(path),
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=self.config.request_timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"Feishu API request failed: {exc.code} {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Feishu API request failed: {exc.reason}") from exc

        if int(payload.get("code") or 0) != 0:
            raise RuntimeError(f"Feishu API request failed: {payload.get('code')} {payload.get('msg')}")
        return payload

    def _build_url(self, path: str) -> str:
        base = self.config.base_url.rstrip("/")
        suffix = path.lstrip("/")
        return f"{base}/{suffix}"
