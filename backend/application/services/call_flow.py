"""Call initiation flows for simplified device interaction."""

from __future__ import annotations

import asyncio
import base64
import time
from typing import Any

from backend.shared.protocol import UiState, build_message, new_turn_id

from backend.application.context import AppContext
from backend.application.services.message_bus import send, send_error, send_ui_state
from backend.application.services.session_init import ensure_not_busy
from backend.application.services.turn_processing import synthesize_text_to_audio
from backend.domain.session import DeviceSession


async def start_outbound_call(
    ctx: AppContext,
    session: DeviceSession,
    message: dict[str, Any],
) -> None:
    if not await ensure_not_busy(session):
        return

    if session.recording:
        await send_error(
            session,
            "Cannot start an outbound call while a hold-to-talk recording is active.",
            code="busy",
        )
        return

    session.turn_id = str(message.get("turn_id") or new_turn_id())
    session.interrupted.clear()
    session.turn_started_monotonic = time.monotonic()
    await send_ui_state(session, UiState.CALLING)

    if session.response_task and not session.response_task.done():
        session.response_task.cancel()

    session.response_task = asyncio.create_task(process_outbound_call(ctx, session))


async def process_outbound_call(ctx: AppContext, session: DeviceSession) -> None:
    turn_id = session.turn_id or new_turn_id()
    greeting = ctx.settings.outbound_call_greeting.strip()

    try:
        await send(
            session,
            build_message(
                "assistant.start",
                turn_id=turn_id,
                agent_id=session.active_agent,
                interaction="outbound_call",
            ),
        )

        if greeting:
            await send(
                session,
                build_message(
                    "assistant.text.partial",
                    turn_id=turn_id,
                    text=greeting,
                    accumulated=greeting,
                    interaction="outbound_call",
                ),
            )

        tts_used = await synthesize_text_to_audio(ctx, session, turn_id, greeting)
        if not tts_used and ctx.settings.enable_fake_audio and greeting:
            encoded = base64.b64encode(greeting.encode("utf-8")).decode("ascii")
            await send(
                session,
                build_message(
                    "assistant.audio.start",
                    turn_id=turn_id,
                    codec="pcm16",
                    sample_rate=16000,
                    channels=1,
                    source="fake",
                    interaction="outbound_call",
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
                    interaction="outbound_call",
                ),
            )
            await send(
                session,
                build_message(
                    "assistant.audio.end",
                    turn_id=turn_id,
                    source="fake",
                    total_chunks=1,
                    interaction="outbound_call",
                ),
            )

        await send(
            session,
            build_message(
                "assistant.text.final",
                turn_id=turn_id,
                text=greeting,
                interrupted=session.interrupted.is_set(),
                agent_id=session.active_agent,
                interaction="outbound_call",
            ),
        )
    except asyncio.CancelledError:
        await send(
            session,
            build_message(
                "assistant.text.final",
                turn_id=turn_id,
                text=greeting,
                interrupted=True,
                agent_id=session.active_agent,
                interaction="outbound_call",
            ),
        )
    finally:
        session.turn_id = None
        session.response_task = None
        session.interrupted.clear()
        session.turn_started_monotonic = None
        await send_ui_state(session, UiState.STANDBY)


async def send_incoming_call(
    session: DeviceSession,
    *,
    call_id: str | None = None,
    from_backend: str = "backend",
    title: str = "Llamada entrante",
) -> None:
    resolved_call_id = call_id or f"incoming-{new_turn_id()}"
    await send_ui_state(session, UiState.INCOMING_CALL)
    await send(
        session,
        build_message(
            "incoming_call",
            call_id=resolved_call_id,
            from_backend=from_backend,
            title=title,
        ),
    )
