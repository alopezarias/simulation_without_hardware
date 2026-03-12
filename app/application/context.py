"""Application context with ports and runtime settings."""

from __future__ import annotations

from dataclasses import dataclass

from app.application.ports import AssistantPort, AudioStorePort, SpeechPort
from app.config.settings import BackendSettings


@dataclass(slots=True)
class AppContext:
    settings: BackendSettings
    assistant: AssistantPort
    speech: SpeechPort
    audio_store: AudioStorePort
