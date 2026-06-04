# xxxclaw_core_benchmark llm_e2e benchmark

Generated at: 20260529-231836

Scoring note: all variants are scored against the current target capability. Baseline failures show where disabled features lose platform capability.

## Metrics

| Variant | Metric | Passed | Total | Rate |
|---|---:|---:|---:|---:|
| current | context_retention_accuracy | 1 | 1 | 100.00% |
| current | task_success_rate | 2 | 3 | 66.67% |
| current | tool_call_success_rate | 3 | 3 | 100.00% |

## Failed Cases

- `current` / `basic_multi_file_project`: ["'mini_calc/main.py' did not contain any of ['print(add(2, 3))', 'print(add(2,3))']"]
