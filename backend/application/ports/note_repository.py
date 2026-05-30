from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Sequence

from backend.domain.note import Note


class NoteRepository(ABC):
    @abstractmethod
    async def save(self, note: Note) -> None: ...

    @abstractmethod
    async def get(self, note_id: str) -> Note | None: ...

    @abstractmethod
    async def list(
        self,
        *,
        type_filter: str | None = None,
        page: int = 1,
        limit: int = 50,
    ) -> tuple[Sequence[Note], int]: ...

    @abstractmethod
    async def delete(self, note_id: str) -> bool: ...
