"""Application bootstrap and FastAPI wiring."""

from __future__ import annotations

import asyncio
import logging
import sys
from dataclasses import dataclass
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from backend.shared.protocol import validate_device_message

from backend.application.context import AppContext
from backend.application.services.message_bus import send_error
from backend.application.services.message_router import handle_message
from backend.config.settings import BackendSettings
from backend.domain.session import DeviceSession
from backend.infrastructure.ai.openclawd_gateway import OpenClawdGateway
from backend.infrastructure.audio.temp_pcm_store import TempPcmAudioStore
from backend.infrastructure.speech.speech_gateway import SpeechGateway
from backend.infrastructure.transport.websocket_output import WebSocketOutput


@dataclass(slots=True)
class AppContainer:
    settings: BackendSettings
    context: AppContext


def create_container() -> AppContainer:
    load_dotenv()
    settings = BackendSettings.from_env()

    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    logger = logging.getLogger("simulation-backend")

    speech_gateway = SpeechGateway()
    ctx = AppContext(
        settings=settings,
        assistant=OpenClawdGateway(),
        speech=speech_gateway,
        audio_store=TempPcmAudioStore(),
    )

    speech_caps = speech_gateway.capabilities()
    logger.info(
        "speech config loaded: reply_mode=%s capabilities=%s",
        settings.audio_reply_mode,
        speech_caps,
    )

    if speech_caps.get("stt_enabled") and not speech_caps.get("stt_available"):
        logger.warning(
            "Whisper STT is enabled but unavailable (python=%s). "
            "Use the same interpreter to run backend and install deps: "
            "`python -m pip install -r backend/requirements.txt`.",
            sys.executable,
        )
    if speech_caps.get("tts_enabled") and not speech_caps.get("tts_available"):
        logger.warning(
            "Local TTS is enabled but unavailable (python=%s). "
            "Use the same interpreter to run backend and install deps: "
            "`python -m pip install -r backend/requirements.txt`.",
            sys.executable,
        )

    return AppContainer(settings=settings, context=ctx)


def create_app() -> tuple[FastAPI, AppContainer]:
    container = create_container()
    app = FastAPI(title="Simulation Backend", version="0.2.0")
    logger = logging.getLogger("simulation-backend")

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket) -> None:
        await websocket.accept()
        session = DeviceSession(
            output=WebSocketOutput(websocket),
            active_agent=container.settings.available_agents[0],
        )
        logger.info("Client connected: %s", session.session_id)

        try:
            while True:
                raw_message = await websocket.receive_json()

                try:
                    message = validate_device_message(raw_message)
                except ValueError as exc:
                    await send_error(session, str(exc), code="bad_message")
                    continue

                await handle_message(container.context, session, message)

        except WebSocketDisconnect:
            logger.info("Client disconnected: %s", session.session_id)
        except Exception:
            logger.exception("Unhandled websocket error for %s", session.session_id)
        finally:
            if session.response_task and not session.response_task.done():
                session.response_task.cancel()
                try:
                    await session.response_task
                except asyncio.CancelledError:
                    pass
            container.context.audio_store.cleanup(session)

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "protocol_version": "0.2",
            "available_agents": container.settings.available_agents,
            "auth_token_required": bool(container.settings.device_auth_token),
            "audio_reply_mode": container.settings.audio_reply_mode,
            "speech": container.context.speech.capabilities(),
        }

    return app, container
