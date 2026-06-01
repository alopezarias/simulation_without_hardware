from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.application.ports.audio_store import AudioStore
from backend.infrastructure.db.note_model import NoteRow


class AudioRetentionService:
    """Delete audio files for notes older than the configured retention window.

    Notes themselves are kept — only the audio blob is removed.  The
    audio_path column is cleared so the /notes/{id}/audio endpoint returns 404
    rather than a misleading "file not found" error.
    """

    async def sweep(
        self,
        db: AsyncSession,
        audio_store: AudioStore,
        *,
        retention_days: int,
    ) -> int:
        cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=retention_days)
        rows = (
            await db.execute(
                select(NoteRow)
                .where(NoteRow.created_at < cutoff)
                .where(NoteRow.audio_path != "")
            )
        ).scalars().all()

        for row in rows:
            await audio_store.delete(row.audio_path)
            row.audio_path = ""

        if rows:
            await db.commit()

        return len(rows)
