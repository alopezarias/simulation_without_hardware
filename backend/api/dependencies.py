from __future__ import annotations

from functools import lru_cache

from fastapi import Depends, HTTPException, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from backend.application.ports.audio_store import AudioStore
from backend.application.ports.classifier_port import ClassifierPort
from backend.application.ports.note_repository import NoteRepository
from backend.application.ports.notifier_port import NotifierPort
from backend.application.ports.stt_port import SttPort
from backend.application.services.note_ingestion import NoteIngestionService
from backend.config.settings import Settings, get_settings
from backend.infrastructure.adapters.local_audio_store import LocalAudioStore
from backend.infrastructure.adapters.null_classifier import NullClassifier
from backend.infrastructure.adapters.null_notifier import NullNotifier
from backend.infrastructure.adapters.sqlite_note_repository import SqliteNoteRepository
from backend.infrastructure.db.database import get_db

# Classifiers are singletons: creating the Anthropic/OpenAI async client once
# avoids re-establishing the underlying httpx connection pool per request.
@lru_cache(maxsize=4)
def _build_classifier(provider: str, model: str, api_key: str) -> ClassifierPort:
    if provider == "anthropic":
        from backend.infrastructure.adapters.anthropic_classifier import AnthropicClassifier
        return AnthropicClassifier(api_key=api_key, model=model)
    if provider == "openai":
        from backend.infrastructure.adapters.openai_classifier import OpenAIClassifier
        return OpenAIClassifier(api_key=api_key, model=model)
    return NullClassifier()

_bearer = HTTPBearer(auto_error=False)


async def require_auth(
    credentials: HTTPAuthorizationCredentials | None = Security(_bearer),
    s: Settings = Depends(get_settings),
) -> None:
    if not s.notes_api_token:
        return
    if not credentials or credentials.credentials != s.notes_api_token:
        raise HTTPException(status_code=401, detail="Unauthorized")


def get_note_repo(session: AsyncSession = Depends(get_db)) -> NoteRepository:
    return SqliteNoteRepository(session)


def get_audio_store(s: Settings = Depends(get_settings)) -> AudioStore:
    return LocalAudioStore(s.notes_audio_dir)


def get_stt(s: Settings = Depends(get_settings)) -> SttPort:
    from backend.infrastructure.adapters.whisper_stt import get_whisper_stt

    return get_whisper_stt(s.whisper_model, s.whisper_language)


def get_classifier(s: Settings = Depends(get_settings)) -> ClassifierPort:
    if not s.ai_classifier_enabled or s.ai_provider == "null":
        return NullClassifier()
    api_key = s.anthropic_api_key if s.ai_provider == "anthropic" else s.openai_api_key
    if not api_key:
        raise HTTPException(
            status_code=503,
            detail=f"AI_PROVIDER={s.ai_provider} but the corresponding API key is not set.",
        )
    return _build_classifier(s.ai_provider, s.effective_classifier_model(), api_key)


def get_notifier() -> NotifierPort:
    return NullNotifier()


def get_ingestion_service(
    stt: SttPort = Depends(get_stt),
    classifier: ClassifierPort = Depends(get_classifier),
    repo: NoteRepository = Depends(get_note_repo),
    audio_store: AudioStore = Depends(get_audio_store),
    notifier: NotifierPort = Depends(get_notifier),
) -> NoteIngestionService:
    return NoteIngestionService(
        stt=stt,
        classifier=classifier,
        repo=repo,
        audio_store=audio_store,
        notifier=notifier,
    )
