"""Compatibility facade over the new hexagonal backend structure."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from backend.application.services import message_bus as message_bus_service
from backend.application.services import message_router as message_router_service
from backend.application.services import recording as recording_service
from backend.application.services import session_init as session_init_service
from backend.application.services import turn_processing as turn_processing_service
from backend.bootstrap import create_app
from backend.config.settings import BackendSettings
from backend.domain.session import DeviceSession as CoreDeviceSession
from backend.infrastructure.logging.sanitizer import sanitize_message_for_log
from backend.shared.protocol import UiState, validate_device_message

app, _container = create_app()
logger = logging.getLogger("simulation-backend")

# Legacy globals maintained for compatibility with tests and scripts.
ENABLE_FAKE_AUDIO = _container.settings.enable_fake_audio
LOOPBACK_AUDIO_ENABLED = _container.settings.loopback_audio_enabled
LOOPBACK_CHUNK_MS = _container.settings.loopback_chunk_ms
AUDIO_REPLY_MODE = _container.settings.audio_reply_mode
DEVICE_AUTH_TOKEN = _container.settings.device_auth_token
AVAILABLE_AGENTS = list(_container.settings.available_agents)
ALLOWED_DEVICE_IDS = set(_container.settings.allowed_device_ids)
adapter = _container.context.assistant
speech_pipeline = _container.context.speech


class _WebSocketOutputCompat:
    """Adapter minimo para reutilizar objetos websocket legacy en la fachada."""

    def __init__(self, websocket: Any) -> None:
        """Guardar referencia al websocket subyacente."""
        self._websocket = websocket

    async def send_json(self, message: dict[str, Any]) -> None:
        """Enviar JSON al cliente WS sin transformar el payload."""
        await self._websocket.send_json(message)


class DeviceSession(CoreDeviceSession):
    """Compatibility session that accepts a websocket like the old backend."""

    def __init__(self, websocket: Any, **kwargs: Any) -> None:
        """Crear sesion legacy inyectando salida WS y agente activo por defecto."""
        active_agent = kwargs.pop("active_agent", None) or (
            AVAILABLE_AGENTS[0] if AVAILABLE_AGENTS else "assistant-general"
        )
        super().__init__(
            output=_WebSocketOutputCompat(websocket),
            active_agent=active_agent,
            **kwargs,
        )
        self.websocket = websocket


def _sync_runtime_from_legacy_globals() -> None:
    """Sincronizar globals legacy hacia el contenedor hexagonal actual.

    Esta funcion permite que monkeypatch/tests y scripts antiguos sigan
    ajustando comportamiento por medio de constantes de modulo.
    """
    global AUDIO_REPLY_MODE

    if AUDIO_REPLY_MODE not in {"assistant", "echo"}:
        logger.warning("Unknown AUDIO_REPLY_MODE=%s; forcing 'assistant'", AUDIO_REPLY_MODE)
        AUDIO_REPLY_MODE = "assistant"

    _container.settings = BackendSettings(
        enable_fake_audio=ENABLE_FAKE_AUDIO,
        loopback_audio_enabled=LOOPBACK_AUDIO_ENABLED,
        loopback_chunk_ms=LOOPBACK_CHUNK_MS,
        audio_reply_mode=AUDIO_REPLY_MODE,
        device_auth_token=DEVICE_AUTH_TOKEN,
        available_agents=list(AVAILABLE_AGENTS) or ["assistant-general"],
        allowed_device_ids=set(ALLOWED_DEVICE_IDS),
        log_level=_container.settings.log_level,
    )
    _container.context.settings = _container.settings
    _container.context.assistant = adapter
    _container.context.speech = speech_pipeline


def _sanitize_for_log(message: dict[str, Any]) -> dict[str, Any]:
    """Sanitizar payload para logging seguro (sin volcado base64 completo)."""
    return sanitize_message_for_log(message)


def _close_audio_file(session: CoreDeviceSession) -> None:
    """Cerrar el archivo temporal de audio asociado a una sesion."""
    _sync_runtime_from_legacy_globals()
    _container.context.audio_store.close(session)


def _cleanup_audio_file(session: CoreDeviceSession) -> None:
    """Cerrar y borrar archivo temporal de audio de la sesion."""
    _sync_runtime_from_legacy_globals()
    _container.context.audio_store.cleanup(session)


async def send(session: CoreDeviceSession, message: dict[str, Any]) -> None:
    """Enviar un mensaje de protocolo al dispositivo."""
    await message_bus_service.send(session, message)


async def send_ui_state(session: CoreDeviceSession, state: UiState) -> None:
    """Publicar cambio de estado de UI al dispositivo."""
    await message_bus_service.send_ui_state(session, state)


async def send_error(session: CoreDeviceSession, detail: str, code: str = "protocol_error") -> None:
    """Publicar error de protocolo/aplicacion y transicionar UI a error."""
    await message_bus_service.send_error(session, detail, code=code)


async def ensure_authenticated(session: CoreDeviceSession) -> bool:
    """Verificar que la sesion ya paso por `device.hello` autenticado."""
    return await session_init_service.ensure_authenticated(session)


async def ensure_not_busy(session: CoreDeviceSession) -> bool:
    """Verificar que no existe un turno de respuesta aun en ejecucion."""
    return await session_init_service.ensure_not_busy(session)


def validate_device_hello(message: dict[str, Any]) -> tuple[str, str | None]:
    """Validar handshake inicial de dispositivo y opcion de agente activo."""
    _sync_runtime_from_legacy_globals()
    return session_init_service.validate_device_hello(_container.context, message)


async def start_recording(session: CoreDeviceSession, message: dict[str, Any]) -> None:
    """Iniciar captura de turno y preparar almacenamiento temporal PCM."""
    _sync_runtime_from_legacy_globals()
    await recording_service.start_recording(_container.context, session, message)


async def cancel_recording(session: CoreDeviceSession) -> None:
    """Cancelar captura y limpiar estado transitorio del turno actual."""
    _sync_runtime_from_legacy_globals()
    await recording_service.cancel_recording(_container.context, session)


async def interrupt_assistant(session: CoreDeviceSession) -> None:
    """Interrumpir respuesta del asistente en curso y volver a `idle`."""
    await recording_service.interrupt_assistant(session)


async def stream_pcm_audio_file(
    session: CoreDeviceSession,
    turn_id: str,
    pcm_path: str,
    *,
    sample_rate: int,
    channels: int,
    source: str,
    loopback: bool = False,
) -> int:
    """Emitir un archivo PCM16 como stream de chunks `assistant.audio.*`."""
    _sync_runtime_from_legacy_globals()
    return await turn_processing_service.stream_pcm_audio_file(
        _container.context,
        session,
        turn_id,
        pcm_path,
        sample_rate=sample_rate,
        channels=channels,
        source=source,
        loopback=loopback,
    )


async def stream_loopback_audio(session: CoreDeviceSession, turn_id: str) -> bool:
    """Reenviar audio capturado del usuario al dispositivo (modo loopback)."""
    _sync_runtime_from_legacy_globals()
    return await turn_processing_service.stream_loopback_audio(_container.context, session, turn_id)


async def transcribe_recording(session: CoreDeviceSession) -> str:
    """Transcribir audio temporal de sesion a texto con el puerto de speech."""
    _sync_runtime_from_legacy_globals()
    return await turn_processing_service.transcribe_recording(_container.context, session)


async def synthesize_text_to_audio(session: CoreDeviceSession, turn_id: str, text: str) -> bool:
    """Sintetizar texto a PCM y transmitirlo al dispositivo en streaming."""
    _sync_runtime_from_legacy_globals()
    return await turn_processing_service.synthesize_text_to_audio(_container.context, session, turn_id, text)


async def process_turn(session: CoreDeviceSession) -> None:
    """Orquestar turno completo: transcript, respuesta, TTS/loopback y cierre."""
    _sync_runtime_from_legacy_globals()
    await turn_processing_service.process_turn(_container.context, session)


async def send_session_ready(session: CoreDeviceSession) -> None:
    """Enviar payload `session.ready` con capacidades y metadata de sesion."""
    _sync_runtime_from_legacy_globals()
    await session_init_service.send_session_ready(_container.context, session)


async def handle_message(session: CoreDeviceSession, message: dict[str, Any]) -> None:
    """Despachar mensaje de protocolo entrante al caso de uso adecuado."""
    _sync_runtime_from_legacy_globals()
    await message_router_service.handle_message(_container.context, session, message)


async def websocket_endpoint(websocket: WebSocket) -> None:
    """Endpoint WS legacy para tests directos de fachada.

    La app productiva usa el endpoint registrado en `backend.bootstrap`.
    Este wrapper se mantiene para compatibilidad con utilidades de prueba.
    """
    await websocket.accept()
    session = DeviceSession(websocket=websocket)
    logger.info("Client connected: %s", session.session_id)

    try:
        while True:
            raw_message = await websocket.receive_json()

            try:
                message = validate_device_message(raw_message)
            except ValueError as exc:
                await send_error(session, str(exc), code="bad_message")
                continue

            await handle_message(session, message)

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
        _cleanup_audio_file(session)


async def health() -> dict[str, Any]:
    """Construir respuesta de salud con configuracion y capacidades actuales."""
    _sync_runtime_from_legacy_globals()
    return {
        "status": "ok",
        "protocol_version": "0.2",
        "available_agents": _container.settings.available_agents,
        "auth_token_required": bool(_container.settings.device_auth_token),
        "audio_reply_mode": _container.settings.audio_reply_mode,
        "speech": _container.context.speech.capabilities(),
    }
