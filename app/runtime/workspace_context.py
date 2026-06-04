from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Iterator


_current_workspace_dir: ContextVar[Path | None] = ContextVar("current_workspace_dir", default=None)


def get_workspace_dir(default: Path) -> Path:
    return (_current_workspace_dir.get() or default).resolve()


@contextmanager
def use_workspace_dir(workspace_dir: Path) -> Iterator[None]:
    token = _current_workspace_dir.set(workspace_dir.resolve())
    try:
        yield
    finally:
        _current_workspace_dir.reset(token)
