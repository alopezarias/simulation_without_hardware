from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from backend.config.settings import get_settings
from backend.infrastructure.db.database import create_tables, init_engine


@asynccontextmanager
async def _lifespan(app: FastAPI):
    s = get_settings()
    logging.basicConfig(level=s.log_level)
    os.makedirs(s.notes_audio_dir, exist_ok=True)
    init_engine(s.notes_db_url)
    await create_tables()
    yield


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
