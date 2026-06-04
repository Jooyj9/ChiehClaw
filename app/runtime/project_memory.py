from __future__ import annotations

from pathlib import Path


def load_project_memory(path: Path | None, max_chars: int) -> str:
    if path is None or max_chars <= 0 or not path.exists() or not path.is_file():
        return ""

    content = path.read_text(encoding="utf-8-sig", errors="replace").strip()
    if len(content) <= max_chars:
        return content

    suffix = "\n...[project memory truncated]..."
    keep = max(0, max_chars - len(suffix))
    return content[:keep].rstrip() + suffix
