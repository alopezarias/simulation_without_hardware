"""Tests for Settings configuration."""

from __future__ import annotations

import pytest

from backend.config.settings import Settings


def test_settings_defaults():
    s = Settings(notes_db_url="sqlite+aiosqlite:///:memory:")
    assert s.host == "0.0.0.0"
    assert s.port == 8000
    assert s.ai_provider == "null"
    assert s.whisper_model == "base"
    assert s.whisper_language == "auto"


def test_log_level_uppercased():
    s = Settings(notes_db_url="sqlite+aiosqlite:///:memory:", log_level="debug")
    assert s.log_level == "DEBUG"


def test_ai_provider_anthropic_sets_default_model():
    s = Settings(
        notes_db_url="sqlite+aiosqlite:///:memory:",
        ai_provider="anthropic",
    )
    assert "haiku" in s.effective_classifier_model()


def test_ai_provider_openai_sets_default_model():
    s = Settings(
        notes_db_url="sqlite+aiosqlite:///:memory:",
        ai_provider="openai",
    )
    assert "gpt" in s.effective_classifier_model()


def test_custom_model_overrides_default():
    s = Settings(
        notes_db_url="sqlite+aiosqlite:///:memory:",
        ai_provider="anthropic",
        ai_classifier_model="claude-opus-4-7",
    )
    assert s.effective_classifier_model() == "claude-opus-4-7"


def test_invalid_ai_provider_raises():
    with pytest.raises(Exception):
        Settings(notes_db_url="sqlite+aiosqlite:///:memory:", ai_provider="gemini")
