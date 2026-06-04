# xxxclaw_core_benchmark llm_e2e benchmark

Generated at: 20260529-231033

Scoring note: all variants are scored against the current target capability. Baseline failures show where disabled features lose platform capability.

## Metrics

| Variant | Metric | Passed | Total | Rate |
|---|---:|---:|---:|---:|
| baseline | context_retention_accuracy | 0 | 1 | 0.00% |
| baseline | task_success_rate | 0 | 3 | 0.00% |
| baseline | tool_call_success_rate | 0 | 3 | 0.00% |
| current | context_retention_accuracy | 0 | 1 | 0.00% |
| current | task_success_rate | 0 | 3 | 0.00% |
| current | tool_call_success_rate | 0 | 3 | 0.00% |

## Failed Cases

- `baseline` / `basic_create_python_file`: ['RuntimeError: OpenAI request failed: [WinError 10013] 以一种访问权限不允许的方式做了一个访问套接字的尝试。']
- `baseline` / `basic_edit_existing_file`: ['RuntimeError: OpenAI request failed: [WinError 10013] 以一种访问权限不允许的方式做了一个访问套接字的尝试。']
- `baseline` / `basic_multi_file_project`: ['RuntimeError: OpenAI request failed: [WinError 10013] 以一种访问权限不允许的方式做了一个访问套接字的尝试。']
- `baseline` / `shell_python_version`: ['RuntimeError: OpenAI request failed: [WinError 10013] 以一种访问权限不允许的方式做了一个访问套接字的尝试。']
- `baseline` / `shell_run_created_script`: ['RuntimeError: OpenAI request failed: [WinError 10013] 以一种访问权限不允许的方式做了一个访问套接字的尝试。']
- `baseline` / `shell_error_explanation`: ['RuntimeError: OpenAI request failed: [WinError 10013] 以一种访问权限不允许的方式做了一个访问套接字的尝试。']
- `baseline` / `context_user_constraint_preserved`: ['RuntimeError: OpenAI request failed: [WinError 10013] 以一种访问权限不允许的方式做了一个访问套接字的尝试。']
- `current` / `basic_create_python_file`: ['RuntimeError: OpenAI request failed: [WinError 10013] 以一种访问权限不允许的方式做了一个访问套接字的尝试。']
- `current` / `basic_edit_existing_file`: ['RuntimeError: OpenAI request failed: [WinError 10013] 以一种访问权限不允许的方式做了一个访问套接字的尝试。']
- `current` / `basic_multi_file_project`: ['RuntimeError: OpenAI request failed: [WinError 10013] 以一种访问权限不允许的方式做了一个访问套接字的尝试。']
- `current` / `shell_python_version`: ['RuntimeError: OpenAI request failed: [WinError 10013] 以一种访问权限不允许的方式做了一个访问套接字的尝试。']
- `current` / `shell_run_created_script`: ['RuntimeError: OpenAI request failed: [WinError 10013] 以一种访问权限不允许的方式做了一个访问套接字的尝试。']
- `current` / `shell_error_explanation`: ['RuntimeError: OpenAI request failed: [WinError 10013] 以一种访问权限不允许的方式做了一个访问套接字的尝试。']
- `current` / `context_user_constraint_preserved`: ['RuntimeError: OpenAI request failed: [WinError 10013] 以一种访问权限不允许的方式做了一个访问套接字的尝试。']
