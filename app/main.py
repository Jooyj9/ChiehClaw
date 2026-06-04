from __future__ import annotations

import argparse
import sys

from app.runtime.runner import Runner


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the minimal xxxclaw coding agent.")
    parser.add_argument("--conversation-id", default="local-cli", help="Conversation ID for session persistence.")
    parser.add_argument("--prompt", help="Single-turn prompt. If omitted, starts interactive mode.")
    parser.add_argument("--no-stream", action="store_true", help="Disable streaming output and wait for the full reply.")
    parser.add_argument(
        "--show-tool-status",
        action="store_true",
        help="Show reasoning/tool-call status for assistant tool-call turns.",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    runner = Runner.from_env()
    runner.recover_unfinished_tasks()
    use_stream = not args.no_stream

    def stream_content(text: str) -> None:
        if not text:
            return
        sys.stdout.write(text)
        sys.stdout.flush()

    def execute_turn(user_input: str):
        if not use_stream:
            return runner.run_turn(conversation_id=args.conversation_id, user_input=user_input)

        streamed = False
        content_seen = False
        tool_status_started = False
        buffered_reasoning: list[str] = []
        shown_tools: set[str] = set()

        def print_tool_reasoning_chunk(text: str) -> None:
            if not text:
                return
            sys.stdout.write(text)
            sys.stdout.flush()

        def on_content_delta(text: str) -> None:
            nonlocal streamed, content_seen
            streamed = True
            content_seen = True
            stream_content(text)

        def on_reasoning_delta(text: str) -> None:
            nonlocal tool_status_started
            if not args.show_tool_status or content_seen:
                return
            if tool_status_started:
                print_tool_reasoning_chunk(text)
            else:
                buffered_reasoning.append(text)

        def on_tool_call_delta(tool_name: str) -> None:
            nonlocal streamed, tool_status_started
            if not args.show_tool_status or content_seen:
                return

            streamed = True
            if not tool_status_started:
                tool_status_started = True
                print("[tool-status] ", end="", flush=True)
                if buffered_reasoning:
                    print_tool_reasoning_chunk("".join(buffered_reasoning))
                    buffered_reasoning.clear()

            if tool_name and tool_name not in shown_tools:
                if buffered_reasoning:
                    print_tool_reasoning_chunk("".join(buffered_reasoning))
                    buffered_reasoning.clear()
                shown_tools.add(tool_name)
                print_tool_reasoning_chunk(f"\n[tool-call] {tool_name}\n")

        result = runner.run_turn_stream(
            conversation_id=args.conversation_id,
            user_input=user_input,
            on_content_delta=on_content_delta,
            on_reasoning_delta=on_reasoning_delta,
            on_tool_call_delta=on_tool_call_delta,
        )

        if streamed:
            print()
        elif result.reply:
            print(result.reply)

        return result

    if args.prompt:
        try:
            execute_turn(args.prompt)
        except Exception as exc:
            print(f"Error: {exc}", file=sys.stderr)
            raise SystemExit(1) from exc
        return

    print("xxxclaw interactive mode. Type `exit` to quit.")
    while True:
        try:
            user_input = input("> ").strip()
        except EOFError:
            print()
            return

        if not user_input:
            continue
        if user_input.lower() in {"exit", "quit"}:
            return

        try:
            execute_turn(user_input)
        except Exception as exc:
            print(f"Error: {exc}")
            continue


if __name__ == "__main__":
    main()
