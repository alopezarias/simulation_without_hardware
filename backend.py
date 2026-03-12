"""Minimal backend for the conversational-device simulation."""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import tempfile
import time
from dataclasses import dataclass, field
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from openclawd_adapter import OpenClawdAdapter
from protocol import (
    UiState,
    build_message,
    new_session_id,
    new_turn_id,
    require_fields,
    validate_device_message,
)
from speech_pipeline import SpeechPipeline

load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("simulation-backend")

app = FastAPI(title="Simulation Backend", version="0.2.0")
adapter = OpenClawdAdapter()
speech_pipeline = SpeechPipeline()

ENABLE_FAKE_AUDIO = os.getenv("ENABLE_FAKE_AUDIO", "false").lower() == "true"
LOOPBACK_AUDIO_ENABLED = os.getenv("LOOPBACK_AUDIO_ENABLED", "true").lower() == "true"
LOOPBACK_CHUNK_MS = int(os.getenv("LOOPBACK_CHUNK_MS", "120"))
AUDIO_REPLY_MODE = os.getenv("AUDIO_REPLY_MODE", "assistant").strip().lower()
DEVICE_AUTH_TOKEN = os.getenv("SIM_DEVICE_AUTH_TOKEN", "").strip()
AVAILABLE_AGENTS = [
    value.strip()
    for value in os.getenv(
        "SIM_AVAILABLE_AGENTS",
        "assistant-general,assistant-tech,assistant-ops",
    ).split(",")
    if value.strip()
]
ALLOWED_DEVICE_IDS = {
    value.strip()
    for value in os.getenv("SIM_ALLOWED_DEVICE_IDS", "").split(",")
    if value.strip()
}

if not AVAILABLE_AGENTS:
    AVAILABLE_AGENTS = ["assistant-general"]

if AUDIO_REPLY_MODE not in {"assistant", "echo"}:
    logger.warning("Unknown AUDIO_REPLY_MODE=%s; forcing 'assistant'", AUDIO_REPLY_MODE)
    AUDIO_REPLY_MODE = "assistant"

logger.info(
    "speech config loaded: reply_mode=%s capabilities=%s",
    AUDIO_REPLY_MODE,
    speech_pipeline.capabilities(),
)


@dataclass
class DeviceSession:
    websocket: WebSocket
    session_id: str = field(default_factory=new_session_id)
    device_id: str = "unknown-device"
    active_agent: str = AVAILABLE_AGENTS[0]
    ui_state: UiState = UiState.IDLE
    recording: bool = False
    turn_id: str | None = None
    text_fragments: list[str] = field(default_factory=list)
    response_task: asyncio.Task[None] | None = None
    interrupted: asyncio.Event = field(default_factory=asyncio.Event)
    authenticated: bool = False
    turn_started_monotonic: float | None = None
    recording_config: dict[str, Any] = field(default_factory=dict)
    audio_chunks_received: int = 0
    audio_bytes_received: int = 0
    audio_file_path: str | None = None
    audio_file_handle: Any | None = None


def _sanitize_for_log(message: dict[str, Any]) -> dict[str, Any]:
    safe = dict(message)

    payload = safe.get("payload")
    if isinstance(payload, str) and payload:
        safe["payload"] = f"<base64:{len(payload)} chars>"

    text = safe.get("text")
    if isinstance(text, str) and len(text) > 240:
        safe["text"] = text[:240] + "...<trimmed>"

    return safe


def _close_audio_file(session: DeviceSession) -> None:
    if session.audio_file_handle is None:
        return

    try:
        session.audio_file_handle.close()
    finally:
        session.audio_file_handle = None


def _cleanup_audio_file(session: DeviceSession) -> None:
    _close_audio_file(session)
    path = session.audio_file_path
    session.audio_file_path = None
    if not path:
        return
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    except Exception:
        logger.exception("Failed to delete temp audio file: %s", path)


async def send(session: DeviceSession, message: dict[str, Any]) -> None:
    logger.info("OUT -> %s", _sanitize_for_log(message))
    await session.websocket.send_json(message)


async def send_ui_state(session: DeviceSession, state: UiState) -> None:
    session.ui_state = state
    await send(session, build_message("ui.state", state=state.value))


async def send_error(session: DeviceSession, detail: str, code: str = "protocol_error") -> None:
    await send(session, build_message("error", code=code, detail=detail))
    await send_ui_state(session, UiState.ERROR)


