# XAGENTS.md

This file is the project-level long-term memory for xxxclaw.

Edit this file to provide stable instructions that should apply across conversations.
Keep it short and project-specific. Do not put secrets here.

## Project Rules

- Prefer clear, readable Python code.
- Keep functions focused and avoid over-engineering.
- Tools are executed by the runtime; do not assume tool results before they are returned.

## Common Commands

- Run tests: `uv run python -m unittest`
- Run deterministic benchmark: `python benchmarks\run_benchmark.py --mode deterministic_runtime --variant all`

## Notes

- Shell commands should operate inside the current conversation workspace.
- Long-running or side-effecting operations should be treated carefully.
