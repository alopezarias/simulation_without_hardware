"""Recording lifecycle services."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from backend.shared.protocol import UiState, new_turn_id

from backend.application.context import AppContext
from backend.application.services.message_bus import send_error, send_ui_state
from backend.application.services.session_init import ensure_not_busy
from backend.domain.session import DeviceSession


async def start_recording(ctx: AppContext, session: DeviceSession, message: dict[str, Any]) -> None:
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
    ctx.audio_store.start_new_recording(session)
    await send_ui_state(session, UiState.LISTENING)


async def cancel_recording(ctx: AppContext, session: DeviceSession) -> None:
    session.recording = False
    session.turn_id = None
    session.text_fragments.clear()
    session.turn_started_monotonic = None
    session.recording_config.clear()
    session.audio_chunks_received = 0
    session.audio_bytes_received = 0
    ctx.audio_store.cleanup(session)
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
