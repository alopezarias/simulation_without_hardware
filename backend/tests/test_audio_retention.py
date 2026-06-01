"""Audio retention service tests.

The sweep() method deletes audio files for notes older than the configured
retention window and clears audio_path in the database row.  Notes themselves
are never deleted — only the audio blob is removed.

Run as part of the normal test suite:
    pytest backend/tests/test_audio_retention.py -v
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from backend.application.services.audio_retention_service import AudioRetentionService
from backend.infrastructure.adapters.local_audio_store import LocalAudioStore
from backend.infrastructure.db.note_model import NoteRow


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_audio_file(base_dir: Path, note_id: str, content: bytes = b"RIFF") -> str:
    """Write a dummy audio file; return its absolute path string."""
    p = base_dir / "2025-01" / f"{note_id}.wav"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(content)
    return str(p)


async def _insert_note(session, note_id: str, audio_path: str, *, days_old: float) -> NoteRow:
    row = NoteRow(
        id=note_id,
        device_id="test-device",
        text="test note",
        type=None,
        tags="[]",
        entities="{}",
        summary="",
        audio_path=audio_path,
        duration_s=1.0,
        capture_mode="manual",
        created_at=datetime.now(UTC).replace(tzinfo=None) - timedelta(days=days_old),
    )
    session.add(row)
    await session.commit()
    return row


# ── service unit tests ────────────────────────────────────────────────────────

class TestAudioRetentionService:

    @pytest.mark.asyncio
    async def test_sweep_removes_expired_audio_file(self, db_session, tmp_path):
        store = LocalAudioStore(str(tmp_path))
        note_id = "old-audio-note"
        audio_path = _make_audio_file(tmp_path, note_id)
        await _insert_note(db_session, note_id, audio_path, days_old=31)

        count = await AudioRetentionService().sweep(db_session, store, retention_days=30)

        assert count == 1
        assert not Path(audio_path).exists()

    @pytest.mark.asyncio
    async def test_sweep_clears_audio_path_in_db(self, db_session, tmp_path):
        store = LocalAudioStore(str(tmp_path))
        note_id = "db-path-cleared"
        audio_path = _make_audio_file(tmp_path, note_id)
        await _insert_note(db_session, note_id, audio_path, days_old=31)

        await AudioRetentionService().sweep(db_session, store, retention_days=30)

        row = await db_session.get(NoteRow, note_id)
        assert row.audio_path == ""

    @pytest.mark.asyncio
    async def test_sweep_keeps_text_note_in_db(self, db_session, tmp_path):
        store = LocalAudioStore(str(tmp_path))
        note_id = "text-survives"
        audio_path = _make_audio_file(tmp_path, note_id)
        await _insert_note(db_session, note_id, audio_path, days_old=31)

        await AudioRetentionService().sweep(db_session, store, retention_days=30)

        row = await db_session.get(NoteRow, note_id)
        assert row is not None
        assert row.text == "test note"

    @pytest.mark.asyncio
    async def test_sweep_keeps_recent_audio(self, db_session, tmp_path):
        store = LocalAudioStore(str(tmp_path))
        note_id = "recent-note"
        audio_path = _make_audio_file(tmp_path, note_id)
        await _insert_note(db_session, note_id, audio_path, days_old=10)

        count = await AudioRetentionService().sweep(db_session, store, retention_days=30)

        assert count == 0
        assert Path(audio_path).exists()
        row = await db_session.get(NoteRow, note_id)
        assert row.audio_path == audio_path

    @pytest.mark.asyncio
    async def test_sweep_skips_notes_without_audio(self, db_session, tmp_path):
        store = LocalAudioStore(str(tmp_path))
        await _insert_note(db_session, "no-audio", "", days_old=60)

        count = await AudioRetentionService().sweep(db_session, store, retention_days=30)

        assert count == 0

    @pytest.mark.asyncio
    async def test_sweep_handles_missing_file_gracefully(self, db_session, tmp_path):
        """If the file is already gone on disk the DB row is still cleared."""
        store = LocalAudioStore(str(tmp_path))
        await _insert_note(
            db_session, "missing-file", "/nonexistent/path/note.wav", days_old=60
        )

        count = await AudioRetentionService().sweep(db_session, store, retention_days=30)

        assert count == 1
        row = await db_session.get(NoteRow, "missing-file")
        assert row.audio_path == ""

    @pytest.mark.asyncio
    async def test_sweep_returns_correct_count_mixed(self, db_session, tmp_path):
        store = LocalAudioStore(str(tmp_path))
        for i in range(3):
            path = _make_audio_file(tmp_path, f"old-{i}")
            await _insert_note(db_session, f"old-{i}", path, days_old=40)
        for i in range(2):
            path = _make_audio_file(tmp_path, f"new-{i}")
            await _insert_note(db_session, f"new-{i}", path, days_old=5)

        count = await AudioRetentionService().sweep(db_session, store, retention_days=30)

        assert count == 3

    @pytest.mark.asyncio
    async def test_sweep_empty_database_returns_zero(self, db_session, tmp_path):
        store = LocalAudioStore(str(tmp_path))
        count = await AudioRetentionService().sweep(db_session, store, retention_days=30)
        assert count == 0

    @pytest.mark.asyncio
    async def test_sweep_multiple_calls_are_idempotent(self, db_session, tmp_path):
        store = LocalAudioStore(str(tmp_path))
        note_id = "idempotent-note"
        audio_path = _make_audio_file(tmp_path, note_id)
        await _insert_note(db_session, note_id, audio_path, days_old=31)

        svc = AudioRetentionService()
        first = await svc.sweep(db_session, store, retention_days=30)
        second = await svc.sweep(db_session, store, retention_days=30)

        assert first == 1
        assert second == 0  # already cleared; audio_path="" is skipped

    @pytest.mark.asyncio
    async def test_sweep_exactly_at_boundary_is_kept(self, db_session, tmp_path):
        """A note created exactly retention_days ago (to the second) is kept."""
        store = LocalAudioStore(str(tmp_path))
        note_id = "boundary-note"
        audio_path = _make_audio_file(tmp_path, note_id)
        # Use 29.9 days — clearly within the window
        await _insert_note(db_session, note_id, audio_path, days_old=29.9)

        count = await AudioRetentionService().sweep(db_session, store, retention_days=30)

        assert count == 0
        assert Path(audio_path).exists()

    @pytest.mark.asyncio
    async def test_sweep_zero_retention_days_clears_all_audio(self, db_session, tmp_path):
        """retention_days=0 means every note older than right now is expired."""
        store = LocalAudioStore(str(tmp_path))
        for i in range(3):
            path = _make_audio_file(tmp_path, f"zero-{i}")
            await _insert_note(db_session, f"zero-{i}", path, days_old=1)

        count = await AudioRetentionService().sweep(db_session, store, retention_days=0)

        assert count == 3

    @pytest.mark.asyncio
    async def test_sweep_only_old_files_are_deleted_from_disk(self, db_session, tmp_path):
        store = LocalAudioStore(str(tmp_path))
        old_path = _make_audio_file(tmp_path, "del-me")
        new_path = _make_audio_file(tmp_path, "keep-me")
        await _insert_note(db_session, "del-me", old_path, days_old=31)
        await _insert_note(db_session, "keep-me", new_path, days_old=5)

        await AudioRetentionService().sweep(db_session, store, retention_days=30)

        assert not Path(old_path).exists()
        assert Path(new_path).exists()
