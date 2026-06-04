# xxxclaw_core_benchmark deterministic_runtime benchmark

Generated at: 20260530-233413

Scoring note: all variants are scored against the current target capability. Baseline failures show where disabled features lose platform capability.

## Metrics

| Variant | Metric | Passed | Total | Rate |
|---|---:|---:|---:|---:|
| baseline | context_retention_accuracy | 0 | 2 | 0.00% |
| baseline | isolation_safety_pass_rate | 3 | 4 | 75.00% |
| baseline | recovery_detection_rate | 1 | 3 | 33.33% |
| baseline | tool_call_success_rate | 2 | 2 | 100.00% |
| current | context_retention_accuracy | 2 | 2 | 100.00% |
| current | isolation_safety_pass_rate | 4 | 4 | 100.00% |
| current | recovery_detection_rate | 3 | 3 | 100.00% |
| current | tool_call_success_rate | 2 | 2 | 100.00% |

## Failed Cases

- `baseline` / `safety_conversation_workspace_isolation`: ['step 1 expected ok=False, got True']
- `baseline` / `context_runtime_workspace_over_stale_app`: ["context did not contain 'current conversation workspace'", "context did not contain 'Older /app paths'"]
- `baseline` / `context_early_file_decision`: ["context did not contain 'older_memory'"]
- `baseline` / `recovery_scan_unfinished_read`: ['expected unfinished_count=1, got 0', "missing interrupted tool 'read_file'"]
- `baseline` / `recovery_skip_non_recoverable_shell`: ["missing event type 'tool_call_skipped'", "missing event type 'turn_recovered'", 'missing recovery assistant notice']
