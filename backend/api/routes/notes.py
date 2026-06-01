from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel

from backend.api.dependencies import get_audio_store, get_note_repo, require_auth
from backend.application.ports.audio_store import AudioStore
from backend.application.ports.note_repository import NoteRepository
from backend.domain.note import Note, NoteType

router = APIRouter(prefix="/notes", tags=["notes"])


class NoteUpdateBody(BaseModel):
    text: str | None = None
    annotation: str | None = None


def _serialize(note: Note) -> dict:
    return {
        "id": note.id,
        "device_id": note.device_id,
        "text": note.text,
        "type": note.type.value if note.type else None,
        "tags": note.tags,
        "entities": note.entities,
        "summary": note.summary,
        "audio_path": note.audio_path,
        "duration_s": note.duration_s,
        "annotation": note.annotation,
        "capture_mode": note.capture_mode.value,
        "created_at": note.created_at.isoformat(),
    }


@router.get("")
async def list_notes(
    type: str | None = Query(None, description="Filter by note type"),
    q: str | None = Query(None, description="Full-text search across note text and summary"),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=100),
    repo: NoteRepository = Depends(get_note_repo),
    _auth=Depends(require_auth),
) -> dict:
    if type is not None and type not in NoteType._value2member_map_:
        raise HTTPException(
            status_code=422,
            detail=f"type must be one of {list(NoteType._value2member_map_)}",
        )
    notes, total = await repo.list(type_filter=type, q=q or None, page=page, limit=limit)
    pages = max(1, -(-total // limit))  # ceiling division
    return {
        "items": [_serialize(n) for n in notes],
        "total": total,
        "page": page,
        "pages": pages,
    }


@router.get("/{note_id}")
async def get_note(
    note_id: str,
    repo: NoteRepository = Depends(get_note_repo),
    _auth=Depends(require_auth),
) -> dict:
    note = await repo.get(note_id)
    if note is None:
        raise HTTPException(status_code=404, detail="Note not found")
    return _serialize(note)


@router.put("/{note_id}")
async def update_note(
    note_id: str,
    body: NoteUpdateBody,
    repo: NoteRepository = Depends(get_note_repo),
    _auth=Depends(require_auth),
) -> dict:
    if body.text is None and body.annotation is None:
        raise HTTPException(status_code=422, detail="Provide at least one of: text, annotation")
    note = await repo.update(note_id, text=body.text, annotation=body.annotation)
    if note is None:
        raise HTTPException(status_code=404, detail="Note not found")
    return _serialize(note)


@router.delete("/{note_id}", status_code=204)
async def delete_note(
    note_id: str,
    repo: NoteRepository = Depends(get_note_repo),
    audio_store: AudioStore = Depends(get_audio_store),
    _auth=Depends(require_auth),
) -> None:
    note = await repo.get(note_id)
    if note is None:
        raise HTTPException(status_code=404, detail="Note not found")
    if note.audio_path:
        await audio_store.delete(note.audio_path)
    await repo.delete(note_id)


@router.get("/{note_id}/audio")
async def get_audio(
    note_id: str,
    repo: NoteRepository = Depends(get_note_repo),
    _auth=Depends(require_auth),
) -> FileResponse:
    note = await repo.get(note_id)
    if note is None or not note.audio_path:
        raise HTTPException(status_code=404, detail="Note not found")
    p = Path(note.audio_path)
    if not p.exists():
        raise HTTPException(status_code=404, detail="Audio file not found on disk")
    return FileResponse(str(p), media_type="audio/wav", filename=f"{note_id}.wav")
