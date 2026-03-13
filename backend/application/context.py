"""Application context with ports and runtime settings."""

from __future__ import annotations

from dataclasses import dataclass

from backend.application.ports import AssistantPort, AudioStorePort, SpeechPort
from backend.config.settings import BackendSettings


@dataclass(slots=True)
class AppContext:
    settings: BackendSettings
    assistant: AssistantPort
    speech: SpeechPort
    audio_store: AudioStorePort
