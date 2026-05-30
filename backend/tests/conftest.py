from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from backend.config.settings import Settings, get_settings
from backend.api.app import create_app


def _test_settings() -> Settings:
    return Settings(
        notes_db_url="sqlite+aiosqlite:///:memory:",
        notes_api_token="test-token",
        ai_provider="null",
        ai_classifier_enabled=False,
        whisper_model="base",
    )


@pytest.fixture(autouse=True)
def override_settings(monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("NOTES_DB_URL", "sqlite+aiosqlite:///:memory:")
    monkeypatch.setenv("AI_PROVIDER", "null")
    monkeypatch.setenv("AI_CLASSIFIER_ENABLED", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
async def client():
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
