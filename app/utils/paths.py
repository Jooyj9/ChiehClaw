from __future__ import annotations

from pathlib import Path


def resolve_workspace_path(workspace_dir: Path, raw_path: str) -> Path:
    workspace_dir = workspace_dir.resolve()
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = workspace_dir / candidate
    candidate = candidate.resolve()

    try:
        candidate.relative_to(workspace_dir)
    except ValueError as exc:
        raise ValueError(f"path escapes workspace: {raw_path}") from exc
    return candidate

