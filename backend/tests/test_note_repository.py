"""Tests for SqliteNoteRepository — direct adapter tests without HTTP."""

from __future__ import annotations

import pytest

from backend.domain.note import CaptureMode, Note, NoteType
from backend.infrastructure.adapters.sqlite_note_repository import SqliteNoteRepository


def _note(**kwargs) -> Note:
    defaults = dict(text="buy milk", device_id="raspi-1", capture_mode=CaptureMode.WAKE_WORD)
    defaults.update(kwargs)
    return Note(**defaults)


# ── Save + get ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_save_and_get_roundtrip(db_session):
    repo = SqliteNoteRepository(db_session)
    note = _note(text="remind me to call Alice")
    await repo.save(note)
    retrieved = await repo.get(note.id)
    assert retrieved is not None
    assert retrieved.id == note.id
    assert retrieved.text == "remind me to call Alice"
    assert retrieved.device_id == "raspi-1"


@pytest.mark.asyncio
async def test_save_preserves_type(db_session):
    repo = SqliteNoteRepository(db_session)
    note = _note(type=NoteType.TASK, tags=["urgent"], summary="call Alice")
    await repo.save(note)
    retrieved = await repo.get(note.id)
    assert retrieved.type == NoteType.TASK
    assert retrieved.tags == ["urgent"]
    assert retrieved.summary == "call Alice"


@pytest.mark.asyncio
async def test_save_preserves_entities(db_session):
    repo = SqliteNoteRepository(db_session)
    note = _note(entities={"person": "Alice", "date": "Friday"})
    await repo.save(note)
    retrieved = await repo.get(note.id)
    assert retrieved.entities == {"person": "Alice", "date": "Friday"}


@pytest.mark.asyncio
async def test_save_preserves_capture_mode(db_session):
    repo = SqliteNoteRepository(db_session)
    note = _note(capture_mode=CaptureMode.MANUAL)
    await repo.save(note)
    retrieved = await repo.get(note.id)
    assert retrieved.capture_mode == CaptureMode.MANUAL


@pytest.mark.asyncio
async def test_save_preserves_duration(db_session):
    repo = SqliteNoteRepository(db_session)
    note = _note(duration_s=4.2)
    await repo.save(note)
    retrieved = await repo.get(note.id)
    assert abs(retrieved.duration_s - 4.2) < 0.001


@pytest.mark.asyncio
async def test_get_nonexistent_returns_none(db_session):
    repo = SqliteNoteRepository(db_session)
    result = await repo.get("no-such-id")
    assert result is None


# ── Delete ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_delete_existing_returns_true(db_session):
    repo = SqliteNoteRepository(db_session)
    note = _note()
    await repo.save(note)
    result = await repo.delete(note.id)
    assert result is True


@pytest.mark.asyncio
async def test_delete_nonexistent_returns_false(db_session):
    repo = SqliteNoteRepository(db_session)
    result = await repo.delete("ghost-id")
    assert result is False


@pytest.mark.asyncio
async def test_delete_removes_from_db(db_session):
    repo = SqliteNoteRepository(db_session)
    note = _note()
    await repo.save(note)
    await repo.delete(note.id)
    assert await repo.get(note.id) is None


# ── List ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_empty(db_session):
    repo = SqliteNoteRepository(db_session)
    notes, total = await repo.list()
    assert notes == []
    assert total == 0


@pytest.mark.asyncio
async def test_list_returns_all(db_session):
    repo = SqliteNoteRepository(db_session)
    for i in range(3):
        await repo.save(_note(text=f"note {i}"))
    notes, total = await repo.list()
    assert total == 3
    assert len(notes) == 3


@pytest.mark.asyncio
async def test_list_ordered_newest_first(db_session):
    import asyncio
    repo = SqliteNoteRepository(db_session)
    note_a = _note(text="first")
    await repo.save(note_a)
    await asyncio.sleep(0.01)
    note_b = _note(text="second")
    await repo.save(note_b)
    notes, _ = await repo.list()
    assert notes[0].id == note_b.id


@pytest.mark.asyncio
async def test_list_filter_by_type(db_session):
    repo = SqliteNoteRepository(db_session)
    await repo.save(_note(type=NoteType.TASK))
    await repo.save(_note(type=NoteType.IDEA))
    await repo.save(_note(type=NoteType.TASK))
    notes, total = await repo.list(type_filter="task")
    assert total == 2
    assert all(n.type == NoteType.TASK for n in notes)


@pytest.mark.asyncio
async def test_list_pagination(db_session):
    repo = SqliteNoteRepository(db_session)
    for i in range(7):
        await repo.save(_note(text=f"note {i}"))
    page1, total = await repo.list(page=1, limit=3)
    page2, _ = await repo.list(page=2, limit=3)
    page3, _ = await repo.list(page=3, limit=3)
    assert total == 7
    assert len(page1) == 3
    assert len(page2) == 3
    assert len(page3) == 1
    all_ids = {n.id for n in page1} | {n.id for n in page2} | {n.id for n in page3}
    assert len(all_ids) == 7


@pytest.mark.asyncio
async def test_list_filter_no_match(db_session):
    repo = SqliteNoteRepository(db_session)
    await repo.save(_note(type=NoteType.NOTE))
    notes, total = await repo.list(type_filter="task")
    assert total == 0
    assert notes == []
