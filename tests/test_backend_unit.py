"""Unit tests for backend.py as a safety net for future hexagonal refactor."""

from __future__ import annotations

import asyncio
import base64
import io
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import backend
from protocol import UiState


class FakeWebSocket:
    def __init__(self, incoming: list[Any] | None = None) -> None:
        self.accepted = False
        self.incoming = list(incoming or [])
        self.sent: list[dict[str, Any]] = []

    async def accept(self) -> None:
        self.accepted = True

    async def receive_json(self) -> Any:
        if self.incoming:
            item = self.incoming.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        raise backend.WebSocketDisconnect()

    async def send_json(self, message: dict[str, Any]) -> None:
        self.sent.append(message)


def make_session(websocket: FakeWebSocket | None = None) -> backend.DeviceSession:
    active_agent = backend.AVAILABLE_AGENTS[0] if backend.AVAILABLE_AGENTS else "assistant-general"
    return backend.DeviceSession(
        websocket=websocket or FakeWebSocket(),
        session_id="session-test",
        device_id="device-test",
        active_agent=active_agent,
    )


def create_temp_pcm(data: bytes = b"\x00\x01" * 600) -> str:
    fd, path = tempfile.mkstemp(prefix="test_pcm_", suffix=".pcm")
    os.close(fd)
    with open(path, "wb") as handle:
        handle.write(data)
    return path


def test_sanitize_for_log_masks_payload_and_trims_text() -> None:
    payload = "QUJDREVGR0g="
    text = "x" * 300
    message = {"type": "audio.chunk", "payload": payload, "text": text}
    safe = backend._sanitize_for_log(message)
    assert safe["payload"] == f"<base64:{len(payload)} chars>"
    assert safe["text"].endswith("...<trimmed>")
    assert len(safe["text"]) < len(text)
    assert message["payload"] == payload


def test_close_audio_file_closes_handle_and_clears_reference() -> None:
    session = make_session()
    handle = io.BytesIO()
    session.audio_file_handle = handle
    backend._close_audio_file(session)
    assert handle.closed
    assert session.audio_file_handle is None


def test_cleanup_audio_file_removes_file_and_resets_path() -> None:
    session = make_session()
    file_path = create_temp_pcm()
    session.audio_file_path = file_path
    session.audio_file_handle = open(file_path, "ab")
    backend._cleanup_audio_file(session)
    assert session.audio_file_path is None
    assert session.audio_file_handle is None
    assert not os.path.exists(file_path)


@pytest.mark.asyncio
async def test_send_forwards_message_to_websocket() -> None:
    websocket = FakeWebSocket()
    session = make_session(websocket)
    msg = {"type": "pong"}
    await backend.send(session, msg)
    assert websocket.sent == [msg]


@pytest.mark.asyncio
async def test_send_ui_state_updates_state_and_sends_message(monkeypatch: pytest.MonkeyPatch) -> None:
    session = make_session()
    send_mock = AsyncMock()
    monkeypatch.setattr(backend, "send", send_mock)
    await backend.send_ui_state(session, UiState.LISTENING)
    assert session.ui_state == UiState.LISTENING
    assert send_mock.call_count == 1
    sent = send_mock.call_args.args[1]
    assert sent["type"] == "ui.state"
    assert sent["state"] == "listening"


@pytest.mark.asyncio
async def test_send_error_sends_error_and_switches_ui_state(monkeypatch: pytest.MonkeyPatch) -> None:
    session = make_session()
    send_mock = AsyncMock()
    send_ui_state_mock = AsyncMock()
    monkeypatch.setattr(backend, "send", send_mock)
    monkeypatch.setattr(backend, "send_ui_state", send_ui_state_mock)
    await backend.send_error(session, "bad", code="x")
    assert send_mock.call_count == 1
    err = send_mock.call_args.args[1]
    assert err["type"] == "error"
    assert err["code"] == "x"
    assert err["detail"] == "bad"
    send_ui_state_mock.assert_awaited_once_with(session, UiState.ERROR)


@pytest.mark.asyncio
async def test_ensure_authenticated_returns_true_when_session_already_authenticated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = make_session()
    session.authenticated = True
    send_error_mock = AsyncMock()
    monkeypatch.setattr(backend, "send_error", send_error_mock)
    ok = await backend.ensure_authenticated(session)
    assert ok is True
    send_error_mock.assert_not_called()


