from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.ai.base import LLMClient
from app.ai.message import AssistantMessage, ChatMessage
from app.agent.session import Session
from app.agent.tool_executor import ToolExecutor
from app.agent.tools import Tool


class AgentLoop:
    def __init__(self, llm_client: LLMClient, tool_executor: ToolExecutor, max_rounds: int = 6) -> None:
        self.llm_client = llm_client
        self.tool_executor = tool_executor
        self.max_rounds = max_rounds

    def run(
        self,
        session: Session,
        tools: list[Tool],
        message_builder: Callable[[Session], list[ChatMessage]] | None = None,
        event_sink: Any | None = None,
        on_content_delta: Callable[[str], None] | None = None,
        on_reasoning_delta: Callable[[str], None] | None = None,
        on_tool_call_delta: Callable[[str], None] | None = None,
    ) -> AssistantMessage:
        for round_index in range(self.max_rounds):
            llm_messages = message_builder(session) if message_builder else session.messages
            self._emit(
                event_sink,
                "llm_call_started",
                round_index=round_index,
                message_count=len(llm_messages),
                tool_names=[tool.name for tool in tools],
            )
            try:
                if on_content_delta or on_reasoning_delta or on_tool_call_delta:
                    response = self.llm_client.stream(
                        messages=llm_messages,
                        tools=tools,
                        system_prompt=session.system_prompt,
                        on_content_delta=on_content_delta,
                        on_reasoning_delta=on_reasoning_delta,
                        on_tool_call_delta=on_tool_call_delta,
                    )
                else:
                    response = self.llm_client.generate(
                        messages=llm_messages,
                        tools=tools,
                        system_prompt=session.system_prompt,
                    )
            except Exception as exc:
                self._emit(event_sink, "llm_call_failed", round_index=round_index, error=str(exc))
                raise

            self._emit(
                event_sink,
                "llm_call_succeeded",
                round_index=round_index,
                tool_call_count=len(response.tool_calls),
                content_chars=len(response.content),
            )

            session.add_assistant_message(response)
            self._emit(
                event_sink,
                "assistant_message_appended",
                round_index=round_index,
                tool_call_count=len(response.tool_calls),
            )

            if not response.tool_calls:
                return response

            tool_results = []
            for tool_call in response.tool_calls:
                self._emit(
                    event_sink,
                    "tool_call_planned",
                    round_index=round_index,
                    tool_call_id=tool_call.id,
                    tool_name=tool_call.name,
                    arguments=tool_call.arguments,
                )
                self._emit(
                    event_sink,
                    "tool_call_started",
                    round_index=round_index,
                    tool_call_id=tool_call.id,
                    tool_name=tool_call.name,
                    arguments=tool_call.arguments,
                )
                result = self.tool_executor.execute(tool_call)
                tool_results.append((tool_call, result))

            fitted_results = self.tool_executor.fit_turn_result_budget(tool_results)
            for tool_call, result in zip(response.tool_calls, fitted_results, strict=True):
                event_type = "tool_call_succeeded" if result.get("ok") else "tool_call_failed"
                self._emit(
                    event_sink,
                    event_type,
                    round_index=round_index,
                    tool_call_id=tool_call.id,
                    tool_name=tool_call.name,
                    result=result,
                )
                session.add_tool_result(
                    tool_call_id=tool_call.id,
                    tool_name=tool_call.name,
                    content=self.tool_executor.serialize_result(result),
                )
                self._emit(
                    event_sink,
                    "tool_result_appended",
                    round_index=round_index,
                    tool_call_id=tool_call.id,
                    tool_name=tool_call.name,
                    ok=bool(result.get("ok")),
                )

        timeout_message = AssistantMessage(content="Stopped after reaching the maximum tool-call rounds.")
        session.add_assistant_message(timeout_message)
        self._emit(event_sink, "turn_max_rounds_reached", max_rounds=self.max_rounds)
        return timeout_message

    @staticmethod
    def _emit(event_sink: Any | None, event_type: str, **payload: Any) -> None:
        if event_sink is None:
            return
        event_sink.emit(event_type, **payload)
