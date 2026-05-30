from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class NoteType(str, Enum):
    TASK = "task"
    IDEA = "idea"
    REMINDER = "reminder"
    NOTE = "note"
    DICTATION = "dictation"


class CaptureMode(str, Enum):
    WAKE_WORD = "wake_word"
    MANUAL = "manual"


@dataclass
class Note:
    text: str
    device_id: str
    capture_mode: CaptureMode
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    type: NoteType | None = None
    tags: list[str] = field(default_factory=list)
    entities: dict[str, Any] = field(default_factory=dict)
    summary: str = ""
    audio_path: str = ""
    duration_s: float = 0.0
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def is_dictation(self) -> bool:
        return self.type == NoteType.DICTATION

    def is_classified(self) -> bool:
        return self.type is not None
