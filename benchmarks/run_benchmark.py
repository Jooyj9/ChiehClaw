from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import time
from collections import defaultdict
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.agent.session import Session
from app.ai.message import ChatMessage, ToolCall
from app.config import Settings
from app.runtime.context_manager import ContextManager
from app.runtime.event_log import EventLog
from app.runtime.runner import Runner
from app.runtime.workspace_context import use_workspace_dir
from app.storage.naming import conversation_storage_name


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run xxxclaw benchmark cases.")
    parser.add_argument("--cases", default="benchmarks/cases/core_cases.json")
    parser.add_argument("--mode", default="deterministic_runtime", choices=["deterministic_runtime", "llm_e2e"])
    parser.add_argument("--variant", default="all", choices=["all", "baseline", "current"])
    parser.add_argument("--report-dir", default="benchmarks/reports")
    parser.add_argument("--keep-workdirs", action="store_true")
    parser.add_argument("--case-id", help="Run only one benchmark case by id.")
    parser.add_argument("--max-cases", type=int, help="Run at most N cases per variant.")
    return parser.parse_args()


def make_settings(root: Path, variant_settings: dict[str, Any], use_real_model: bool = False) -> Settings:
    if use_real_model:
        settings = replace(
            Settings.from_env(ROOT),
            data_dir=root / "data",
            sessions_dir=root / "data" / "sessions",
            logs_dir=root / "data" / "logs",
            files_dir=root / "data" / "files",
            workspace_root_dir=root / "workspaces",
        )
    else:
        settings = Settings(
            model_provider="mock",
            model_name="mock",
            openai_base_url="",
            openai_api_key=None,
            data_dir=root / "data",
            sessions_dir=root / "data" / "sessions",
            logs_dir=root / "data" / "logs",
            files_dir=root / "data" / "files",
            workspace_root_dir=root / "workspaces",
        )
    for key, value in variant_settings.items():
        if hasattr(settings, key):
            setattr(settings, key, value)
    settings.ensure_directories()
    return settings


def write_fixture_files(workspace: Path, files: dict[str, str]) -> None:
    for rel_path, content in files.items():
        target = workspace / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def expand_messages(items: list[dict[str, Any]]) -> list[ChatMessage]:
    messages: list[ChatMessage] = []
    for item in items:
        repeat = int(item.get("repeat", 0) or 0)
        if repeat:
            for index in range(repeat):
                messages.append(
                    ChatMessage(
                        role=item["role"],
                        content=str(item.get("content_template", "")).format(i=index),
                    )
                )
            continue
        messages.append(ChatMessage(role=item["role"], content=item.get("content", "")))
    return messages


def execute_tool(runner: Runner, conversation_id: str, call: dict[str, Any]) -> dict[str, Any]:
    workspace = runner._workspace_for_conversation(conversation_id)
    with use_workspace_dir(workspace):
        return runner.agent_loop.tool_executor.execute(
            ToolCall(
                id=f"bench-{call['name']}",
                name=call["name"],
                arguments=dict(call.get("arguments") or {}),
            )
        )


def contains_value(payload: Any, expected: str) -> bool:
    return expected in json.dumps(payload, ensure_ascii=False)


def stringify(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str)


def event_types(events: list[dict[str, Any]]) -> list[str]:
    return [str(event.get("type") or "") for event in events]


def turn_ids(events: list[dict[str, Any]]) -> set[str]:
    return {str(event.get("turn_id") or "") for event in events if event.get("turn_id")}


def check_common_expected(
    *,
    expected: dict[str, Any],
    result: dict[str, Any] | None = None,
    workspace: Path | None = None,
) -> list[str]:
    failures: list[str] = []
    if result is not None and "tool_ok" in expected and result.get("ok") is not expected["tool_ok"]:
        failures.append(f"expected tool_ok={expected['tool_ok']}, got {result.get('ok')}")

    if result is not None and expected.get("error_contains"):
        if expected["error_contains"] not in str(result.get("error", "")):
            failures.append(f"error did not contain {expected['error_contains']!r}: {result.get('error')!r}")

    if result is not None and expected.get("error_contains_any"):
        error = str(result.get("error", ""))
        if not any(item in error for item in expected["error_contains_any"]):
            failures.append(f"error did not contain any of {expected['error_contains_any']!r}: {error!r}")

    if workspace is not None:
        for rel_path in expected.get("host_files_absent", []):
            target = (workspace / rel_path).resolve()
            if target.exists():
                failures.append(f"unexpected host file exists: {target}")

    return failures


