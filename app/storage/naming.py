from __future__ import annotations

import base64
import re


SAFE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")
WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


def conversation_storage_name(conversation_id: str) -> str:
    if _is_safe_storage_name(conversation_id):
        return conversation_id

    encoded = base64.urlsafe_b64encode(conversation_id.encode("utf-8")).decode("ascii").rstrip("=")
    return f"conv_{encoded or 'empty'}"


def _is_safe_storage_name(name: str) -> bool:
    if not name or not SAFE_NAME_PATTERN.fullmatch(name):
        return False

    stem = name.rstrip(". ").upper()
    return stem not in WINDOWS_RESERVED_NAMES
