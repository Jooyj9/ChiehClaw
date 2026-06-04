from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.request
from typing import Callable, Iterable

from app.ai.base import LLMClient
from app.ai.message import AssistantMessage, ChatMessage, ToolCall
from app.ai.models import ModelConfig


def _normalize_content(content: object) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
        return "\n".join(part for part in parts if part)
    return str(content)


def _parse_tool_calls(message: dict[str, object]) -> list[ToolCall]:
    return [
        ToolCall(
            id=str(item["id"]),
            name=str(item["function"]["name"]),
            arguments=json.loads(item["function"].get("arguments") or "{}"),
        )
        for item in message.get("tool_calls", [])
    ]


def _parse_assistant_message(message: dict[str, object]) -> AssistantMessage:
    return AssistantMessage(
        content=_normalize_content(message.get("content")),
        reasoning_content=_normalize_content(message.get("reasoning_content")),
        tool_calls=_parse_tool_calls(message),
    )


class OpenAICompatibleClient(LLMClient):
    def __init__(
        self,
        config: ModelConfig,
        base_url: str,
        api_key: str,
        request_timeout_seconds: int = 180,
        max_retries: int = 1,
    ) -> None:
        self.config = config
        self.base_url = base_url
        self.api_key = api_key
        self.request_timeout_seconds = request_timeout_seconds
        self.max_retries = max_retries

    def _build_request(self, body: dict[str, object]) -> urllib.request.Request:
        return urllib.request.Request(
            url=self.base_url,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

    @staticmethod
    def _is_timeout_error(exc: BaseException) -> bool:
        if isinstance(exc, (TimeoutError, socket.timeout)):
            return True
        if isinstance(exc, urllib.error.URLError):
            return isinstance(exc.reason, (TimeoutError, socket.timeout))
        return False

    def _build_body(
        self,
        messages: list[ChatMessage],
        tools: Iterable[object] | None = None,
        system_prompt: str | None = None,
        stream: bool = False,
    ) -> dict[str, object]:
        # history trimed messages
        payload_messages = [message.to_openai_dict() for message in messages]
        # compose sys_prompt
        if system_prompt:
            payload_messages = [{"role": "system", "content": system_prompt}, *payload_messages]

        # pack
        body: dict[str, object] = {
            "model": self.config.model,
            "messages": payload_messages,
            "temperature": 1,
        }
        if tools:
            body["tools"] = [tool.to_openai_schema() for tool in tools]
        if stream:
            body["stream"] = True
        return body

    def _request_json(self, body: dict[str, object]) -> dict[str, object]:
        attempts = self.max_retries + 1
        payload: dict[str, object] | None = None
        last_error: BaseException | None = None

        for attempt in range(attempts):
            request = self._build_request(body)
            try:
                with urllib.request.urlopen(request, timeout=self.request_timeout_seconds) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                break
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="ignore")
                raise RuntimeError(f"OpenAI request failed: {exc.code} {detail}") from exc
            except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
                last_error = exc
                if attempt + 1 < attempts:
                    time.sleep(min(2**attempt, 3))
                    continue
                if self._is_timeout_error(exc):
                    raise RuntimeError(
                        f"OpenAI request timed out after {self.request_timeout_seconds}s "
                        f"(retries: {self.max_retries})"
                    ) from exc
                if isinstance(exc, urllib.error.URLError):
                    raise RuntimeError(f"OpenAI request failed: {exc.reason}") from exc
                raise RuntimeError(f"OpenAI request failed: {exc}") from exc

        if payload is None:
            raise RuntimeError(f"OpenAI request failed: {last_error}")
        return payload

    def _stream_once(
        self,
        body: dict[str, object],
        on_content_delta: Callable[[str], None] | None = None,
        on_reasoning_delta: Callable[[str], None] | None = None,
        on_tool_call_delta: Callable[[str], None] | None = None,
    ) -> AssistantMessage:
        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        tool_buffers: dict[int, dict[str, str]] = {}
        announced_tool_names: set[tuple[int, str]] = set()

        request = self._build_request(body)
        with urllib.request.urlopen(request, timeout=self.request_timeout_seconds) as response:
            for raw_line in response:
                line = raw_line.decode("utf-8").strip()
                if not line or not line.startswith("data:"):
                    continue

                payload_text = line[5:].strip()
                if payload_text == "[DONE]":
                    break

                chunk = json.loads(payload_text)
                choice = chunk.get("choices", [{}])[0]
                delta = choice.get("delta", {})

                content_delta = _normalize_content(delta.get("content"))
                if content_delta:
                    content_parts.append(content_delta)
                    if on_content_delta:
                        on_content_delta(content_delta)

                reasoning_delta = _normalize_content(delta.get("reasoning_content"))
                if reasoning_delta:
                    reasoning_parts.append(reasoning_delta)
                    if on_reasoning_delta:
                        on_reasoning_delta(reasoning_delta)

                for item in delta.get("tool_calls", []):
                    index = int(item.get("index", 0))
                    buffer = tool_buffers.setdefault(index, {"id": "", "name": "", "arguments": ""})

                    item_id = item.get("id")
                    if item_id and not buffer["id"]:
                        buffer["id"] = str(item_id)

                    function = item.get("function", {})
                    function_name = function.get("name")
                    if function_name:
                        buffer["name"] += str(function_name)
                        key = (index, buffer["name"])
                        if on_tool_call_delta and key not in announced_tool_names:
                            announced_tool_names.add(key)
                            on_tool_call_delta(buffer["name"])

                    arguments_delta = function.get("arguments")
                    if arguments_delta:
                        buffer["arguments"] += str(arguments_delta)

        tool_calls: list[ToolCall] = []
        for _, buffer in sorted(tool_buffers.items()):
            arguments_text = buffer["arguments"].strip()
            if not arguments_text:
                arguments = {}
            else:
                try:
                    arguments = json.loads(arguments_text)
                except json.JSONDecodeError as exc:
                    raise RuntimeError(f"Failed to parse streamed tool arguments: {arguments_text}") from exc

            tool_calls.append(
                ToolCall(
                    id=buffer["id"] or "stream_tool_call",
                    name=buffer["name"],
                    arguments=arguments,
                )
            )

        return AssistantMessage(
            content="".join(content_parts),
            reasoning_content="".join(reasoning_parts),
            tool_calls=tool_calls,
        )

    def generate(
        self,
        messages: list[ChatMessage],
        tools: Iterable[object] | None = None,
        system_prompt: str | None = None,
    ) -> AssistantMessage:
        body = self._build_body(messages=messages, tools=tools, system_prompt=system_prompt, stream=False)
        payload = self._request_json(body)
        message = payload["choices"][0]["message"]
        return _parse_assistant_message(message)

    def stream(
        self,
        messages: list[ChatMessage],
        tools: Iterable[object] | None = None,
        system_prompt: str | None = None,
        on_content_delta: Callable[[str], None] | None = None,
        on_reasoning_delta: Callable[[str], None] | None = None,
        on_tool_call_delta: Callable[[str], None] | None = None,
    ) -> AssistantMessage:
        body = self._build_body(messages=messages, tools=tools, system_prompt=system_prompt, stream=True)
        attempts = self.max_retries + 1
        last_error: BaseException | None = None

        for attempt in range(attempts):
            emitted_any = False

            def content_handler(text: str) -> None:
                nonlocal emitted_any
                emitted_any = True
                if on_content_delta:
                    on_content_delta(text)

            def reasoning_handler(text: str) -> None:
                nonlocal emitted_any
                emitted_any = True
                if on_reasoning_delta:
                    on_reasoning_delta(text)

            def tool_call_handler(text: str) -> None:
                nonlocal emitted_any
                emitted_any = True
                if on_tool_call_delta:
                    on_tool_call_delta(text)

            try:
                return self._stream_once(
                    body=body,
                    on_content_delta=content_handler,
                    on_reasoning_delta=reasoning_handler,
                    on_tool_call_delta=tool_call_handler,
                )
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="ignore")
                raise RuntimeError(f"OpenAI request failed: {exc.code} {detail}") from exc
            except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
                last_error = exc
                if attempt + 1 < attempts and not emitted_any:
                    time.sleep(min(2**attempt, 3))
                    continue
                if self._is_timeout_error(exc):
                    raise RuntimeError(
                        f"OpenAI stream timed out after {self.request_timeout_seconds}s "
                        f"(retries: {self.max_retries})"
                    ) from exc
                if isinstance(exc, urllib.error.URLError):
                    raise RuntimeError(f"OpenAI request failed: {exc.reason}") from exc
                raise RuntimeError(f"OpenAI request failed: {exc}") from exc

        raise RuntimeError(f"OpenAI request failed: {last_error}")
