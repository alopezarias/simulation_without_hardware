from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Server
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"
    app_version: str = "0.1.0"

    # Database
    notes_db_url: str = "sqlite+aiosqlite:///data/notes.db"

    # Audio storage
    notes_audio_dir: str = "data/audio"
    notes_audio_retention_days: int = 30

    # Auth (simple static token for v1)
    notes_api_token: str = ""

    # AI classifier
    ai_provider: Literal["anthropic", "openai", "null"] = "null"
    ai_classifier_enabled: bool = True
    ai_classifier_model: str = ""
    anthropic_api_key: str = ""
    openai_api_key: str = ""

    # Whisper STT
    whisper_model: str = "base"
    whisper_language: str = "auto"

    @field_validator("log_level")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.upper()

    def effective_classifier_model(self) -> str:
        if self.ai_classifier_model:
            return self.ai_classifier_model
        defaults = {
            "anthropic": "claude-haiku-4-5-20251001",
            "openai": "gpt-4o-mini",
            "null": "",
        }
        return defaults[self.ai_provider]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