def read_file_if_exists(workspace: Path, rel_path: str) -> str | None:
    target = workspace / rel_path
    if not target.exists() or not target.is_file():
        return None
    return target.read_text(encoding="utf-8", errors="replace")


def check_file_expectations(workspace: Path, expected: dict[str, Any]) -> list[str]:
    failures: list[str] = []

    for rel_path, text in expected.get("files_contain", {}).items():
        content = read_file_if_exists(workspace, rel_path)
        if content is None:
            failures.append(f"missing expected file {rel_path!r}")
        elif text not in content:
            failures.append(f"{rel_path!r} did not contain {text!r}")

    for rel_path, text in expected.get("files_not_contain", {}).items():
        content = read_file_if_exists(workspace, rel_path)
        if content is not None and text in content:
            failures.append(f"{rel_path!r} unexpectedly contained {text!r}")

    for rel_path, texts in expected.get("files_contain_all", {}).items():
        content = read_file_if_exists(workspace, rel_path)
        if content is None:
            failures.append(f"missing expected file {rel_path!r}")
            continue
        for text in texts:
            if text not in content:
                failures.append(f"{rel_path!r} did not contain {text!r}")

    for rel_path, texts in expected.get("files_contain_any", {}).items():
        content = read_file_if_exists(workspace, rel_path)
        if content is None:
            failures.append(f"missing expected file {rel_path!r}")
        elif not any(text in content for text in texts):
            failures.append(f"{rel_path!r} did not contain any of {texts!r}")

    for rel_path in expected.get("files_absent", []):
        if (workspace / rel_path).exists():
            failures.append(f"{rel_path!r} should be absent")

    return failures


def tool_event_payload(events: list[dict[str, Any]]) -> str:
    tool_events = [event for event in events if str(event.get("type") or "").startswith("tool_")]
    return stringify(tool_events)


def check_llm_expectations(
    *,
    workspace: Path,
    session: Session,
    events: list[dict[str, Any]],
    final_reply: str,
    expected: dict[str, Any],
) -> list[str]:
    failures = check_file_expectations(workspace, expected)
    planned_tools = [str(event.get("tool_name") or "") for event in events if event.get("type") == "tool_call_planned"]
    for tool_name in expected.get("required_tool_names", []):
        if tool_name not in planned_tools:
            failures.append(f"required tool {tool_name!r} was not planned; got {planned_tools!r}")

    payload = tool_event_payload(events)
    if expected.get("tool_result_regex") and not re.search(expected["tool_result_regex"], payload):
        failures.append(f"tool result did not match regex {expected['tool_result_regex']!r}")

    for key, value in expected.get("tool_result_contains", {}).items():
        if str(key) not in payload or str(value) not in payload:
            failures.append(f"tool result did not contain {key!r}: {value!r}")

    if expected.get("assistant_reply_contains_any"):
        if not any(text in final_reply for text in expected["assistant_reply_contains_any"]):
            failures.append(f"assistant reply did not contain any of {expected['assistant_reply_contains_any']!r}")

    metadata_text = stringify(session.metadata)
    for text in expected.get("context_metadata_contains", []):
        if text not in metadata_text:
            failures.append(f"session metadata did not contain {text!r}")

    return failures


def scoring_expected(case: dict[str, Any]) -> dict[str, Any]:
    expected = dict(case.get("expected") or {})
    current_expected = expected.get("current")
    if isinstance(current_expected, dict):
        expected.update(current_expected)
    return expected


def run_tool_call_case(case: dict[str, Any], runner: Runner, variant: str) -> tuple[bool, dict[str, Any]]:
    conversation_id = case["id"]
    workspace = runner._workspace_for_conversation(conversation_id)
    write_fixture_files(workspace, (case.get("fixture") or {}).get("files") or {})
    results = [execute_tool(runner, conversation_id, call) for call in case.get("tool_calls", [])]
    result = results[-1] if results else None
    expected = scoring_expected(case)
    failures = check_common_expected(expected=expected, result=result, workspace=workspace)
    return not failures, {"failures": failures, "tool_results": results, "workspace": str(workspace)}


