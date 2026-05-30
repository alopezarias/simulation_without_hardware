from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class TranscriptionResult:
    text: str
    language: str
    duration_s: float


class SttPort(ABC):
    @abstractmethod
    async def transcribe(self, audio_bytes: bytes) -> TranscriptionResult: ...
