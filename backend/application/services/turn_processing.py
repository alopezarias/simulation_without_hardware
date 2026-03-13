"""Turn processing services: STT, assistant response, TTS and audio streaming."""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import time

from backend.shared.protocol import UiState, build_message, new_turn_id

from backend.application.context import AppContext
from backend.application.services.message_bus import send, send_error, send_ui_state
from backend.domain.session import DeviceSession

logger = logging.getLogger("simulation-backend")


async def stream_pcm_audio_file(
    ctx: AppContext,
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
    chunk_size = max(256, int(bytes_per_second * ctx.settings.loopback_chunk_ms / 1000))
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
            pass

    return seq


async def stream_loopback_audio(ctx: AppContext, session: DeviceSession, turn_id: str) -> bool:
    if not ctx.settings.loopback_audio_enabled:
        return False
    if not session.audio_file_path:
        return False
    if session.audio_bytes_received <= 0:
        return False

    sample_rate = int(session.recording_config.get("sample_rate", 16000) or 16000)
    channels = int(session.recording_config.get("channels", 1) or 1)
    await stream_pcm_audio_file(
        ctx,
        session,
        turn_id,
        session.audio_file_path,
        sample_rate=sample_rate,
        channels=channels,
        source="device_audio",
        loopback=True,
    )
    return True


async def transcribe_recording(ctx: AppContext, session: DeviceSession) -> str:
    if not session.audio_file_path:
        return ""
    if session.audio_bytes_received <= 0:
        return ""
    if not ctx.speech.stt_available:
        return ""

    sample_rate = int(session.recording_config.get("sample_rate", 16000) or 16000)
    channels = int(session.recording_config.get("channels", 1) or 1)

    try:
        text = await asyncio.to_thread(
            ctx.speech.transcribe_pcm_file,
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


async def synthesize_text_to_audio(
    ctx: AppContext,
    session: DeviceSession,
    turn_id: str,
    text: str,
) -> bool:
    cleaned = " ".join(text.split()).strip()
    if not cleaned:
        return False
    if not ctx.speech.tts_available:
        return False

    sample_rate = int(session.recording_config.get("sample_rate", 16000) or 16000)
    channels = int(session.recording_config.get("channels", 1) or 1)
    pcm_path: str | None = None
    total_bytes = 0
    try:
        pcm_path, total_bytes = await asyncio.to_thread(
            ctx.speech.synthesize_text_to_pcm_file,
            cleaned,
            sample_rate,
            channels,
        )
        if total_bytes <= 0:
            return False

        await stream_pcm_audio_file(
            ctx,
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


async def process_turn(ctx: AppContext, session: DeviceSession) -> None:
    turn_id = session.turn_id or new_turn_id()
    typed_text = " ".join(fragment.strip() for fragment in session.text_fragments).strip()
    session.text_fragments.clear()

    transcribed_text = ""
    if session.audio_bytes_received > 0:
        transcribed_text = await transcribe_recording(ctx, session)

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
    has_audio_input = session.audio_bytes_received > 0
    # For microphone-driven turns, always answer with the recognized transcript.
    # This preserves the expected hardware flow: audio -> STT -> same text -> TTS.
    force_transcript_echo = has_audio_input and not typed_text

    try:
        if force_transcript_echo:
            final_text = transcribed_text or user_text
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
        elif ctx.settings.audio_reply_mode == "echo":
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
            async for chunk in ctx.assistant.stream_response(
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

        tts_used = await synthesize_text_to_audio(ctx, session, turn_id, final_text)
        if session.interrupted.is_set():
            raise asyncio.CancelledError()

        if not tts_used and ctx.settings.enable_fake_audio:
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
            and ctx.settings.loopback_audio_enabled
        ):
            loopback_used = await stream_loopback_audio(ctx, session, turn_id)

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
        ctx.audio_store.cleanup(session)
        await send_ui_state(session, UiState.IDLE)
