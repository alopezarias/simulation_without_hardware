from __future__ import annotations

import json
from datetime import timezone
from typing import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.application.ports.note_repository import NoteRepository
from backend.domain.note import CaptureMode, Note, NoteType
from backend.infrastructure.db.note_model import NoteRow


def _to_domain(row: NoteRow) -> Note:
    return Note(
        id=row.id,
        device_id=row.device_id,
        text=row.text,
        type=NoteType(row.type) if row.type else None,
        tags=json.loads(row.tags or "[]"),
        entities=json.loads(row.entities or "{}"),
        summary=row.summary or "",
        audio_path=row.audio_path or "",
        duration_s=row.duration_s or 0.0,
        capture_mode=CaptureMode(row.capture_mode or "wake_word"),
        created_at=row.created_at.replace(tzinfo=timezone.utc),
    )


class SqliteNoteRepository(NoteRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save(self, note: Note) -> None:
        row = NoteRow(
            id=note.id,
            device_id=note.device_id,
            text=note.text,
            type=note.type.value if note.type else None,
            tags=json.dumps(note.tags),
            entities=json.dumps(note.entities),
            summary=note.summary,
            audio_path=note.audio_path,
            duration_s=note.duration_s,
            capture_mode=note.capture_mode.value,
            created_at=note.created_at.replace(tzinfo=None),  # SQLite stores naive UTC
        )
        self._session.add(row)
        await self._session.commit()

    async def get(self, note_id: str) -> Note | None:
        row = await self._session.get(NoteRow, note_id)
        return _to_domain(row) if row else None

    async def list(
        self,
        *,
        type_filter: str | None = None,
        page: int = 1,
        limit: int = 50,
    ) -> tuple[Sequence[Note], int]:
        base_q = select(NoteRow)
        if type_filter:
            base_q = base_q.where(NoteRow.type == type_filter)

        total: int = (
            await self._session.scalar(
                select(func.count()).select_from(base_q.subquery())
            )
        ) or 0

        rows = (
            await self._session.execute(
                base_q.order_by(NoteRow.created_at.desc())
                .offset((page - 1) * limit)
                .limit(limit)
            )
        ).scalars().all()

        return [_to_domain(r) for r in rows], total

    async def delete(self, note_id: str) -> bool:
        row = await self._session.get(NoteRow, note_id)
        if row is None:
            return False
        await self._session.delete(row)
        await self._session.commit()
        return True
