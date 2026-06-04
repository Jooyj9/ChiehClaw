# xxxclaw_core_benchmark llm_e2e benchmark

Generated at: 20260529-232457

Scoring note: all variants are scored against the current target capability. Baseline failures show where disabled features lose platform capability.

## Metrics

| Variant | Metric | Passed | Total | Rate |
|---|---:|---:|---:|---:|
| baseline | context_retention_accuracy | 1 | 1 | 100.00% |
| baseline | task_success_rate | 3 | 3 | 100.00% |
| baseline | tool_call_success_rate | 2 | 3 | 66.67% |
| current | context_retention_accuracy | 1 | 1 | 100.00% |
| current | task_success_rate | 3 | 3 | 100.00% |
| current | tool_call_success_rate | 3 | 3 | 100.00% |

## Failed Cases

- `baseline` / `shell_python_version`: ["tool result did not contain 'execution_mode': 'docker'"]
