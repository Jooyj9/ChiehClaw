from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.ai.message import ToolCall
from app.agent.tools import Tool
from app.runtime.workspace_context import get_workspace_dir
from app.utils.logger import append_jsonl
from app.utils.paths import resolve_workspace_path


PATH_ARGUMENTS = {
    "read_file": ("path",),
    "write_file": ("path",),
    "edit_file": ("path",),
    "ls": ("path",),
    "run_shell": ("cwd",),
}
SENSITIVE_KEYWORDS = ("secret", "token", "password", "api_key", "apikey", "authorization", "access_key")
SECRET_PATTERNS = (
    re.compile(r"(?i)(api[_-]?key|token|secret|password|authorization)\s*[:=]\s*([^\s,;]+)"),
    re.compile(r"(?i)(bearer\s+)[a-z0-9._\-]+"),
    re.compile(r"\b(sk|ak|pk)-[A-Za-z0-9_\-]{16,}\b"),
)


class ToolExecutor:
    def __init__(
        self,
        tools: list[Tool],
        *,
        workspace_root_dir: Path | None = None,
        audit_log_path: Path | None = None,
        max_result_chars: int = 4000,
        max_field_chars: int = 2000,
    ) -> None:
        self._tools = {tool.name: tool for tool in tools}
        self._workspace_root_dir = workspace_root_dir
        self._audit_log_path = audit_log_path
        self._max_result_chars = max_result_chars
        self._max_field_chars = max_field_chars

    def execute(self, tool_call: ToolCall) -> dict[str, Any]:
        tool = self._tools.get(tool_call.name)
        if tool is None:
            return self.after_tool_call(
                tool_call,
                {"ok": False, "tool": tool_call.name, "error": f"Unknown tool: {tool_call.name}"},
            )

        try:
            self.before_tool_call(tool_call)
            result = tool.execute(**tool_call.arguments)
            raw_result = {"ok": True, "tool": tool_call.name, "result": result}
        except Exception as exc:
            raw_result = {"ok": False, "tool": tool_call.name, "error": str(exc)}
        return self.after_tool_call(tool_call, raw_result)

    def before_tool_call(self, tool_call: ToolCall) -> None:
        if self._workspace_root_dir is None:
            return

        workspace_dir = get_workspace_dir(self._workspace_root_dir)
        for argument_name in PATH_ARGUMENTS.get(tool_call.name, ()):
            raw_path = tool_call.arguments.get(argument_name)
            if raw_path is None:
                continue
            if not isinstance(raw_path, str):
                raise ValueError(f"{argument_name} must be a string path")
            resolve_workspace_path(workspace_dir, raw_path)

    def after_tool_call(self, tool_call: ToolCall, raw_result: dict[str, Any]) -> dict[str, Any]:
        original_chars = len(self.serialize_result(raw_result))
        sanitized_result, redacted_count, truncated_count = self._sanitize(raw_result)
        summary = self._summarize_result(sanitized_result)
        result = {
            **sanitized_result,
            "summary": summary,
        }
        result = self._fit_result_budget(result)
        self._write_audit_log(
            tool_call=tool_call,
            result=result,
            stats={
                "original_chars": original_chars,
                "serialized_chars": len(self.serialize_result(result)),
                "redacted_fields": redacted_count,
                "truncated_fields": truncated_count,
            },
        )
        return result

    @staticmethod
    def serialize_result(result: dict[str, Any]) -> str:
        return json.dumps(result, ensure_ascii=False, indent=2)

    def _sanitize(self, value: Any) -> tuple[Any, int, int]:
        redacted_count = 0
        truncated_count = 0

        def visit(current: Any, key: str = "") -> Any:
            nonlocal redacted_count, truncated_count

            if self._is_sensitive_key(key):
                redacted_count += 1
                return "[REDACTED]"
            if isinstance(current, dict):
                return {item_key: visit(item_value, str(item_key)) for item_key, item_value in current.items()}
            if isinstance(current, list):
                return [visit(item) for item in current]
            if isinstance(current, str):
                redacted = self._redact_string(current)
                if redacted != current:
                    redacted_count += 1
                if len(redacted) > self._max_field_chars:
                    truncated_count += 1
                    return self._truncate(redacted, self._max_field_chars)
                return redacted
            return current

        return visit(value), redacted_count, truncated_count

    def _fit_result_budget(self, result: dict[str, Any]) -> dict[str, Any]:
        serialized = self.serialize_result(result)
        if len(serialized) <= self._max_result_chars:
            return result

        compact = {
            "ok": result.get("ok", False),
            "tool": result.get("tool"),
            "error": result.get("error"),
            "summary": {
                **dict(result.get("summary") or {}),
                "result_truncated": True,
                "truncated_reason": f"serialized tool result exceeded {self._max_result_chars} chars",
            },
        }
        if "result" in result:
            compact["result"] = self._preview_value(result["result"])
        return compact

    def _write_audit_log(self, tool_call: ToolCall, result: dict[str, Any], stats: dict[str, Any]) -> None:
        if self._audit_log_path is None:
            return

        append_jsonl(
            self._audit_log_path,
            {
                "ts": datetime.now(UTC).isoformat(),
                "tool_call_id": tool_call.id,
                "tool_name": tool_call.name,
                "ok": bool(result.get("ok")),
                "summary": result.get("summary", {}),
                "stats": stats,
            },
        )

    def _summarize_result(self, result: dict[str, Any]) -> dict[str, Any]:
        payload = result.get("result")
        summary: dict[str, Any] = {"ok": bool(result.get("ok")), "tool": result.get("tool")}
        if result.get("error"):
            summary["error"] = self._truncate(str(result["error"]), 300)
        if isinstance(payload, dict):
            summary["result_keys"] = sorted(str(key) for key in payload.keys())
            for key in ("path", "cwd", "workspace", "container_cwd", "execution_mode", "returncode", "bytes_written"):
                if key in payload:
                    summary[key] = payload[key]
            for key in ("content", "stdout", "stderr"):
                if isinstance(payload.get(key), str):
                    summary[f"{key}_chars"] = len(payload[key])
            if isinstance(payload.get("entries"), list):
                summary["entry_count"] = len(payload["entries"])
        else:
            summary["result_type"] = type(payload).__name__
        return summary

    def _preview_value(self, value: Any) -> Any:
        if isinstance(value, dict):
            preview: dict[str, Any] = {}
            for key in ("path", "cwd", "workspace", "container_cwd", "execution_mode", "returncode", "bytes_written"):
                if key in value:
                    preview[key] = value[key]
            for key in ("content", "stdout", "stderr"):
                if isinstance(value.get(key), str):
                    preview[key] = self._truncate(value[key], 500)
            if "entries" in value and isinstance(value["entries"], list):
                preview["entries_preview"] = value["entries"][:20]
                preview["entry_count"] = len(value["entries"])
            return preview
        if isinstance(value, str):
            return self._truncate(value, 500)
        return value

    @staticmethod
    def _is_sensitive_key(key: str) -> bool:
        lowered = key.lower()
        return any(keyword in lowered for keyword in SENSITIVE_KEYWORDS)

    @staticmethod
    def _redact_string(value: str) -> str:
        redacted = value
        redacted = SECRET_PATTERNS[0].sub(r"\1=[REDACTED]", redacted)
        redacted = SECRET_PATTERNS[1].sub(r"\1[REDACTED]", redacted)
        redacted = SECRET_PATTERNS[2].sub("[REDACTED]", redacted)
        return redacted

    @staticmethod
    def _truncate(value: str, max_chars: int) -> str:
        if len(value) <= max_chars:
            return value
        return f"{value[:max_chars]}\n...[truncated {len(value) - max_chars} chars]..."
