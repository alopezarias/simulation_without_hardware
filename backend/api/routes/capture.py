from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from backend.api.dependencies import get_ingestion_service, require_auth
from backend.application.services.note_ingestion import NoteIngestionService
from backend.domain.note import CaptureMode

router = APIRouter(tags=["capture"])

_ALLOWED_CONTENT_TYPES = {"audio/wav", "audio/wave", "audio/x-wav", "application/octet-stream"}
_MAX_AUDIO_BYTES = 60 * 16000 * 2  # 60s × 16kHz × 16-bit = ~1.9 MB


@router.post("/audio/capture", status_code=202)
async def capture_audio(
    audio: UploadFile = File(..., description="WAV audio, 16kHz 16-bit mono"),
    device_id: str = Form(...),
    capture_mode: str = Form("wake_word"),
    service: NoteIngestionService = Depends(get_ingestion_service),
    _auth=Depends(require_auth),
) -> dict:
    if capture_mode not in CaptureMode._value2member_map_:
        raise HTTPException(
            status_code=422,
            detail=f"capture_mode must be one of {list(CaptureMode._value2member_map_)}",
        )

    audio_bytes = await audio.read()
    if not audio_bytes:
        raise HTTPException(status_code=422, detail="Audio file is empty")
    if len(audio_bytes) > _MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail="Audio exceeds 60-second limit")

    note = await service.ingest(
        audio_bytes=audio_bytes,
        device_id=device_id,
        capture_mode=CaptureMode(capture_mode),
    )

    return {
        "note_id": note.id,
        "status": "created",
        "type": note.type.value if note.type else None,
        "summary": note.summary,
        "duration_s": note.duration_s,
    }