def run_steps_case(case: dict[str, Any], runner: Runner, variant: str) -> tuple[bool, dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for step in case.get("steps", []):
        results.append(execute_tool(runner, step["conversation_id"], step["tool_call"]))

    expected = scoring_expected(case)
    failures: list[str] = []
    expected_steps = expected.get("step_results", [])
    for index, step_expected in enumerate(expected_steps):
        if index >= len(results):
            failures.append(f"missing step result {index}")
            continue
        if "ok" in step_expected and results[index].get("ok") is not step_expected["ok"]:
            failures.append(f"step {index} expected ok={step_expected['ok']}, got {results[index].get('ok')}")
    return not failures, {"failures": failures, "tool_results": results}


def run_context_case(
    case: dict[str, Any],
    settings: Settings,
    runner: Runner,
    variant: str,
) -> tuple[bool, dict[str, Any]]:
    session = Session(conversation_id=case["id"], system_prompt="")
    session.metadata["workspace_dir"] = str(runner._workspace_for_conversation(case["id"]))
    session.metadata["shell_execution_mode"] = settings.shell_execution_mode
    session.messages = expand_messages((case.get("fixture") or {}).get("session_messages") or [])
    context_settings = case.get("context_settings") or {}
    manager = ContextManager(
        max_tokens=context_settings.get("max_tokens", settings.context_max_tokens),
        recent_tokens=context_settings.get("recent_tokens", settings.context_recent_tokens),
        older_memory_tokens=context_settings.get("older_memory_tokens", settings.context_older_memory_tokens),
        tool_result_max_chars=settings.tool_result_max_chars,
        assistant_reasoning_max_chars=settings.assistant_reasoning_max_chars,
        enable_runtime_context=settings.feature_runtime_context,
        enable_older_memory=settings.feature_older_memory,
    )
    messages = manager.build_messages(session)
    rendered = "\n".join(f"{message.role}:{message.name or ''}:{message.content}" for message in messages)
    expected = scoring_expected(case)
    failures: list[str] = []
    for text in expected.get("context_messages_contain", []):
        if text not in rendered:
            failures.append(f"context did not contain {text!r}")
    for text in expected.get("context_messages_absent", []):
        if text in rendered:
            failures.append(f"context unexpectedly contained {text!r}")
    return not failures, {
        "failures": failures,
        "context_message_count": len(messages),
        "context_stats": session.metadata.get("context_stats", {}),
        "context_preview": rendered[:1000],
    }


def append_fixture_events(runner: Runner, conversation_id: str, events: list[dict[str, Any]]) -> None:
    for event in events:
        runner.event_log.append(conversation_id, event)


def run_recovery_scan_case(case: dict[str, Any], runner: Runner, variant: str) -> tuple[bool, dict[str, Any]]:
    conversation_id = case["id"]
    workspace = runner._workspace_for_conversation(conversation_id)
    fixture = case.get("fixture") or {}
    write_fixture_files(workspace, fixture.get("files") or {})
    append_fixture_events(runner, conversation_id, fixture.get("events") or [])
    unfinished = runner.scan_unfinished_tasks()
    expected = scoring_expected(case)
    failures: list[str] = []
    if len(unfinished) != expected.get("unfinished_count"):
        failures.append(f"expected unfinished_count={expected.get('unfinished_count')}, got {len(unfinished)}")
    interrupted_names = [tool.tool_name for turn in unfinished for tool in turn.interrupted_tools]
    for name in expected.get("interrupted_tools", []):
        if name not in interrupted_names:
            failures.append(f"missing interrupted tool {name!r}")
    if "recoverable" in expected and interrupted_names:
        tool = runner._tools_by_name.get(interrupted_names[0])
        if bool(tool and tool.recoverable) is not expected["recoverable"]:
            failures.append(f"expected recoverable={expected['recoverable']} for {interrupted_names[0]}")
    return not failures, {"failures": failures, "unfinished_count": len(unfinished), "interrupted_tools": interrupted_names}


def run_recovery_skip_case(case: dict[str, Any], runner: Runner, variant: str) -> tuple[bool, dict[str, Any]]:
    conversation_id = case["id"]
    append_fixture_events(runner, conversation_id, (case.get("fixture") or {}).get("events") or [])
    runner.recover_unfinished_tasks()
    events = runner.event_log.read(conversation_id)
    types = event_types(events)
    expected = scoring_expected(case)
    failures: list[str] = []
    for item in expected.get("events_contain", []):
        if item not in types:
            failures.append(f"missing event type {item!r}")
    for item in expected.get("events_absent", []):
        if item in types:
            failures.append(f"unexpected event type {item!r}")
    if expected.get("assistant_notice_contains"):
        session = runner.session_store.load(conversation_id, "")
        content = "\n".join(message.content for message in session.messages)
        if expected["assistant_notice_contains"] not in content:
            failures.append("missing recovery assistant notice")
    return not failures, {"failures": failures, "event_types": types}


def run_recovery_compact_case(case: dict[str, Any], runner: Runner) -> tuple[bool, dict[str, Any]]:
    conversation_id = case["id"]
    fixture = case.get("fixture") or {}
    for index in range(int(fixture.get("completed_turn_count", 0))):
        turn_id = f"done-{index}"
        runner.event_log.append(conversation_id, {"turn_id": turn_id, "type": "turn_started"})
        runner.event_log.append(conversation_id, {"turn_id": turn_id, "type": "turn_succeeded"})
    runner.event_log.append(conversation_id, {"turn_id": "unfinished", "type": "turn_started"})
    runner.event_log.append(conversation_id, fixture["unfinished_event"])
    expected = case.get("expected") or {}
    removed = runner.event_log.compact_completed_turns(
        conversation_id,
        keep_completed_turns=int(expected.get("compact_keep_completed_turns", 2)),
    )
    retained_turns = turn_ids(runner.event_log.read(conversation_id))
    failures: list[str] = []
    for item in expected.get("events_absent", []):
        if item in retained_turns:
            failures.append(f"unexpected retained turn {item!r}")
    for item in expected.get("events_contain", []):
        if item not in retained_turns:
            failures.append(f"missing retained turn {item!r}")
    return not failures, {"failures": failures, "removed_count": removed, "retained_turns": sorted(retained_turns)}


def run_case(case: dict[str, Any], settings: Settings, runner: Runner, variant: str) -> tuple[bool, dict[str, Any]]:
    if case.get("steps"):
        return run_steps_case(case, runner, variant)
    if case.get("tool_calls"):
        return run_tool_call_case(case, runner, variant)
    if case["category"] == "context_retention":
        return run_context_case(case, settings, runner, variant)
    if case["id"] == "recovery_scan_unfinished_read":
        return run_recovery_scan_case(case, runner, variant)
    if case["id"] == "recovery_skip_non_recoverable_shell":
        return run_recovery_skip_case(case, runner, variant)
    if case["id"] == "recovery_compact_keeps_unfinished":
        return run_recovery_compact_case(case, runner)
    return False, {"failures": [f"unsupported deterministic case shape: {case['id']}"]}


def run_llm_case(case: dict[str, Any], settings: Settings, runner: Runner, variant: str) -> tuple[bool, dict[str, Any]]:
    conversation_id = case["id"]
    workspace = runner._workspace_for_conversation(conversation_id)
    write_fixture_files(workspace, (case.get("fixture") or {}).get("files") or {})

    final_reply = ""
    for turn in case.get("turns", []):
        result = runner.run_turn(conversation_id=conversation_id, user_input=turn["user"])
        final_reply = result.reply

    session = runner.session_store.load(conversation_id, "")
    events = runner.event_log.read(conversation_id)
    expected = scoring_expected(case)
    failures = check_llm_expectations(
        workspace=workspace,
        session=session,
        events=events,
        final_reply=final_reply,
        expected=expected,
    )
    return not failures, {
        "failures": failures,
        "workspace": str(workspace),
        "final_reply": final_reply[:1000],
        "event_types": event_types(events),
        "tool_names": [event.get("tool_name") for event in events if event.get("type") == "tool_call_planned"],
        "context_stats": session.metadata.get("context_stats", {}),
    }


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, dict[str, dict[str, int]]] = defaultdict(lambda: defaultdict(lambda: {"passed": 0, "total": 0}))
    for record in records:
        bucket = grouped[record["variant"]][record["metric"]]
        bucket["total"] += 1
        bucket["passed"] += int(record["passed"])
    return grouped


