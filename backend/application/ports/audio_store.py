from __future__ import annotations

from abc import ABC, abstractmethod


class AudioStore(ABC):
    @abstractmethod
    async def save(self, note_id: str, audio_bytes: bytes) -> str:
        """Persist audio and return its relative path."""
        ...

    @abstractmethod
    async def delete(self, path: str) -> None: ...