async def ensure_authenticated(session: DeviceSession) -> bool:
    if session.authenticated:
        return True

    await send_error(
        session,
        "device.hello must be sent and authenticated before other messages.",
        code="unauthorized",
    )
    return False


async def ensure_not_busy(session: DeviceSession) -> bool:
    if session.response_task and not session.response_task.done():
        await send_error(
            session,
            "Cannot start a new turn while assistant is speaking. Send assistant.interrupt first.",
            code="busy",
        )
        return False

    return True


def validate_device_hello(message: dict[str, Any]) -> tuple[str, str | None]:
    require_fields(message, "device_id")
    device_id = str(message["device_id"]).strip()
    if not device_id:
        raise ValueError("device_id cannot be empty.")

    if ALLOWED_DEVICE_IDS and device_id not in ALLOWED_DEVICE_IDS:
        raise ValueError(f"device_id '{device_id}' is not allowed.")

    active_agent = None
    if "active_agent" in message:
        active_agent = str(message["active_agent"]).strip()
        if active_agent and active_agent not in AVAILABLE_AGENTS:
            raise ValueError(
                f"active_agent '{active_agent}' is not valid. "
                f"Available agents: {', '.join(AVAILABLE_AGENTS)}"
            )

    if DEVICE_AUTH_TOKEN:
        token = str(message.get("auth_token", "")).strip()
        if token != DEVICE_AUTH_TOKEN:
            raise ValueError("Invalid auth token for device.")

    return device_id, active_agent


async def start_recording(session: DeviceSession, message: dict[str, Any]) -> None:
    if not await ensure_not_busy(session):
        return

    if session.recording:
        await send_error(session, "recording.start ignored because recording is already active.")
        return

    session.recording = True
    session.turn_id = str(message.get("turn_id") or new_turn_id())
    session.text_fragments.clear()
    session.interrupted.clear()
    session.turn_started_monotonic = time.monotonic()
    session.recording_config = {
        "codec": message.get("codec", "pcm16"),
        "sample_rate": message.get("sample_rate", 16000),
        "channels": message.get("channels", 1),
    }
    session.audio_chunks_received = 0
    session.audio_bytes_received = 0
    _cleanup_audio_file(session)
    fd, audio_path = tempfile.mkstemp(prefix="sim_audio_", suffix=".pcm")
    os.close(fd)
    session.audio_file_path = audio_path
    session.audio_file_handle = open(audio_path, "wb")
    await send_ui_state(session, UiState.LISTENING)


async def cancel_recording(session: DeviceSession) -> None:
    session.recording = False
    session.turn_id = None
    session.text_fragments.clear()
    session.turn_started_monotonic = None
    session.recording_config.clear()
    session.audio_chunks_received = 0
    session.audio_bytes_received = 0
    _cleanup_audio_file(session)
    await send_ui_state(session, UiState.IDLE)


async def interrupt_assistant(session: DeviceSession) -> None:
    session.interrupted.set()

    if session.response_task and not session.response_task.done():
        session.response_task.cancel()
        try:
            await session.response_task
        except asyncio.CancelledError:
            pass

    await send_ui_state(session, UiState.IDLE)


async def stream_pcm_audio_file(
    session: DeviceSession,
    turn_id: str,
    pcm_path: str,
    *,
    sample_rate: int,
    channels: int,
    source: str,
    loopback: bool = False,
) -> int:
    codec = "pcm16"
    bytes_per_second = max(1, sample_rate * channels * 2)
    chunk_size = max(256, int(bytes_per_second * LOOPBACK_CHUNK_MS / 1000))
    total_bytes = 0
    seq = 0
    timestamp_ms = 0

    try:
        total_bytes = os.path.getsize(pcm_path)
    except OSError:
        total_bytes = 0

    try:
        await send(
            session,
            build_message(
                "assistant.audio.start",
                turn_id=turn_id,
                codec=codec,
                sample_rate=sample_rate,
                channels=channels,
                source=source,
                loopback=loopback,
                total_bytes=total_bytes,
            ),
        )
    except Exception as exc:
        detail = str(exc).lower()
        if "websocket.close" in detail or "response already completed" in detail:
            session.interrupted.set()
            raise asyncio.CancelledError() from exc
        raise

    try:
        with open(pcm_path, "rb") as handle:
            while True:
                if session.interrupted.is_set():
                    break

                chunk = handle.read(chunk_size)
                if not chunk:
                    break

                duration_ms = max(1, int(len(chunk) * 1000 / bytes_per_second))
                encoded = base64.b64encode(chunk).decode("ascii")
                try:
                    await send(
                        session,
                        build_message(
                            "assistant.audio.chunk",
                            turn_id=turn_id,
                            seq=seq,
                            timestamp_ms=timestamp_ms,
                            duration_ms=duration_ms,
                            payload=encoded,
                            source=source,
                            loopback=loopback,
                        ),
                    )
                except Exception as exc:
                    detail = str(exc).lower()
                    if "websocket.close" in detail or "response already completed" in detail:
                        session.interrupted.set()
                        raise asyncio.CancelledError() from exc
                    raise

                seq += 1
                timestamp_ms += duration_ms
                await asyncio.sleep(duration_ms / 1000)
    finally:
        try:
            await send(
                session,
                build_message(
                    "assistant.audio.end",
                    turn_id=turn_id,
                    source=source,
                    loopback=loopback,
                    total_chunks=seq,
                ),
            )
        except Exception:
            # Socket may already be closed by the client.
            pass

    return seq


