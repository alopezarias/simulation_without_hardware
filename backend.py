"""Minimal backend for the conversational-device simulation."""

from __future__ import annotations

import asyncio
import base64
import logging
import os
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

load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("simulation-backend")

app = FastAPI(title="Simulation Backend", version="0.2.0")
adapter = OpenClawdAdapter()

ENABLE_FAKE_AUDIO = os.getenv("ENABLE_FAKE_AUDIO", "false").lower() == "true"
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


async def send(session: DeviceSession, message: dict[str, Any]) -> None:
    logger.info("OUT -> %s", message)
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
    await send_ui_state(session, UiState.LISTENING)


async def cancel_recording(session: DeviceSession) -> None:
    session.recording = False
    session.turn_id = None
    session.text_fragments.clear()
    session.turn_started_monotonic = None
    session.recording_config.clear()
    session.audio_chunks_received = 0
    session.audio_bytes_received = 0
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


async def process_turn(session: DeviceSession) -> None:
    turn_id = session.turn_id or new_turn_id()
    user_text = " ".join(fragment.strip() for fragment in session.text_fragments).strip()
    session.text_fragments.clear()

    if not user_text:
        if session.audio_bytes_received > 0:
            kb = session.audio_bytes_received / 1024
            user_text = (
                f"(audio recibido: {kb:.1f} KB en "
                f"{session.audio_chunks_received} chunks; transcripcion pendiente)"
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
    audio_started = False
    started_at = session.turn_started_monotonic

    try:
        if ENABLE_FAKE_AUDIO:
            await send(
                session,
                build_message(
                    "assistant.audio.start",
                    turn_id=turn_id,
                    codec="pcm16",
                    sample_rate=16000,
                    channels=1,
                ),
            )
            audio_started = True

        seq = 0
        timestamp_ms = 0
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

            if audio_started:
                encoded = base64.b64encode(chunk.encode("utf-8")).decode("ascii")
                await send(
                    session,
                    build_message(
                        "assistant.audio.chunk",
                        turn_id=turn_id,
                        seq=seq,
                        timestamp_ms=timestamp_ms,
                        duration_ms=120,
                        payload=encoded,
                    ),
                )
                seq += 1
                timestamp_ms += 120

        final_text = "".join(collected_chunks).strip()
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
            "turn.completed session=%s device=%s turn=%s interrupted=%s latency_ms=%s",
            session.session_id,
            session.device_id,
            turn_id,
            session.interrupted.is_set(),
            latency_ms,
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
        if audio_started:
            await send(session, build_message("assistant.audio.end", turn_id=turn_id))

        session.recording = False
        session.turn_id = None
        session.response_task = None
        session.interrupted.clear()
        session.turn_started_monotonic = None
        session.recording_config.clear()
        session.audio_chunks_received = 0
        session.audio_bytes_received = 0
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
        ),
    )


async def handle_message(session: DeviceSession, message: dict[str, Any]) -> None:
    message_type = message["type"]
    logger.info("IN  <- %s", message)

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
        if isinstance(payload, str) and payload.strip():
            try:
                chunk_size_bytes = len(base64.b64decode(payload, validate=True))
            except Exception:
                chunk_size_bytes = 0

        if chunk_size_bytes <= 0:
            chunk_size_bytes = int(message.get("size_bytes", 0) or 0)

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


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "protocol_version": "0.2",
        "available_agents": AVAILABLE_AGENTS,
        "auth_token_required": bool(DEVICE_AUTH_TOKEN),
    }
