from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from backend.domain.note import NoteType


@dataclass
class ClassificationResult:
    type: NoteType
    tags: list[str] = field(default_factory=list)
    entities: dict[str, Any] = field(default_factory=dict)
    summary: str = ""


class ClassifierPort(ABC):
    @abstractmethod
    async def classify(self, text: str) -> ClassificationResult: ...