async def stream_loopback_audio(session: DeviceSession, turn_id: str) -> bool:
    if not LOOPBACK_AUDIO_ENABLED:
        return False
    if not session.audio_file_path:
        return False
    if session.audio_bytes_received <= 0:
        return False

    sample_rate = int(session.recording_config.get("sample_rate", 16000) or 16000)
    channels = int(session.recording_config.get("channels", 1) or 1)
    await stream_pcm_audio_file(
        session,
        turn_id,
        session.audio_file_path,
        sample_rate=sample_rate,
        channels=channels,
        source="device_audio",
        loopback=True,
    )
    return True


async def transcribe_recording(session: DeviceSession) -> str:
    if not session.audio_file_path:
        return ""
    if session.audio_bytes_received <= 0:
        return ""
    if not speech_pipeline.stt_available:
        return ""

    sample_rate = int(session.recording_config.get("sample_rate", 16000) or 16000)
    channels = int(session.recording_config.get("channels", 1) or 1)

    try:
        text = await asyncio.to_thread(
            speech_pipeline.transcribe_pcm_file,
            session.audio_file_path,
            sample_rate,
            channels,
        )
        logger.info(
            "transcription completed device=%s session=%s turn=%s text_len=%s",
            session.device_id,
            session.session_id,
            session.turn_id,
            len(text),
        )
        return text
    except Exception:
        logger.exception(
            "transcription failed device=%s session=%s turn=%s",
            session.device_id,
            session.session_id,
            session.turn_id,
        )
        return ""


async def synthesize_text_to_audio(session: DeviceSession, turn_id: str, text: str) -> bool:
    cleaned = " ".join(text.split()).strip()
    if not cleaned:
        return False
    if not speech_pipeline.tts_available:
        return False

    sample_rate = int(session.recording_config.get("sample_rate", 16000) or 16000)
    channels = int(session.recording_config.get("channels", 1) or 1)
    pcm_path: str | None = None
    total_bytes = 0
    try:
        pcm_path, total_bytes = await asyncio.to_thread(
            speech_pipeline.synthesize_text_to_pcm_file,
            cleaned,
            sample_rate,
            channels,
        )
        if total_bytes <= 0:
            return False

        await stream_pcm_audio_file(
            session,
            turn_id,
            pcm_path,
            sample_rate=sample_rate,
            channels=channels,
            source="tts",
        )
        return True
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception(
            "tts generation failed device=%s session=%s turn=%s",
            session.device_id,
            session.session_id,
            session.turn_id,
        )
        return False
    finally:
        if pcm_path:
            try:
                os.remove(pcm_path)
            except FileNotFoundError:
                pass


