from __future__ import annotations

from backend.application.ports.audio_store import AudioStore
from backend.application.ports.classifier_port import ClassifierPort
from backend.application.ports.note_repository import NoteRepository
from backend.application.ports.notifier_port import NotifierPort
from backend.application.ports.stt_port import SttPort
from backend.domain.note import CaptureMode, Note


class NoteIngestionService:
    def __init__(
        self,
        stt: SttPort,
        classifier: ClassifierPort,
        repo: NoteRepository,
        audio_store: AudioStore,
        notifier: NotifierPort,
    ) -> None:
        self._stt = stt
        self._classifier = classifier
        self._repo = repo
        self._audio_store = audio_store
        self._notifier = notifier

    async def ingest(
        self,
        audio_bytes: bytes,
        device_id: str,
        capture_mode: CaptureMode,
    ) -> Note:
        note = Note(text="", device_id=device_id, capture_mode=capture_mode)

        audio_path = await self._audio_store.save(note.id, audio_bytes)
        note.audio_path = audio_path

        transcription = await self._stt.transcribe(audio_bytes)
        note.text = transcription.text
        note.duration_s = transcription.duration_s

        classification = await self._classifier.classify(transcription.text)
        note.type = classification.type
        note.tags = classification.tags
        note.entities = classification.entities
        note.summary = classification.summary

        await self._repo.save(note)
        await self._notifier.broadcast("note.created", _serialize(note))

        return note


def _serialize(note: Note) -> dict:
    return {
        "id": note.id,
        "device_id": note.device_id,
        "text": note.text,
        "type": note.type.value if note.type else None,
        "tags": note.tags,
        "entities": note.entities,
        "summary": note.summary,
        "duration_s": note.duration_s,
        "capture_mode": note.capture_mode.value,
        "created_at": note.created_at.isoformat(),
    }