@pytest.mark.asyncio
async def test_ensure_authenticated_returns_false_and_emits_unauthorized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = make_session()
    send_error_mock = AsyncMock()
    monkeypatch.setattr(backend, "send_error", send_error_mock)
    ok = await backend.ensure_authenticated(session)
    assert ok is False
    send_error_mock.assert_awaited_once()
    _, kwargs = send_error_mock.call_args
    assert kwargs["code"] == "unauthorized"


@pytest.mark.asyncio
async def test_ensure_not_busy_returns_false_for_running_response_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = make_session()
    send_error_mock = AsyncMock()
    monkeypatch.setattr(backend, "send_error", send_error_mock)

    async def long_task() -> None:
        await asyncio.sleep(60)

    task = asyncio.create_task(long_task())
    session.response_task = task
    try:
        ok = await backend.ensure_not_busy(session)
        assert ok is False
        send_error_mock.assert_awaited_once()
        _, kwargs = send_error_mock.call_args
        assert kwargs["code"] == "busy"
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


def test_validate_device_hello_accepts_valid_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(backend, "AVAILABLE_AGENTS", ["assistant-general", "assistant-tech"])
    monkeypatch.setattr(backend, "ALLOWED_DEVICE_IDS", set())
    monkeypatch.setattr(backend, "DEVICE_AUTH_TOKEN", "")
    device_id, active_agent = backend.validate_device_hello(
        {"type": "device.hello", "device_id": "dev-1", "active_agent": "assistant-tech"}
    )
    assert device_id == "dev-1"
    assert active_agent == "assistant-tech"


def test_validate_device_hello_rejects_empty_device_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(backend, "ALLOWED_DEVICE_IDS", set())
    monkeypatch.setattr(backend, "DEVICE_AUTH_TOKEN", "")
    with pytest.raises(ValueError, match="device_id cannot be empty"):
        backend.validate_device_hello({"type": "device.hello", "device_id": "   "})