async def process_turn(session: DeviceSession) -> None:
    turn_id = session.turn_id or new_turn_id()
    typed_text = " ".join(fragment.strip() for fragment in session.text_fragments).strip()
    session.text_fragments.clear()

    transcribed_text = ""
    if session.audio_bytes_received > 0:
        transcribed_text = await transcribe_recording(session)

    if typed_text and transcribed_text:
        user_text = f"{typed_text} {transcribed_text}".strip()
    elif typed_text:
        user_text = typed_text
    elif transcribed_text:
        user_text = transcribed_text
    elif session.audio_bytes_received > 0:
        kb = session.audio_bytes_received / 1024
        user_text = (
            f"(audio recibido: {kb:.1f} KB en "
            f"{session.audio_chunks_received} chunks; transcripcion no disponible)"
        )
    else:
        user_text = "(turno vacio)"

    await send(
        session,
        build_message("transcript.final", turn_id=turn_id, text=user_text),
    )
    await send(
        session,
        build_message("assistant.start", turn_id=turn_id, agent_id=session.active_agent),
    )
    await send_ui_state(session, UiState.SPEAKING)

    collected_chunks: list[str] = []
    loopback_used = False
    tts_used = False
    started_at = session.turn_started_monotonic

    try:
        if AUDIO_REPLY_MODE == "echo":
            final_text = user_text
            if final_text:
                await send(
                    session,
                    build_message(
                        "assistant.text.partial",
                        turn_id=turn_id,
                        text=final_text,
                        accumulated=final_text,
                    ),
                )
        else:
            async for chunk in adapter.stream_response(
                agent_id=session.active_agent,
                user_text=user_text,
                session_id=session.session_id,
            ):
                if session.interrupted.is_set():
                    break

                collected_chunks.append(chunk)
                partial_text = "".join(collected_chunks)
                await send(
                    session,
                    build_message(
                        "assistant.text.partial",
                        turn_id=turn_id,
                        text=chunk,
                        accumulated=partial_text,
                    ),
                )

            final_text = "".join(collected_chunks).strip()

        if not final_text:
            final_text = "(sin respuesta)"

        tts_used = await synthesize_text_to_audio(session, turn_id, final_text)
        if session.interrupted.is_set():
            raise asyncio.CancelledError()

        if not tts_used and ENABLE_FAKE_AUDIO:
            encoded = base64.b64encode(final_text.encode("utf-8")).decode("ascii")
            await send(
                session,
                build_message(
                    "assistant.audio.start",
                    turn_id=turn_id,
                    codec="pcm16",
                    sample_rate=16000,
                    channels=1,
                    source="fake",
                ),
            )
            await send(
                session,
                build_message(
                    "assistant.audio.chunk",
                    turn_id=turn_id,
                    seq=0,
                    timestamp_ms=0,
                    duration_ms=120,
                    payload=encoded,
                    source="fake",
                ),
            )
            await send(
                session,
                build_message("assistant.audio.end", turn_id=turn_id, source="fake", total_chunks=1),
            )

        if (
            not session.interrupted.is_set()
            and not tts_used
            and session.audio_bytes_received > 0
            and LOOPBACK_AUDIO_ENABLED
        ):
            loopback_used = await stream_loopback_audio(session, turn_id)

        latency_ms: int | None = None
        if started_at is not None:
            latency_ms = int((time.monotonic() - started_at) * 1000)

        await send(
            session,
            build_message(
                "assistant.text.final",
                turn_id=turn_id,
                text=final_text,
                interrupted=session.interrupted.is_set(),
                agent_id=session.active_agent,
                latency_ms=latency_ms,
            ),
        )
        logger.info(
            "turn.completed session=%s device=%s turn=%s interrupted=%s latency_ms=%s tts=%s loopback=%s",
            session.session_id,
            session.device_id,
            turn_id,
            session.interrupted.is_set(),
            latency_ms,
            tts_used,
            loopback_used,
        )

    except asyncio.CancelledError:
        final_text = "".join(collected_chunks).strip()
        await send(
            session,
            build_message(
                "assistant.text.final",
                turn_id=turn_id,
                text=final_text,
                interrupted=True,
                agent_id=session.active_agent,
            ),
        )
    except Exception as exc:
        logger.exception("Error while processing turn")
        await send_error(session, f"Assistant generation failed: {exc}", code="assistant_error")
    finally:
        session.recording = False
        session.turn_id = None
        session.response_task = None
        session.interrupted.clear()
        session.turn_started_monotonic = None
        session.recording_config.clear()
        session.audio_chunks_received = 0
        session.audio_bytes_received = 0
        _cleanup_audio_file(session)
        await send_ui_state(session, UiState.IDLE)


async def send_session_ready(session: DeviceSession) -> None:
    await send(
        session,
        build_message(
            "session.ready",
            session_id=session.session_id,
            device_id=session.device_id,
            active_agent=session.active_agent,
            available_agents=AVAILABLE_AGENTS,
            protocol_version="0.2",
            speech=speech_pipeline.capabilities(),
            audio_reply_mode=AUDIO_REPLY_MODE,
        ),
    )


