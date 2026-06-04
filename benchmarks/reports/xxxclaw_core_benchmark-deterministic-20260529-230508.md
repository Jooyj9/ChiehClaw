# xxxclaw_core_benchmark deterministic benchmark

Generated at: 20260529-230508

## Metrics

| Variant | Metric | Passed | Total | Rate |
|---|---:|---:|---:|---:|
| baseline | context_retention_accuracy | 2 | 2 | 100.00% |
| baseline | isolation_safety_pass_rate | 4 | 4 | 100.00% |
| baseline | recovery_detection_rate | 3 | 3 | 100.00% |
| baseline | tool_call_success_rate | 2 | 2 | 100.00% |
| current | context_retention_accuracy | 1 | 2 | 50.00% |
| current | isolation_safety_pass_rate | 4 | 4 | 100.00% |
| current | recovery_detection_rate | 3 | 3 | 100.00% |
| current | tool_call_success_rate | 2 | 2 | 100.00% |

## Failed Cases

- `current` / `context_early_file_decision`: ["context did not contain 'older_memory'"]
