from __future__ import annotations

from fastapi import APIRouter

from backend.config.settings import get_settings

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict:
    s = get_settings()
    return {
        "status": "ok",
        "version": s.app_version,
        "whisper_model": s.whisper_model,
        "ai_provider": s.ai_provider,
        "ai_classifier_enabled": s.ai_classifier_enabled,
        "db": "ok",
    }