async def handle_message(session: DeviceSession, message: dict[str, Any]) -> None:
    message_type = message["type"]
    logger.info("IN  <- %s", _sanitize_for_log(message))

    if message_type == "device.hello":
        try:
            device_id, requested_agent = validate_device_hello(message)
        except ValueError as exc:
            await send_error(session, str(exc), code="auth_error")
            return

        session.device_id = device_id
        session.authenticated = True
        if requested_agent:
            session.active_agent = requested_agent

        await send_session_ready(session)
        await send_ui_state(session, UiState.IDLE)
        return

    if not await ensure_authenticated(session):
        return

    if message_type == "session.start":
        await send_session_ready(session)
        return

    if message_type == "agent.select":
        require_fields(message, "agent_id")
        requested = str(message["agent_id"]).strip()
        if requested not in AVAILABLE_AGENTS:
            await send_error(
                session,
                f"Unknown agent '{requested}'. Available agents: {', '.join(AVAILABLE_AGENTS)}",
                code="invalid_agent",
            )
            return

        session.active_agent = requested
        await send(session, build_message("agent.selected", agent_id=session.active_agent))
        await send_ui_state(session, UiState.IDLE)
        return

    if message_type == "recording.start":
        await start_recording(session, message)
        return

    if message_type == "audio.chunk":
        if not session.recording:
            await start_recording(session, message)

        payload = message.get("payload")
        chunk_size_bytes = 0
        decoded_chunk = b""
        if isinstance(payload, str) and payload.strip():
            try:
                decoded_chunk = base64.b64decode(payload, validate=True)
                chunk_size_bytes = len(decoded_chunk)
            except Exception:
                chunk_size_bytes = 0
                decoded_chunk = b""

        if chunk_size_bytes <= 0:
            chunk_size_bytes = int(message.get("size_bytes", 0) or 0)

        if decoded_chunk and session.audio_file_handle is not None:
            session.audio_file_handle.write(decoded_chunk)

        session.audio_chunks_received += 1
        session.audio_bytes_received += max(0, chunk_size_bytes)

        kb = chunk_size_bytes / 1024 if chunk_size_bytes > 0 else 0.0
        logger.info(
            "audio.chunk received device=%s session=%s turn=%s seq=%s size_bytes=%s size_kb=%.2f duration_ms=%s total_chunks=%s total_kb=%.2f",
            session.device_id,
            session.session_id,
            session.turn_id,
            message.get("seq"),
            chunk_size_bytes,
            kb,
            message.get("duration_ms"),
            session.audio_chunks_received,
            session.audio_bytes_received / 1024,
        )

        text_hint = str(message.get("text_hint", "")).strip()
        if text_hint:
            session.text_fragments.append(text_hint)
            await send(
                session,
                build_message("transcript.partial", turn_id=session.turn_id, text=text_hint),
            )
        return

    if message_type == "debug.user_text":
        require_fields(message, "text")
        text = str(message["text"]).strip()
        if not text:
            await send_error(session, "debug.user_text requires a non-empty text.")
            return

        if not session.recording:
            await start_recording(session, message)

        session.text_fragments.append(text)
        await send(
            session,
            build_message("transcript.partial", turn_id=session.turn_id, text=text),
        )
        return

    if message_type == "recording.stop":
        if not session.recording:
            await send_error(session, "Cannot stop recording because device is not listening.")
            return

        _close_audio_file(session)
        session.recording = False
        await send_ui_state(session, UiState.PROCESSING)

        if session.response_task and not session.response_task.done():
            session.response_task.cancel()

        session.response_task = asyncio.create_task(process_turn(session))
        return

    if message_type == "recording.cancel":
        await cancel_recording(session)
        return

    if message_type == "assistant.interrupt":
        await interrupt_assistant(session)
        return

    if message_type == "ping":
        await send(session, build_message("pong"))
        return

    await send_error(session, f"Unknown message type: {message_type}")


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
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


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "protocol_version": "0.2",
        "available_agents": AVAILABLE_AGENTS,
        "auth_token_required": bool(DEVICE_AUTH_TOKEN),
        "audio_reply_mode": AUDIO_REPLY_MODE,
        "speech": speech_pipeline.capabilities(),
    }