def make_report_paths(report_dir: Path, suite_name: str, mode: str) -> tuple[Path, Path]:
    report_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    report_mode = mode.replace("_runtime", "")
    jsonl_path = report_dir / f"{suite_name}-{report_mode}-{timestamp}.jsonl"
    md_path = report_dir / f"{suite_name}-{report_mode}-{timestamp}.md"
    return jsonl_path, md_path


def append_jsonl_record(jsonl_path: Path, record: dict[str, Any]) -> None:
    with jsonl_path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(record, ensure_ascii=False) + "\n")


def write_reports(
    records: list[dict[str, Any]],
    report_dir: Path,
    suite_name: str,
    mode: str,
    jsonl_path: Path | None = None,
    md_path: Path | None = None,
) -> tuple[Path, Path]:
    if jsonl_path is None or md_path is None:
        jsonl_path, md_path = make_report_paths(report_dir, suite_name, mode)
    else:
        report_dir.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now().strftime("%Y%m%d-%H%M%S")

    with jsonl_path.open("w", encoding="utf-8") as file:
        for record in records:
            file.write(json.dumps(record, ensure_ascii=False) + "\n")

    summary = summarize(records)
    lines = [
        f"# {suite_name} {mode} benchmark",
        "",
        f"Generated at: {generated_at}",
        "",
        "Scoring note: all variants are scored against the current target capability. Baseline failures show where disabled features lose platform capability.",
        "",
        "## Metrics",
        "",
        "| Variant | Metric | Passed | Total | Rate |",
        "|---|---:|---:|---:|---:|",
    ]
    for variant in sorted(summary):
        for metric in sorted(summary[variant]):
            counts = summary[variant][metric]
            rate = counts["passed"] / counts["total"] if counts["total"] else 0
            lines.append(f"| {variant} | {metric} | {counts['passed']} | {counts['total']} | {rate:.2%} |")

    failed = [record for record in records if not record["passed"]]
    lines.extend(["", "## Failed Cases", ""])
    if not failed:
        lines.append("No failed deterministic cases.")
    else:
        for record in failed:
            lines.append(f"- `{record['variant']}` / `{record['case_id']}`: {record['details'].get('failures')}")

    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return jsonl_path, md_path


