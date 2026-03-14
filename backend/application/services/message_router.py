"""Message routing for device protocol events."""

from __future__ import annotations

import asyncio
import base64
import logging
from typing import Any

from backend.shared.protocol import UiState, build_message, require_fields

from backend.application.context import AppContext
from backend.application.services.message_bus import send, send_error, send_ui_state
from backend.application.services.recording import cancel_recording, interrupt_assistant, start_recording
from backend.application.services.session_init import complete_hello, ensure_authenticated, send_session_ready
from backend.application.services.turn_processing import process_turn
from backend.domain.session import DeviceSession
from backend.infrastructure.logging.sanitizer import sanitize_message_for_log

logger = logging.getLogger("simulation-backend")


async def handle_message(ctx: AppContext, session: DeviceSession, message: dict[str, Any]) -> None:
    message_type = message["type"]
    logger.info("IN  <- %s", sanitize_message_for_log(message))

    if message_type == "device.hello":
        await complete_hello(ctx, session, message)
        return

    if not await ensure_authenticated(session):
        return

    if message_type == "session.start":
        await send_session_ready(ctx, session)
        return

    if message_type == "agents.version.request":
        await send(
            session,
            build_message(
                "agents.version.response",
                version=ctx.settings.agent_catalog_version,
                active_agent=session.active_agent,
            ),
        )
        return

    if message_type == "agents.list.request":
        await send(
            session,
            build_message(
                "agents.list.response",
                version=ctx.settings.agent_catalog_version,
                active_agent=session.active_agent,
                agents=ctx.settings.available_agents,
            ),
        )
        return

    if message_type == "agent.select":
        require_fields(message, "agent_id")
        requested = str(message["agent_id"]).strip()
        if requested not in ctx.settings.available_agents:
            await send_error(
                session,
                f"Unknown agent '{requested}'. Available agents: {', '.join(ctx.settings.available_agents)}",
                code="invalid_agent",
            )
            return

        session.active_agent = requested
        await send(session, build_message("agent.selected", agent_id=session.active_agent))
        await send_ui_state(session, UiState.IDLE)
        return

    if message_type == "recording.start":
        await start_recording(ctx, session, message)
        return

    if message_type == "audio.chunk":
        if not session.recording:
            await start_recording(ctx, session, message)

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

        if decoded_chunk:
            ctx.audio_store.append_chunk(session, decoded_chunk)

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
            await start_recording(ctx, session, message)

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

        ctx.audio_store.close(session)
        session.recording = False
        await send_ui_state(session, UiState.PROCESSING)

        if session.response_task and not session.response_task.done():
            session.response_task.cancel()

        session.response_task = asyncio.create_task(process_turn(ctx, session))
        return

    if message_type == "recording.cancel":
        await cancel_recording(ctx, session)
        return

    if message_type == "assistant.interrupt":
        await interrupt_assistant(session)
        return

    if message_type == "ping":
        await send(session, build_message("pong"))
        return

    await send_error(session, f"Unknown message type: {message_type}")