def test_validate_device_hello_rejects_not_allowed_device(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(backend, "ALLOWED_DEVICE_IDS", {"allowed-1"})
    monkeypatch.setattr(backend, "DEVICE_AUTH_TOKEN", "")
    with pytest.raises(ValueError, match="is not allowed"):
        backend.validate_device_hello({"type": "device.hello", "device_id": "other"})


def test_validate_device_hello_rejects_unknown_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(backend, "AVAILABLE_AGENTS", ["assistant-general"])
    monkeypatch.setattr(backend, "ALLOWED_DEVICE_IDS", set())
    monkeypatch.setattr(backend, "DEVICE_AUTH_TOKEN", "")
    with pytest.raises(ValueError, match="is not valid"):
        backend.validate_device_hello(
            {"type": "device.hello", "device_id": "dev-1", "active_agent": "assistant-x"}
        )


def test_validate_device_hello_rejects_invalid_auth_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(backend, "AVAILABLE_AGENTS", ["assistant-general"])
    monkeypatch.setattr(backend, "ALLOWED_DEVICE_IDS", set())
    monkeypatch.setattr(backend, "DEVICE_AUTH_TOKEN", "secret")
    with pytest.raises(ValueError, match="Invalid auth token"):
        backend.validate_device_hello({"type": "device.hello", "device_id": "dev-1", "auth_token": "bad"})


@pytest.mark.asyncio
async def test_start_recording_configures_session_and_opens_pcm_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = make_session()
    ensure_not_busy_mock = AsyncMock(return_value=True)
    send_ui_state_mock = AsyncMock()
    monkeypatch.setattr(backend, "ensure_not_busy", ensure_not_busy_mock)
    monkeypatch.setattr(backend, "send_ui_state", send_ui_state_mock)
    await backend.start_recording(
        session,
        {"type": "recording.start", "turn_id": "turn-1", "sample_rate": 22050, "channels": 2},
    )
    assert session.recording is True
    assert session.turn_id == "turn-1"
    assert session.recording_config["sample_rate"] == 22050
    assert session.recording_config["channels"] == 2
    assert session.audio_file_path is not None
    assert session.audio_file_handle is not None
    send_ui_state_mock.assert_awaited_once_with(session, UiState.LISTENING)
    backend._cleanup_audio_file(session)


@pytest.mark.asyncio
async def test_start_recording_stops_if_session_is_busy(monkeypatch: pytest.MonkeyPatch) -> None:
    session = make_session()
    monkeypatch.setattr(backend, "ensure_not_busy", AsyncMock(return_value=False))
    send_ui_state_mock = AsyncMock()
    monkeypatch.setattr(backend, "send_ui_state", send_ui_state_mock)
    await backend.start_recording(session, {"type": "recording.start"})
    assert session.recording is False
    send_ui_state_mock.assert_not_called()


@pytest.mark.asyncio
async def test_start_recording_rejects_if_already_recording(monkeypatch: pytest.MonkeyPatch) -> None:
    session = make_session()
    session.recording = True
    monkeypatch.setattr(backend, "ensure_not_busy", AsyncMock(return_value=True))
    send_error_mock = AsyncMock()
    monkeypatch.setattr(backend, "send_error", send_error_mock)
    await backend.start_recording(session, {"type": "recording.start"})
    send_error_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_cancel_recording_resets_session_and_cleans_audio(monkeypatch: pytest.MonkeyPatch) -> None:
    session = make_session()
    pcm_path = create_temp_pcm()
    session.recording = True
    session.turn_id = "turn-1"
    session.text_fragments = ["hello"]
    session.turn_started_monotonic = time.monotonic()
    session.recording_config = {"sample_rate": 16000}
    session.audio_chunks_received = 3
    session.audio_bytes_received = 512
    session.audio_file_path = pcm_path
    session.audio_file_handle = open(pcm_path, "ab")
    send_ui_state_mock = AsyncMock()
    monkeypatch.setattr(backend, "send_ui_state", send_ui_state_mock)
    await backend.cancel_recording(session)
    assert session.recording is False
    assert session.turn_id is None
    assert session.text_fragments == []
    assert session.recording_config == {}
    assert session.audio_chunks_received == 0
    assert session.audio_bytes_received == 0
    assert session.audio_file_path is None
    assert not os.path.exists(pcm_path)
    send_ui_state_mock.assert_awaited_once_with(session, UiState.IDLE)


@pytest.mark.asyncio
async def test_interrupt_assistant_cancels_running_task_and_sets_idle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = make_session()

    async def long_task() -> None:
        await asyncio.sleep(60)

    session.response_task = asyncio.create_task(long_task())
    send_ui_state_mock = AsyncMock()
    monkeypatch.setattr(backend, "send_ui_state", send_ui_state_mock)
    await backend.interrupt_assistant(session)
    assert session.interrupted.is_set()
    assert session.response_task.cancelled()
    send_ui_state_mock.assert_awaited_once_with(session, UiState.IDLE)


@pytest.mark.asyncio
async def test_stream_pcm_audio_file_streams_start_chunk_and_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = make_session()
    pcm_path = create_temp_pcm(b"\x00\x01" * 900)
    send_mock = AsyncMock()
    sleep_mock = AsyncMock(return_value=None)
    monkeypatch.setattr(backend, "send", send_mock)
    monkeypatch.setattr(backend.asyncio, "sleep", sleep_mock)
    chunks = await backend.stream_pcm_audio_file(
        session,
        turn_id="turn-1",
        pcm_path=pcm_path,
        sample_rate=16000,
        channels=1,
        source="tts",
    )
    messages = [call.args[1] for call in send_mock.call_args_list]
    assert messages[0]["type"] == "assistant.audio.start"
    assert messages[1]["type"] == "assistant.audio.chunk"
    assert messages[-1]["type"] == "assistant.audio.end"
    assert chunks >= 1
    os.remove(pcm_path)


@pytest.mark.asyncio
async def test_stream_pcm_audio_file_converts_socket_close_to_cancelled_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = make_session()
    pcm_path = create_temp_pcm()

    async def fail_send(_session: backend.DeviceSession, _message: dict[str, Any]) -> None:
        raise RuntimeError("websocket.close")

    monkeypatch.setattr(backend, "send", fail_send)
    with pytest.raises(asyncio.CancelledError):
        await backend.stream_pcm_audio_file(
            session,
            turn_id="turn-1",
            pcm_path=pcm_path,
            sample_rate=16000,
            channels=1,
            source="tts",
        )
    assert session.interrupted.is_set()
    os.remove(pcm_path)


@pytest.mark.asyncio
async def test_stream_loopback_audio_respects_guards(monkeypatch: pytest.MonkeyPatch) -> None:
    session = make_session()
    monkeypatch.setattr(backend, "LOOPBACK_AUDIO_ENABLED", False)
    assert await backend.stream_loopback_audio(session, "turn-1") is False


@pytest.mark.asyncio
async def test_stream_loopback_audio_streams_when_audio_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    session = make_session()
    session.audio_file_path = create_temp_pcm()
    session.audio_bytes_received = 1000
    session.recording_config = {"sample_rate": 24000, "channels": 1}
    monkeypatch.setattr(backend, "LOOPBACK_AUDIO_ENABLED", True)
    stream_mock = AsyncMock(return_value=1)
    monkeypatch.setattr(backend, "stream_pcm_audio_file", stream_mock)
    ok = await backend.stream_loopback_audio(session, "turn-1")
    assert ok is True
    stream_mock.assert_awaited_once()
    os.remove(session.audio_file_path)


@pytest.mark.asyncio
async def test_transcribe_recording_returns_empty_when_stt_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = make_session()
    session.audio_file_path = create_temp_pcm()
    session.audio_bytes_received = 128
    fake_pipeline = Mock(stt_available=False)
    monkeypatch.setattr(backend, "speech_pipeline", fake_pipeline)
    text = await backend.transcribe_recording(session)
    assert text == ""
    os.remove(session.audio_file_path)


@pytest.mark.asyncio
async def test_transcribe_recording_returns_text_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    session = make_session()
    session.audio_file_path = create_temp_pcm()
    session.audio_bytes_received = 128
    session.recording_config = {"sample_rate": 16000, "channels": 1}
    fake_pipeline = Mock(stt_available=True)
    fake_pipeline.transcribe_pcm_file = Mock(return_value="hola mundo")
    monkeypatch.setattr(backend, "speech_pipeline", fake_pipeline)
    text = await backend.transcribe_recording(session)
    assert text == "hola mundo"
    os.remove(session.audio_file_path)


@pytest.mark.asyncio
async def test_transcribe_recording_returns_empty_when_pipeline_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = make_session()
    session.audio_file_path = create_temp_pcm()
    session.audio_bytes_received = 128
    fake_pipeline = Mock(stt_available=True)
    fake_pipeline.transcribe_pcm_file = Mock(side_effect=RuntimeError("boom"))
    monkeypatch.setattr(backend, "speech_pipeline", fake_pipeline)
    text = await backend.transcribe_recording(session)
    assert text == ""
    os.remove(session.audio_file_path)


@pytest.mark.asyncio
async def test_synthesize_text_to_audio_returns_false_for_empty_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = make_session()
    fake_pipeline = Mock(tts_available=True)
    monkeypatch.setattr(backend, "speech_pipeline", fake_pipeline)
    ok = await backend.synthesize_text_to_audio(session, "turn-1", "   ")
    assert ok is False


@pytest.mark.asyncio
async def test_synthesize_text_to_audio_returns_false_when_tts_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = make_session()
    fake_pipeline = Mock(tts_available=False)
    monkeypatch.setattr(backend, "speech_pipeline", fake_pipeline)
    ok = await backend.synthesize_text_to_audio(session, "turn-1", "hola")
    assert ok is False


@pytest.mark.asyncio
async def test_synthesize_text_to_audio_streams_generated_pcm_and_cleans_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = make_session()
    session.recording_config = {"sample_rate": 22050, "channels": 1}
    temp_pcm = create_temp_pcm(b"\x01\x02" * 200)
    fake_pipeline = Mock(tts_available=True)
    fake_pipeline.synthesize_text_to_pcm_file = Mock(return_value=(temp_pcm, os.path.getsize(temp_pcm)))
    stream_mock = AsyncMock(return_value=1)
    monkeypatch.setattr(backend, "speech_pipeline", fake_pipeline)
    monkeypatch.setattr(backend, "stream_pcm_audio_file", stream_mock)
    ok = await backend.synthesize_text_to_audio(session, "turn-1", "hola mundo")
    assert ok is True
    stream_mock.assert_awaited_once()
    assert not os.path.exists(temp_pcm)


@pytest.mark.asyncio
async def test_synthesize_text_to_audio_returns_false_if_tts_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = make_session()
    fake_pipeline = Mock(tts_available=True)
    fake_pipeline.synthesize_text_to_pcm_file = Mock(side_effect=RuntimeError("tts failed"))
    monkeypatch.setattr(backend, "speech_pipeline", fake_pipeline)
    ok = await backend.synthesize_text_to_audio(session, "turn-1", "hola")
    assert ok is False


@pytest.mark.asyncio
async def test_synthesize_text_to_audio_propagates_cancelled_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = make_session()
    temp_pcm = create_temp_pcm()
    fake_pipeline = Mock(tts_available=True)
    fake_pipeline.synthesize_text_to_pcm_file = Mock(return_value=(temp_pcm, os.path.getsize(temp_pcm)))
    stream_mock = AsyncMock(side_effect=asyncio.CancelledError())
    monkeypatch.setattr(backend, "speech_pipeline", fake_pipeline)
    monkeypatch.setattr(backend, "stream_pcm_audio_file", stream_mock)
    with pytest.raises(asyncio.CancelledError):
        await backend.synthesize_text_to_audio(session, "turn-1", "hola")
    assert not os.path.exists(temp_pcm)


@pytest.mark.asyncio
async def test_process_turn_echo_mode_emits_expected_messages(monkeypatch: pytest.MonkeyPatch) -> None:
    session = make_session()
    session.turn_id = "turn-1"
    session.text_fragments = ["hola"]
    session.turn_started_monotonic = time.monotonic() - 0.2
    send_mock = AsyncMock()
    send_ui_state_mock = AsyncMock()
    synthesize_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(backend, "AUDIO_REPLY_MODE", "echo")
    monkeypatch.setattr(backend, "send", send_mock)
    monkeypatch.setattr(backend, "send_ui_state", send_ui_state_mock)
    monkeypatch.setattr(backend, "synthesize_text_to_audio", synthesize_mock)
    await backend.process_turn(session)
    messages = [call.args[1] for call in send_mock.call_args_list]
    assert messages[0]["type"] == "transcript.final"
    assert messages[0]["text"] == "hola"
    assert any(msg["type"] == "assistant.text.partial" for msg in messages)
    final = next(msg for msg in messages if msg["type"] == "assistant.text.final")
    assert final["text"] == "hola"
    assert final["interrupted"] is False
    assert session.turn_id is None
    assert session.recording is False
    assert session.audio_bytes_received == 0
    assert send_ui_state_mock.await_args_list[0].args[1] == UiState.SPEAKING
    assert send_ui_state_mock.await_args_list[-1].args[1] == UiState.IDLE


@pytest.mark.asyncio
async def test_process_turn_assistant_mode_uses_adapter_stream_and_loopback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = make_session()
    session.turn_id = "turn-1"
    session.text_fragments = ["texto"]
    session.audio_bytes_received = 1024
    session.audio_chunks_received = 2
    send_mock = AsyncMock()
    send_ui_state_mock = AsyncMock()
    transcribe_mock = AsyncMock(return_value="audio")
    synthesize_mock = AsyncMock(return_value=False)
    loopback_mock = AsyncMock(return_value=True)

    class AdapterStub:
        async def stream_response(self, **_kwargs: Any):
            for chunk in ("respuesta ", "final"):
                yield chunk

    monkeypatch.setattr(backend, "AUDIO_REPLY_MODE", "assistant")
    monkeypatch.setattr(backend, "send", send_mock)
    monkeypatch.setattr(backend, "send_ui_state", send_ui_state_mock)
    monkeypatch.setattr(backend, "transcribe_recording", transcribe_mock)
    monkeypatch.setattr(backend, "synthesize_text_to_audio", synthesize_mock)
    monkeypatch.setattr(backend, "stream_loopback_audio", loopback_mock)
    monkeypatch.setattr(backend, "adapter", AdapterStub())
    await backend.process_turn(session)
    messages = [call.args[1] for call in send_mock.call_args_list]
    transcript = next(msg for msg in messages if msg["type"] == "transcript.final")
    assert transcript["text"] == "texto audio"
    final = next(msg for msg in messages if msg["type"] == "assistant.text.final")
    assert final["text"] == "respuesta final"
    loopback_mock.assert_awaited_once()
    assert send_ui_state_mock.await_args_list[-1].args[1] == UiState.IDLE


@pytest.mark.asyncio
async def test_process_turn_handles_cancelled_generation(monkeypatch: pytest.MonkeyPatch) -> None:
    session = make_session()
    session.turn_id = "turn-1"
    session.text_fragments = ["hola"]
    send_mock = AsyncMock()
    send_ui_state_mock = AsyncMock()

    async def interrupting_tts(_session: backend.DeviceSession, _turn: str, _text: str) -> bool:
        session.interrupted.set()
        return False

    monkeypatch.setattr(backend, "AUDIO_REPLY_MODE", "echo")
    monkeypatch.setattr(backend, "send", send_mock)
    monkeypatch.setattr(backend, "send_ui_state", send_ui_state_mock)
    monkeypatch.setattr(backend, "synthesize_text_to_audio", interrupting_tts)
    await backend.process_turn(session)
    messages = [call.args[1] for call in send_mock.call_args_list]
    final = [msg for msg in messages if msg["type"] == "assistant.text.final"][-1]
    assert final["interrupted"] is True
    assert send_ui_state_mock.await_args_list[-1].args[1] == UiState.IDLE


@pytest.mark.asyncio
async def test_send_session_ready_emits_protocol_and_capabilities(monkeypatch: pytest.MonkeyPatch) -> None:
    session = make_session()
    send_mock = AsyncMock()
    fake_pipeline = Mock()
    fake_pipeline.capabilities.return_value = {"stt_available": True}
    monkeypatch.setattr(backend, "send", send_mock)
    monkeypatch.setattr(backend, "speech_pipeline", fake_pipeline)
    monkeypatch.setattr(backend, "AUDIO_REPLY_MODE", "assistant")
    await backend.send_session_ready(session)
    msg = send_mock.call_args.args[1]
    assert msg["type"] == "session.ready"
    assert msg["session_id"] == session.session_id
    assert msg["audio_reply_mode"] == "assistant"
    assert msg["speech"] == {"stt_available": True}


@pytest.mark.asyncio
async def test_handle_message_device_hello_authenticates_and_sends_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = make_session()
    send_session_ready_mock = AsyncMock()
    send_ui_state_mock = AsyncMock()
    monkeypatch.setattr(backend, "send_session_ready", send_session_ready_mock)
    monkeypatch.setattr(backend, "send_ui_state", send_ui_state_mock)
    await backend.handle_message(session, {"type": "device.hello", "device_id": "dev-1"})
    assert session.authenticated is True
    assert session.device_id == "dev-1"
    send_session_ready_mock.assert_awaited_once()
    send_ui_state_mock.assert_awaited_once_with(session, UiState.IDLE)


@pytest.mark.asyncio
async def test_handle_message_device_hello_sends_auth_error_on_validation_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = make_session()
    monkeypatch.setattr(backend, "validate_device_hello", Mock(side_effect=ValueError("bad")))
    send_error_mock = AsyncMock()
    monkeypatch.setattr(backend, "send_error", send_error_mock)
    await backend.handle_message(session, {"type": "device.hello", "device_id": "x"})
    send_error_mock.assert_awaited_once()
    _, kwargs = send_error_mock.call_args
    assert kwargs["code"] == "auth_error"


@pytest.mark.asyncio
async def test_handle_message_stops_when_not_authenticated(monkeypatch: pytest.MonkeyPatch) -> None:
    session = make_session()
    ensure_auth_mock = AsyncMock(return_value=False)
    monkeypatch.setattr(backend, "ensure_authenticated", ensure_auth_mock)
    send_mock = AsyncMock()
    monkeypatch.setattr(backend, "send", send_mock)
    await backend.handle_message(session, {"type": "session.start"})
    ensure_auth_mock.assert_awaited_once()
    send_mock.assert_not_called()


@pytest.mark.asyncio
async def test_handle_message_session_start_replays_session_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    session = make_session()
    session.authenticated = True
    send_session_ready_mock = AsyncMock()
    monkeypatch.setattr(backend, "send_session_ready", send_session_ready_mock)
    await backend.handle_message(session, {"type": "session.start"})
    send_session_ready_mock.assert_awaited_once_with(session)


@pytest.mark.asyncio
async def test_handle_message_agent_select_rejects_unknown_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    session = make_session()
    session.authenticated = True
    monkeypatch.setattr(backend, "AVAILABLE_AGENTS", ["assistant-general"])
    send_error_mock = AsyncMock()
    monkeypatch.setattr(backend, "send_error", send_error_mock)
    await backend.handle_message(session, {"type": "agent.select", "agent_id": "unknown"})
    send_error_mock.assert_awaited_once()
    _, kwargs = send_error_mock.call_args
    assert kwargs["code"] == "invalid_agent"


@pytest.mark.asyncio
async def test_handle_message_agent_select_updates_session(monkeypatch: pytest.MonkeyPatch) -> None:
    session = make_session()
    session.authenticated = True
    monkeypatch.setattr(backend, "AVAILABLE_AGENTS", ["assistant-general", "assistant-tech"])
    send_mock = AsyncMock()
    send_ui_state_mock = AsyncMock()
    monkeypatch.setattr(backend, "send", send_mock)
    monkeypatch.setattr(backend, "send_ui_state", send_ui_state_mock)
    await backend.handle_message(session, {"type": "agent.select", "agent_id": "assistant-tech"})
    assert session.active_agent == "assistant-tech"
    sent = send_mock.call_args.args[1]
    assert sent["type"] == "agent.selected"
    send_ui_state_mock.assert_awaited_once_with(session, UiState.IDLE)


@pytest.mark.asyncio
async def test_handle_message_recording_start_delegates_to_start_recording(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = make_session()
    session.authenticated = True
    start_mock = AsyncMock()
    monkeypatch.setattr(backend, "start_recording", start_mock)
    message = {"type": "recording.start", "turn_id": "turn-1"}
    await backend.handle_message(session, message)
    start_mock.assert_awaited_once_with(session, message)


@pytest.mark.asyncio
async def test_handle_message_audio_chunk_decodes_payload_and_emits_partial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = make_session()
    session.authenticated = True
    buffer = io.BytesIO()

    async def fake_start_recording(s: backend.DeviceSession, _msg: dict[str, Any]) -> None:
        s.recording = True
        s.turn_id = "turn-1"
        s.audio_file_handle = buffer

    start_mock = AsyncMock(side_effect=fake_start_recording)
    send_mock = AsyncMock()
    monkeypatch.setattr(backend, "start_recording", start_mock)
    monkeypatch.setattr(backend, "send", send_mock)
    payload = base64.b64encode(b"abcd").decode("ascii")
    await backend.handle_message(
        session,
        {
            "type": "audio.chunk",
            "seq": 0,
            "duration_ms": 30,
            "payload": payload,
            "text_hint": "hola",
        },
    )
    start_mock.assert_awaited_once()
    assert session.audio_chunks_received == 1
    assert session.audio_bytes_received == 4
    assert session.text_fragments == ["hola"]
    assert buffer.getvalue() == b"abcd"
    partial = send_mock.call_args.args[1]
    assert partial["type"] == "transcript.partial"


@pytest.mark.asyncio
async def test_handle_message_audio_chunk_falls_back_to_size_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = make_session()
    session.authenticated = True
    session.recording = True
    session.turn_id = "turn-1"
    session.audio_file_handle = io.BytesIO()
    await backend.handle_message(
        session,
        {"type": "audio.chunk", "payload": "not-base64", "size_bytes": 321},
    )
    assert session.audio_chunks_received == 1
    assert session.audio_bytes_received == 321


@pytest.mark.asyncio
async def test_handle_message_debug_user_text_requires_non_empty_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = make_session()
    session.authenticated = True
    send_error_mock = AsyncMock()
    monkeypatch.setattr(backend, "send_error", send_error_mock)
    await backend.handle_message(session, {"type": "debug.user_text", "text": "   "})
    send_error_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_handle_message_debug_user_text_starts_recording_and_emits_partial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = make_session()
    session.authenticated = True

    async def fake_start_recording(s: backend.DeviceSession, _msg: dict[str, Any]) -> None:
        s.recording = True
        s.turn_id = "turn-1"

    start_mock = AsyncMock(side_effect=fake_start_recording)
    send_mock = AsyncMock()
    monkeypatch.setattr(backend, "start_recording", start_mock)
    monkeypatch.setattr(backend, "send", send_mock)
    await backend.handle_message(session, {"type": "debug.user_text", "text": "hola"})
    assert session.text_fragments == ["hola"]
    start_mock.assert_awaited_once()
    partial = send_mock.call_args.args[1]
    assert partial["type"] == "transcript.partial"


@pytest.mark.asyncio
async def test_handle_message_recording_stop_requires_listening(monkeypatch: pytest.MonkeyPatch) -> None:
    session = make_session()
    session.authenticated = True
    send_error_mock = AsyncMock()
    monkeypatch.setattr(backend, "send_error", send_error_mock)
    await backend.handle_message(session, {"type": "recording.stop"})
    send_error_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_handle_message_recording_stop_sets_processing_and_spawns_turn_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = make_session()
    session.authenticated = True
    session.recording = True
    session.audio_file_handle = io.BytesIO()
    send_ui_state_mock = AsyncMock()
    process_turn_mock = AsyncMock(return_value=None)
    monkeypatch.setattr(backend, "send_ui_state", send_ui_state_mock)
    monkeypatch.setattr(backend, "process_turn", process_turn_mock)
    await backend.handle_message(session, {"type": "recording.stop"})
    assert session.recording is False
    send_ui_state_mock.assert_awaited_once_with(session, UiState.PROCESSING)
    assert session.response_task is not None
    await session.response_task


@pytest.mark.asyncio
async def test_handle_message_recording_cancel_delegates(monkeypatch: pytest.MonkeyPatch) -> None:
    session = make_session()
    session.authenticated = True
    cancel_mock = AsyncMock()
    monkeypatch.setattr(backend, "cancel_recording", cancel_mock)
    await backend.handle_message(session, {"type": "recording.cancel"})
    cancel_mock.assert_awaited_once_with(session)


@pytest.mark.asyncio
async def test_handle_message_assistant_interrupt_delegates(monkeypatch: pytest.MonkeyPatch) -> None:
    session = make_session()
    session.authenticated = True
    interrupt_mock = AsyncMock()
    monkeypatch.setattr(backend, "interrupt_assistant", interrupt_mock)
    await backend.handle_message(session, {"type": "assistant.interrupt"})
    interrupt_mock.assert_awaited_once_with(session)


@pytest.mark.asyncio
async def test_handle_message_ping_sends_pong(monkeypatch: pytest.MonkeyPatch) -> None:
    session = make_session()
    session.authenticated = True
    send_mock = AsyncMock()
    monkeypatch.setattr(backend, "send", send_mock)
    await backend.handle_message(session, {"type": "ping"})
    pong = send_mock.call_args.args[1]
    assert pong["type"] == "pong"


@pytest.mark.asyncio
async def test_handle_message_unknown_type_sends_error(monkeypatch: pytest.MonkeyPatch) -> None:
    session = make_session()
    session.authenticated = True
    send_error_mock = AsyncMock()
    monkeypatch.setattr(backend, "send_error", send_error_mock)
    await backend.handle_message(session, {"type": "unknown"})
    send_error_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_websocket_endpoint_accepts_and_dispatches_messages(monkeypatch: pytest.MonkeyPatch) -> None:
    websocket = FakeWebSocket(incoming=[{"type": "ping"}])
    handle_mock = AsyncMock()
    monkeypatch.setattr(backend, "validate_device_message", Mock(side_effect=lambda raw: raw))
    monkeypatch.setattr(backend, "handle_message", handle_mock)
    await backend.websocket_endpoint(websocket)  # type: ignore[arg-type]
    assert websocket.accepted is True
    assert handle_mock.await_count == 1
    assert handle_mock.await_args.args[1]["type"] == "ping"


@pytest.mark.asyncio
async def test_websocket_endpoint_sends_bad_message_error(monkeypatch: pytest.MonkeyPatch) -> None:
    websocket = FakeWebSocket(incoming=[{"not": "valid"}])
    validate_mock = Mock(side_effect=ValueError("bad message"))
    send_error_mock = AsyncMock()
    monkeypatch.setattr(backend, "validate_device_message", validate_mock)
    monkeypatch.setattr(backend, "send_error", send_error_mock)
    await backend.websocket_endpoint(websocket)  # type: ignore[arg-type]
    send_error_mock.assert_awaited_once()
    _, kwargs = send_error_mock.call_args
    assert kwargs["code"] == "bad_message"


@pytest.mark.asyncio
async def test_health_reports_current_runtime_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_pipeline = Mock()
    fake_pipeline.capabilities.return_value = {"stt_available": True, "tts_available": False}
    monkeypatch.setattr(backend, "speech_pipeline", fake_pipeline)
    monkeypatch.setattr(backend, "AVAILABLE_AGENTS", ["assistant-general"])
    monkeypatch.setattr(backend, "AUDIO_REPLY_MODE", "assistant")
    monkeypatch.setattr(backend, "DEVICE_AUTH_TOKEN", "x")
    status = await backend.health()
    assert status["status"] == "ok"
    assert status["protocol_version"] == "0.2"
    assert status["available_agents"] == ["assistant-general"]
    assert status["audio_reply_mode"] == "assistant"
    assert status["auth_token_required"] is True
    assert status["speech"] == {"stt_available": True, "tts_available": False}
