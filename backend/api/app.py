from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from backend.config.settings import get_settings
from backend.infrastructure.db.database import create_tables, init_engine

logger = logging.getLogger(__name__)


async def _audio_retention_loop(retention_days: int, audio_dir: str, interval_s: int = 3600) -> None:
    from backend.application.services.audio_retention_service import AudioRetentionService
    from backend.infrastructure.adapters.local_audio_store import LocalAudioStore
    from backend.infrastructure.db.database import get_session_factory

    service = AudioRetentionService()
    store = LocalAudioStore(audio_dir)

    while True:
        await asyncio.sleep(interval_s)
        try:
            async with get_session_factory()() as session:
                count = await service.sweep(session, store, retention_days=retention_days)
            if count:
                logger.info("audio_retention: cleared audio from %d note(s)", count)
        except Exception:
            logger.exception("audio_retention sweep failed")


@asynccontextmanager
async def _lifespan(app: FastAPI):
    s = get_settings()
    logging.basicConfig(level=s.log_level)
    os.makedirs(s.notes_audio_dir, exist_ok=True)
    init_engine(s.notes_db_url)
    await create_tables()

    task = asyncio.create_task(
        _audio_retention_loop(s.notes_audio_retention_days, s.notes_audio_dir)
    )
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


def create_app() -> FastAPI:
    from backend.api.routes.capture import router as capture_router
    from backend.api.routes.health import router as health_router
    from backend.api.routes.notes import router as notes_router
    from backend.api.routes.ws import router as ws_router

    app = FastAPI(
        title="Note-Taker Backend",
        version=get_settings().app_version,
        lifespan=_lifespan,
    )
    app.include_router(health_router)
    app.include_router(capture_router)
    app.include_router(notes_router)
    app.include_router(ws_router)
    return app


app = create_app()
