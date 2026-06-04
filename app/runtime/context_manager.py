from __future__ import annotations

import re
from dataclasses import dataclass

from app.ai.message import ChatMessage
from app.agent.session import Session
from app.runtime.token_counter import TokenCounter


@dataclass(slots=True)
class MemoryCandidate:
    index: int
    score: float
    text: str


class ContextManager:
    def __init__(
        self,
        max_tokens: int | None = 32000,
        recent_tokens: int = 20000,
        older_memory_tokens: int = 5000,
        tool_result_max_chars: int = 4000,
        assistant_reasoning_max_chars: int = 2000,
        enable_runtime_context: bool = True,
        enable_older_memory: bool = True,
    ) -> None:
        self.max_tokens = max_tokens
        self.recent_tokens = recent_tokens
        self.older_memory_tokens = older_memory_tokens
        self.tool_result_max_chars = tool_result_max_chars
        self.assistant_reasoning_max_chars = assistant_reasoning_max_chars
        self.enable_runtime_context = enable_runtime_context
        self.enable_older_memory = enable_older_memory
        self.token_counter = TokenCounter()

    def build_messages(self, session: Session) -> list[ChatMessage]:
        self.update_pinned_context(session)
        self.ensure_message_token_counts(session.messages)

        runtime_message = self._build_runtime_message(session)
        project_memory_message = self._build_project_memory_message(session)
        sanitized = [self._sanitize_message(message) for message in session.messages]
        self.ensure_message_token_counts(sanitized)
        total_tokens = (
            sum(self._message_tokens(message) for message in sanitized)
            + self._message_tokens(runtime_message)
            + self._message_tokens(project_memory_message)
        )

        should_trim = self.max_tokens is not None and total_tokens > self.max_tokens
        if not should_trim:
            self._write_context_metadata(
                session=session,
                recent_messages=sanitized,
                older_memory=None,
                pinned_message=None,
                runtime_message=runtime_message,
                project_memory_message=project_memory_message,
                trimmed_message_count=0,
                total_tokens=total_tokens,
            )
            return self._prepend_fixed_messages(runtime_message, project_memory_message, sanitized)

        pinned_message = self._build_pinned_message(session)
        fixed_tokens = (
            self._message_tokens(pinned_message)
            + self._message_tokens(runtime_message)
            + self._message_tokens(project_memory_message)
        )
        recent_budget = self._recent_budget(fixed_tokens)
        split_index = self._recent_split_index(sanitized, token_budget=recent_budget)
        older_source = sanitized[:split_index]
        recent_messages = sanitized[split_index:]
        older_memory = None
        if self.enable_older_memory:
            older_memory = self._build_older_memory(
                messages=older_source,
                query=self._latest_user_query(session),
                recent_messages=recent_messages,
                pinned_message=pinned_message,
            )

        self._write_context_metadata(
            session=session,
            recent_messages=recent_messages,
            older_memory=older_memory,
            pinned_message=pinned_message,
            runtime_message=runtime_message,
            project_memory_message=project_memory_message,
            trimmed_message_count=len(older_source),
            total_tokens=total_tokens,
        )

        result: list[ChatMessage] = self._prepend_fixed_messages(runtime_message, project_memory_message, [])
        if pinned_message:
            result.append(pinned_message)
        if older_memory:
            result.append(older_memory)
        result.extend(recent_messages)
        return result

    def update_pinned_context(self, session: Session) -> None:
        pinned = dict(session.metadata.get("pinned_context") or {})
        user_messages = [message for message in session.messages if message.role == "user" and message.content.strip()]

        if user_messages and not pinned.get("initial_goal"):
            pinned["initial_goal"] = self._shorten(user_messages[0].content, 220)
        # Recent user requests are already kept by the recent window; duplicating them
        # in pinned context makes prompts longer without adding much signal.
        pinned.pop("open_tasks", None)

        key_files = set(pinned.get("key_files") or [])
        for message in session.messages:
            text = self._message_text(message)
            key_files.update(self._extract_file_paths(text))

        pinned["key_files"] = sorted(key_files)[-12:]
        pinned.pop("constraints", None)
        session.metadata["pinned_context"] = pinned

    def ensure_message_token_counts(self, messages: list[ChatMessage]) -> None:
        for message in messages:
            if message.estimated_token_count is None:
                message.estimated_token_count = self.token_counter.count_message(message)

    def _sanitize_message(self, message: ChatMessage) -> ChatMessage:
        content = message.content
        reasoning_content = message.reasoning_content

        if message.role == "tool" and len(content) > self.tool_result_max_chars:
            content = self._truncate(content, self.tool_result_max_chars, "\n...[tool result truncated]...")
        if message.role == "assistant" and len(reasoning_content) > self.assistant_reasoning_max_chars:
            reasoning_content = self._truncate(
                reasoning_content,
                self.assistant_reasoning_max_chars,
                "\n...[reasoning truncated]...",
            )

        sanitized = ChatMessage(
            role=message.role,
            content=content,
            reasoning_content=reasoning_content,
            name=message.name,
            tool_call_id=message.tool_call_id,
            tool_calls=message.tool_calls,
        )
        if content == message.content and reasoning_content == message.reasoning_content:
            sanitized.estimated_token_count = message.estimated_token_count
        else:
            sanitized.estimated_token_count = self.token_counter.count_message(sanitized)
        return sanitized

    def _build_pinned_message(self, session: Session) -> ChatMessage | None:
        pinned = session.metadata.get("pinned_context") or {}
        lines = ["Pinned context:"]
        if pinned.get("initial_goal"):
            lines.append(f"- initial goal: {pinned['initial_goal']}")
        if pinned.get("key_files"):
            lines.append("- key files: " + ", ".join(pinned["key_files"][-6:]))
        if len(lines) == 1:
            return None

        return self._synthetic_message("pinned_context", "\n".join(lines))

    def _build_runtime_message(self, session: Session) -> ChatMessage | None:
        if not self.enable_runtime_context:
            return None

        workspace_dir = str(session.metadata.get("workspace_dir") or "").strip()
        if not workspace_dir:
            return None

        shell_mode = str(session.metadata.get("shell_execution_mode") or "local")
        lines = [
            "Runtime context:",
            f"- current conversation workspace: {workspace_dir}",
            "- When the user asks for the current directory, answer this workspace path.",
            "- read_file/write_file/edit_file/ls are scoped to this workspace.",
            "- If shell mode is docker, /workspace inside the sandbox maps to this same directory.",
            "- Older /app paths in chat history are stale service paths; do not treat /app as the current workspace.",
            f"- shell execution mode: {shell_mode}",
        ]
        message = ChatMessage(role="system", content="\n".join(lines))
        message.estimated_token_count = self.token_counter.count_message(message)
        return message

    def _build_project_memory_message(self, session: Session) -> ChatMessage | None:
        content = str(session.metadata.get("project_memory") or "").strip()
        if not content:
            return None

        message = ChatMessage(
            role="system",
            content=(
                "Project long-term memory from XAGENTS.md:\n"
                "These are stable project-level notes. Follow them unless they conflict with the base system prompt.\n\n"
                f"{content}"
            ),
        )
        message.estimated_token_count = self.token_counter.count_message(message)
        return message
    # can by RAG
    def _build_older_memory(
        self,
        messages: list[ChatMessage],
        query: str,
        recent_messages: list[ChatMessage],
        pinned_message: ChatMessage | None,
    ) -> ChatMessage | None:
        budget = self._older_memory_budget()
        if budget <= 0 or not messages:
            return None

        blocked_keywords = self._keywords(self._message_text(pinned_message) if pinned_message else "")
        blocked_keywords |= self._keywords("\n".join(self._message_text(message) for message in recent_messages))
        query_keywords = self._keywords(query)
        total = max(1, len(messages))
        candidates: list[MemoryCandidate] = []

        for index, message in enumerate(messages):
            text = self._message_text(message)
            keywords = self._keywords(text)
            relevance = len(query_keywords & keywords)
            importance = self._importance_score(message, text)
            if relevance <= 0 and importance <= 0:
                continue
            if relevance == 0 and keywords and self._overlap_ratio(keywords, blocked_keywords) > 0.7:
                continue

            recency = (index + 1) / total
            token_penalty = min(self._message_tokens(message) / max(1, budget), 2.0)
            score = relevance * 4 + importance * 3 + recency - token_penalty
            snippet = self._extract_snippet(text=text, query_keywords=query_keywords, char_budget=260)
            candidates.append(MemoryCandidate(index=index, score=score, text=snippet))

        if not candidates:
            return None

        lines = ["Older memory:"]
        used = self.token_counter.count_text("\n".join(lines))
        selected = sorted(candidates, key=lambda candidate: (-candidate.score, -candidate.index))
        for candidate in selected:
            line = f"- {candidate.text}"
            line_tokens = self.token_counter.count_text(line)
            if used + line_tokens > budget:
                continue
            lines.append(line)
            used += line_tokens
            if len(lines) >= 7:
                break

        if len(lines) == 1:
            return None
        return self._synthetic_message("older_memory", "\n".join(lines))

    def _write_context_metadata(
        self,
        session: Session,
        recent_messages: list[ChatMessage],
        older_memory: ChatMessage | None,
        pinned_message: ChatMessage | None,
        runtime_message: ChatMessage | None,
        project_memory_message: ChatMessage | None,
        trimmed_message_count: int,
        total_tokens: int,
    ) -> None:
        pinned_tokens = self._message_tokens(pinned_message) if pinned_message else 0
        runtime_tokens = self._message_tokens(runtime_message) if runtime_message else 0
        project_memory_tokens = self._message_tokens(project_memory_message) if project_memory_message else 0
        older_memory_tokens = self._message_tokens(older_memory) if older_memory else 0
        recent_tokens = sum(self._message_tokens(message) for message in recent_messages)
        session.metadata["older_memory"] = older_memory.content if older_memory else ""
        session.metadata["context_stats"] = {
            "estimated_total_tokens": total_tokens,
            "runtime_tokens": runtime_tokens,
            "project_memory_tokens": project_memory_tokens,
            "pinned_tokens": pinned_tokens,
            "older_memory_tokens": older_memory_tokens,
            "recent_tokens": recent_tokens,
            "trimmed_message_count": trimmed_message_count,
        }

    @staticmethod
    def _prepend_fixed_messages(
        runtime_message: ChatMessage | None,
        project_memory_message: ChatMessage | None,
        messages: list[ChatMessage],
    ) -> list[ChatMessage]:
        fixed_messages = [message for message in (runtime_message, project_memory_message) if message]
        return [*fixed_messages, *messages]

    def _recent_split_index(self, messages: list[ChatMessage], token_budget: int) -> int:
        used = 0
        split_index = len(messages)
        for index in range(len(messages) - 1, -1, -1):
            message_tokens = self._message_tokens(messages[index])
            if used and used + message_tokens > token_budget:
                break
            used += message_tokens
            split_index = index
        return max(1, split_index)

    def _recent_budget(self, pinned_tokens: int) -> int:
        if self.max_tokens is None:
            return self.recent_tokens
        remaining = self.max_tokens - pinned_tokens - self._older_memory_budget()
        return max(1, min(self.recent_tokens, remaining))

    def _older_memory_budget(self) -> int:
        if not self.enable_older_memory:
            return 0
        if self.max_tokens is None:
            return self.older_memory_tokens
        return min(self.older_memory_tokens, max(1, self.max_tokens // 4))

    def _message_tokens(self, message: ChatMessage | None) -> int:
        if message is None:
            return 0
        if message.estimated_token_count is None:
            message.estimated_token_count = self.token_counter.count_message(message)
        return message.estimated_token_count

    def _synthetic_message(self, name: str, content: str) -> ChatMessage:
        message = ChatMessage(role="assistant", name=name, content=content)
        message.estimated_token_count = self.token_counter.count_message(message)
        return message

    @staticmethod
    def _message_text(message: ChatMessage | None) -> str:
        if message is None:
            return ""
        parts = [message.content, message.reasoning_content, message.name or ""]
        for tool_call in message.tool_calls:
            parts.append(tool_call.name)
            parts.append(str(tool_call.arguments))
        return "\n".join(parts)

    @staticmethod
    def _importance_score(message: ChatMessage, text: str) -> int:
        score = 0
        lowered = text.lower()
        if message.role == "tool":
            score += 2
        if message.tool_calls:
            score += 2
        if ContextManager._extract_file_paths(text):
            score += 2
        if any(word in lowered for word in ("error", "failed", "exception", "traceback", "timeout")):
            score += 3
            if ContextManager._has_constraint_marker(text):
                score += 1
        return score

    def _extract_snippet(self, text: str, query_keywords: set[str], char_budget: int) -> str:
        compact = " ".join(text.split())
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        matched = [line for line in lines if query_keywords & self._keywords(line)]
        source = " ... ".join(matched[:3]) if matched else compact
        terms = sorted(query_keywords & self._keywords(source))
        if terms:
            source = f"matched terms: {', '.join(terms[:5])}. {source}"
        return self._shorten(source, char_budget)

    @staticmethod
    def _extract_file_paths(text: str) -> list[str]:
        pattern = r"[A-Za-z0-9_.\-/\\]+\.(?:py|md|json|toml|yaml|yml|txt|c|java|ts|tsx|js)"
        return [item.replace("\\", "/") for item in re.findall(pattern, text)]

    @staticmethod
    def _has_constraint_marker(text: str) -> bool:
        lowered = text.lower()
        return any(marker in lowered for marker in ("must", "should", "only", "never", "do not", "constraint")) or any(
            marker in text for marker in ("必须", "不要", "不能", "只", "约束", "要求")
        )

    @staticmethod
    def _keywords(text: str) -> set[str]:
        keywords: set[str] = set()
        for item in re.findall(r"[A-Za-z0-9_.\-/\\]+", text):
            normalized = item.strip("._-/\\").lower()
            if len(normalized) < 2:
                continue
            keywords.add(normalized)
            if "/" in normalized or "\\" in normalized:
                keywords.update(part for part in re.split(r"[/\\]", normalized) if len(part) >= 2)
        return keywords

    @staticmethod
    def _overlap_ratio(left: set[str], right: set[str]) -> float:
        if not left or not right:
            return 0.0
        return len(left & right) / len(left)

    @staticmethod
    def _dedupe_keep_order(items: list[str]) -> list[str]:
        seen: set[str] = set()
        deduped: list[str] = []
        for item in items:
            if item in seen:
                continue
            seen.add(item)
            deduped.append(item)
        return deduped

    @staticmethod
    def _latest_user_query(session: Session) -> str:
        for message in reversed(session.messages):
            if message.role == "user" and message.content.strip():
                return message.content
        return ""

    @staticmethod
    def _shorten(text: str, limit: int) -> str:
        text = " ".join(text.split())
        if len(text) <= limit:
            return text
        return text[: max(0, limit - 3)] + "..."

    @staticmethod
    def _truncate(text: str, limit: int, suffix: str) -> str:
        if len(text) <= limit:
            return text
        keep = max(0, limit - len(suffix))
        return text[:keep] + suffix