def main() -> None:
    args = parse_args()
    suite_path = (ROOT / args.cases).resolve()
    suite = json.loads(suite_path.read_text(encoding="utf-8"))
    variants = list(suite["variants"].keys()) if args.variant == "all" else [args.variant]
    cases = [case for case in suite["cases"] if case.get("mode") == args.mode]
    if args.case_id:
        cases = [case for case in cases if case["id"] == args.case_id]
    if args.max_cases is not None:
        cases = cases[: args.max_cases]
    use_real_model = args.mode == "llm_e2e"

    run_root = ROOT / "benchmarks" / "tmp" / datetime.now().strftime("%Y%m%d-%H%M%S")
    report_dir = (ROOT / args.report_dir).resolve()
    jsonl_path, md_path = make_report_paths(report_dir, suite["suite"], args.mode)
    print(f"jsonl={jsonl_path}", flush=True)
    print(f"summary={md_path}", flush=True)
    records: list[dict[str, Any]] = []
    try:
        for variant in variants:
            variant_settings = dict(suite["variants"][variant].get("settings") or {})
            for case in cases:
                case_root = run_root / variant / case["id"]
                settings = make_settings(case_root, variant_settings, use_real_model=use_real_model)
                runner = Runner(settings)
                started = time.perf_counter()
                print(f"running {variant}/{case['id']}...", flush=True)
                try:
                    if args.mode == "llm_e2e":
                        passed, details = run_llm_case(case, settings, runner, variant)
                    else:
                        passed, details = run_case(case, settings, runner, variant)
                except Exception as exc:
                    passed = False
                    details = {"failures": [f"{type(exc).__name__}: {exc}"]}
                elapsed_ms = int((time.perf_counter() - started) * 1000)
                record = {
                    "suite": suite["suite"],
                    "variant": variant,
                    "case_id": case["id"],
                    "category": case["category"],
                    "metric": case["metric"],
                    "mode": case["mode"],
                    "passed": passed,
                    "elapsed_ms": elapsed_ms,
                    "details": details,
                }
                records.append(record)
                append_jsonl_record(jsonl_path, record)
                print(
                    f"finished {variant}/{case['id']}: {'PASS' if passed else 'FAIL'} "
                    f"({elapsed_ms} ms)",
                    flush=True,
                )
    finally:
        if not args.keep_workdirs:
            shutil.rmtree(run_root, ignore_errors=True)

    jsonl_path, md_path = write_reports(records, report_dir, suite["suite"], args.mode, jsonl_path, md_path)
    print(f"records={len(records)}")
    print(f"jsonl={jsonl_path}")
    print(f"summary={md_path}")
    for variant, metrics in summarize(records).items():
        print(f"[{variant}]")
        for metric, counts in metrics.items():
            rate = counts["passed"] / counts["total"] if counts["total"] else 0
            print(f"  {metric}: {counts['passed']}/{counts['total']} ({rate:.2%})")


if __name__ == "__main__":
    main()
