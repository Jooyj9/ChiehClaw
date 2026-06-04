from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def read_json(path: Path) -> Any:
    # utf-8-sig keeps old PowerShell-written JSON files with BOM readable.
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []

    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if line.strip():
            records.append(json.loads(line))
    return records
