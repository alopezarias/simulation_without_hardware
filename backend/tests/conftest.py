from __future__ import annotations

import io
import struct
import wave

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from backend.config.settings import get_settings
from backend.infrastructure.db import note_model  # noqa: F401 — registers ORM models
from backend.infrastructure.db.database import Base


# ── DB fixture (shared in-memory SQLite per test) ────────────────────────────

@pytest.fixture
async def db_engine():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def db_session(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        yield session


# ── App + client fixtures ────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def clear_classifier_cache():
    from backend.api.dependencies import _build_classifier
    _build_classifier.cache_clear()
    yield
    _build_classifier.cache_clear()


@pytest.fixture
def app(db_session, tmp_path):
    from backend.api.app import create_app
    from backend.api.dependencies import (
        get_audio_store,
        get_classifier,
        get_notifier,
        get_stt,
    )
    from backend.infrastructure.adapters.local_audio_store import LocalAudioStore
    from backend.infrastructure.adapters.null_classifier import NullClassifier
    from backend.infrastructure.adapters.null_notifier import NullNotifier
    from backend.infrastructure.adapters.null_stt import NullStt
    from backend.infrastructure.db.database import get_db

    get_settings.cache_clear()
    _app = create_app()

    async def _override_db():
        yield db_session

    _app.dependency_overrides[get_db] = _override_db
    _app.dependency_overrides[get_stt] = lambda: NullStt(fixed_text="meeting notes from today")
    _app.dependency_overrides[get_classifier] = lambda: NullClassifier()
    _app.dependency_overrides[get_notifier] = lambda: NullNotifier()
    _app.dependency_overrides[get_audio_store] = lambda: LocalAudioStore(str(tmp_path))

    yield _app
    get_settings.cache_clear()


@pytest.fixture
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


@pytest.fixture
async def authed_client(app):
    """Client pre-configured with the test auth token."""
    import os
    os.environ["NOTES_API_TOKEN"] = "test-token"
    get_settings.cache_clear()
    app.dependency_overrides  # already set

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": "Bearer test-token"},
    ) as ac:
        yield ac
    os.environ.pop("NOTES_API_TOKEN", None)
    get_settings.cache_clear()


# ── Audio helpers ────────────────────────────────────────────────────────────

def make_wav(duration_s: float = 0.5, sample_rate: int = 16000) -> bytes:
    """Generate a minimal silent WAV (16kHz, 16-bit, mono)."""
    n_samples = int(sample_rate * duration_s)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(struct.pack(f"<{n_samples}h", *([0] * n_samples)))
    return buf.getvalue()
