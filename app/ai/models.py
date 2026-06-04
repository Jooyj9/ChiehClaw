from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class ModelConfig:
    provider: str
    model: str
    temperature: float = 0.0

