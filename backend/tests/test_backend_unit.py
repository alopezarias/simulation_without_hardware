"""Unit tests for backend facade and delegated hexagonal services."""

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

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend import api as backend
from backend.shared.protocol import UiState


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


def test_device_session_defaults_to_first_available_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(backend, "AVAILABLE_AGENTS", ["assistant-tech", "assistant-general"])
    session = backend.DeviceSession(websocket=FakeWebSocket())
    assert session.active_agent == "assistant-tech"


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
async def test_send_ui_state_updates_state_and_emits_ui_message() -> None:
    websocket = FakeWebSocket()
    session = make_session(websocket)
    await backend.send_ui_state(session, UiState.LISTENING)
    assert session.ui_state == UiState.LISTENING
    assert websocket.sent[-1]["type"] == "ui.state"
    assert websocket.sent[-1]["state"] == "listening"


@pytest.mark.asyncio
async def test_send_error_emits_error_then_error_ui_state() -> None:
    websocket = FakeWebSocket()
    session = make_session(websocket)
    await backend.send_error(session, "bad", code="x")
    assert websocket.sent[0]["type"] == "error"
    assert websocket.sent[0]["code"] == "x"
    assert websocket.sent[1]["type"] == "ui.state"
    assert websocket.sent[1]["state"] == "error"


@pytest.mark.asyncio
async def test_ensure_authenticated_true_when_authenticated() -> None:
    session = make_session()
    session.authenticated = True
    assert await backend.ensure_authenticated(session) is True


@pytest.mark.asyncio
async def test_ensure_authenticated_false_and_emits_error() -> None:
    websocket = FakeWebSocket()
    session = make_session(websocket)
    assert await backend.ensure_authenticated(session) is False
    assert websocket.sent[0]["type"] == "error"
    assert websocket.sent[0]["code"] == "unauthorized"


@pytest.mark.asyncio
async def test_ensure_not_busy_false_for_active_response_task() -> None:
    websocket = FakeWebSocket()
    session = make_session(websocket)

    async def long_task() -> None:
        await asyncio.sleep(60)

    task = asyncio.create_task(long_task())
    session.response_task = task
    try:
        assert await backend.ensure_not_busy(session) is False
        assert websocket.sent[0]["type"] == "error"
        assert websocket.sent[0]["code"] == "busy"
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


def test_validate_device_hello_rejects_invalid_auth_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(backend, "AVAILABLE_AGENTS", ["assistant-general"])
    monkeypatch.setattr(backend, "ALLOWED_DEVICE_IDS", set())
    monkeypatch.setattr(backend, "DEVICE_AUTH_TOKEN", "secret")
    with pytest.raises(ValueError, match="Invalid auth token"):
        backend.validate_device_hello({"type": "device.hello", "device_id": "dev-1", "auth_token": "bad"})


def test_validate_device_hello_rejects_unknown_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(backend, "AVAILABLE_AGENTS", ["assistant-general"])
    monkeypatch.setattr(backend, "ALLOWED_DEVICE_IDS", set())
    monkeypatch.setattr(backend, "DEVICE_AUTH_TOKEN", "")
    with pytest.raises(ValueError, match="is not valid"):
        backend.validate_device_hello(
            {"type": "device.hello", "device_id": "dev-1", "active_agent": "assistant-x"}
        )


def test_sync_runtime_forces_supported_audio_reply_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(backend, "AUDIO_REPLY_MODE", "invalid-mode")
    backend._sync_runtime_from_legacy_globals()
    assert backend.AUDIO_REPLY_MODE == "assistant"


def test_backend_settings_exposes_stable_agent_catalog_version() -> None:
    version = backend._container.settings.agent_catalog_version
    assert isinstance(version, str)
    assert len(version) == 12


@pytest.mark.asyncio
async def test_start_recording_configures_session_and_opens_pcm_file() -> None:
    websocket = FakeWebSocket()
    session = make_session(websocket)
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
    assert websocket.sent[-1]["type"] == "ui.state"
    assert websocket.sent[-1]["state"] == "listening"
    backend._cleanup_audio_file(session)


@pytest.mark.asyncio
async def test_start_recording_rejects_when_busy() -> None:
    websocket = FakeWebSocket()
    session = make_session(websocket)

    async def long_task() -> None:
        await asyncio.sleep(60)

    task = asyncio.create_task(long_task())
    session.response_task = task
    try:
        await backend.start_recording(session, {"type": "recording.start"})
        assert session.recording is False
        assert websocket.sent[0]["code"] == "busy"
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


@pytest.mark.asyncio
async def test_start_recording_rejects_if_already_recording() -> None:
    websocket = FakeWebSocket()
    session = make_session(websocket)
    session.recording = True
    await backend.start_recording(session, {"type": "recording.start"})
    assert websocket.sent[0]["type"] == "error"


@pytest.mark.asyncio
async def test_cancel_recording_resets_session_and_cleans_audio() -> None:
    websocket = FakeWebSocket()
    session = make_session(websocket)
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

    await backend.cancel_recording(session)
    assert session.recording is False
    assert session.turn_id is None
    assert session.recording_config == {}
    assert session.audio_chunks_received == 0
    assert session.audio_bytes_received == 0
    assert session.audio_file_path is None
    assert not os.path.exists(pcm_path)
    assert websocket.sent[-1]["type"] == "ui.state"
    assert websocket.sent[-1]["state"] == "idle"


@pytest.mark.asyncio
async def test_interrupt_assistant_cancels_running_task_and_sets_idle() -> None:
    websocket = FakeWebSocket()
    session = make_session(websocket)

    async def long_task() -> None:
        await asyncio.sleep(60)

    session.response_task = asyncio.create_task(long_task())
    await backend.interrupt_assistant(session)
    assert session.interrupted.is_set()
    assert session.response_task.cancelled()
    assert websocket.sent[-1]["type"] == "ui.state"
    assert websocket.sent[-1]["state"] == "idle"


@pytest.mark.asyncio
async def test_stream_pcm_audio_file_streams_start_chunk_and_end(monkeypatch: pytest.MonkeyPatch) -> None:
    websocket = FakeWebSocket()
    session = make_session(websocket)
    pcm_path = create_temp_pcm(b"\x00\x01" * 900)
    monkeypatch.setattr(backend.turn_processing_service.asyncio, "sleep", AsyncMock(return_value=None))
    chunks = await backend.stream_pcm_audio_file(
        session,
        turn_id="turn-1",
        pcm_path=pcm_path,
        sample_rate=16000,
        channels=1,
        source="tts",
    )
    assert websocket.sent[0]["type"] == "assistant.audio.start"
    assert any(message["type"] == "assistant.audio.chunk" for message in websocket.sent)
    assert websocket.sent[-1]["type"] == "assistant.audio.end"
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

    monkeypatch.setattr(backend.turn_processing_service, "send", fail_send)
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
    monkeypatch.setattr(backend.turn_processing_service, "stream_pcm_audio_file", stream_mock)
    ok = await backend.stream_loopback_audio(session, "turn-1")
    assert ok is True
    stream_mock.assert_awaited_once()
    os.remove(session.audio_file_path)


@pytest.mark.asyncio
async def test_transcribe_recording_returns_empty_when_stt_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
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
async def test_transcribe_recording_returns_empty_when_pipeline_fails(monkeypatch: pytest.MonkeyPatch) -> None:
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
async def test_synthesize_text_to_audio_returns_false_for_empty_text(monkeypatch: pytest.MonkeyPatch) -> None:
    session = make_session()
    fake_pipeline = Mock(tts_available=True)
    monkeypatch.setattr(backend, "speech_pipeline", fake_pipeline)
    ok = await backend.synthesize_text_to_audio(session, "turn-1", "   ")
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
    monkeypatch.setattr(backend.turn_processing_service, "stream_pcm_audio_file", stream_mock)
    ok = await backend.synthesize_text_to_audio(session, "turn-1", "hola mundo")
    assert ok is True
    stream_mock.assert_awaited_once()
    assert not os.path.exists(temp_pcm)


@pytest.mark.asyncio
async def test_synthesize_text_to_audio_propagates_cancelled_error(monkeypatch: pytest.MonkeyPatch) -> None:
    session = make_session()
    temp_pcm = create_temp_pcm()
    fake_pipeline = Mock(tts_available=True)
    fake_pipeline.synthesize_text_to_pcm_file = Mock(return_value=(temp_pcm, os.path.getsize(temp_pcm)))
    stream_mock = AsyncMock(side_effect=asyncio.CancelledError())
    monkeypatch.setattr(backend, "speech_pipeline", fake_pipeline)
    monkeypatch.setattr(backend.turn_processing_service, "stream_pcm_audio_file", stream_mock)
    with pytest.raises(asyncio.CancelledError):
        await backend.synthesize_text_to_audio(session, "turn-1", "hola")
    assert not os.path.exists(temp_pcm)


@pytest.mark.asyncio
async def test_process_turn_echo_mode_emits_expected_messages(monkeypatch: pytest.MonkeyPatch) -> None:
    websocket = FakeWebSocket()
    session = make_session(websocket)
    session.turn_id = "turn-1"
    session.text_fragments = ["hola"]
    session.turn_started_monotonic = time.monotonic() - 0.2
    monkeypatch.setattr(backend, "AUDIO_REPLY_MODE", "echo")
    monkeypatch.setattr(backend.turn_processing_service, "synthesize_text_to_audio", AsyncMock(return_value=True))

    await backend.process_turn(session)

    types = [msg["type"] for msg in websocket.sent]
    assert "transcript.final" in types
    assert "assistant.text.partial" in types
    final = [msg for msg in websocket.sent if msg["type"] == "assistant.text.final"][-1]
    assert final["text"] == "hola"
    assert final["interrupted"] is False


@pytest.mark.asyncio
async def test_process_turn_assistant_mode_uses_adapter_stream_and_loopback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    websocket = FakeWebSocket()
    session = make_session(websocket)
    session.turn_id = "turn-1"
    session.text_fragments = ["texto"]
    session.audio_bytes_received = 1024
    session.audio_chunks_received = 2

    class AdapterStub:
        async def stream_response(self, **_kwargs: Any):
            for chunk in ("respuesta ", "final"):
                yield chunk

    monkeypatch.setattr(backend, "AUDIO_REPLY_MODE", "assistant")
    monkeypatch.setattr(backend, "adapter", AdapterStub())
    monkeypatch.setattr(backend.turn_processing_service, "transcribe_recording", AsyncMock(return_value="audio"))
    monkeypatch.setattr(backend.turn_processing_service, "synthesize_text_to_audio", AsyncMock(return_value=False))
    loopback_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(backend.turn_processing_service, "stream_loopback_audio", loopback_mock)

    await backend.process_turn(session)

    transcript = next(msg for msg in websocket.sent if msg["type"] == "transcript.final")
    final = next(msg for msg in websocket.sent if msg["type"] == "assistant.text.final")
    assert transcript["text"] == "texto audio"
    assert final["text"] == "respuesta final"
    loopback_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_process_turn_audio_only_uses_assistant_response_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    websocket = FakeWebSocket()
    session = make_session(websocket)
    session.turn_id = "turn-1"
    session.audio_bytes_received = 2048
    session.audio_chunks_received = 3

    class AdapterStub:
        async def stream_response(self, **_kwargs: Any):
            for chunk in ("respuesta ", "generica"):
                yield chunk

    monkeypatch.setattr(backend, "AUDIO_REPLY_MODE", "assistant")
    monkeypatch.setattr(backend, "adapter", AdapterStub())
    monkeypatch.setattr(
        backend.turn_processing_service,
        "transcribe_recording",
        AsyncMock(return_value="esto viene del microfono"),
    )
    synthesize_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(backend.turn_processing_service, "synthesize_text_to_audio", synthesize_mock)
    stream_loopback_mock = AsyncMock(return_value=False)
    monkeypatch.setattr(backend.turn_processing_service, "stream_loopback_audio", stream_loopback_mock)

    await backend.process_turn(session)

    transcript = next(msg for msg in websocket.sent if msg["type"] == "transcript.final")
    final = next(msg for msg in websocket.sent if msg["type"] == "assistant.text.final")
    assert transcript["text"] == "esto viene del microfono"
    assert final["text"] == "respuesta generica"
    synthesize_mock.assert_awaited_once_with(
        backend._container.context,
        session,
        "turn-1",
        "respuesta generica",
    )
    stream_loopback_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_turn_mock_mode_uses_transcript_as_assistant_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    websocket = FakeWebSocket()
    session = make_session(websocket)
    session.turn_id = "turn-1"
    session.audio_bytes_received = 2048
    session.audio_chunks_received = 3

    class MockModeAdapter:
        mode = "mock"

        async def stream_response(self, **_kwargs: Any):
            raise AssertionError("mock mode must not call assistant generation")
            yield ""

    monkeypatch.setattr(backend, "AUDIO_REPLY_MODE", "assistant")
    monkeypatch.setattr(backend, "adapter", MockModeAdapter())
    monkeypatch.setattr(
        backend.turn_processing_service,
        "transcribe_recording",
        AsyncMock(return_value="texto reconocido por stt"),
    )
    synthesize_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(backend.turn_processing_service, "synthesize_text_to_audio", synthesize_mock)
    stream_loopback_mock = AsyncMock(return_value=False)
    monkeypatch.setattr(backend.turn_processing_service, "stream_loopback_audio", stream_loopback_mock)

    await backend.process_turn(session)

    transcript = next(msg for msg in websocket.sent if msg["type"] == "transcript.final")
    partial = next(msg for msg in websocket.sent if msg["type"] == "assistant.text.partial")
    final = next(msg for msg in websocket.sent if msg["type"] == "assistant.text.final")
    assert transcript["text"] == "texto reconocido por stt"
    assert partial["text"] == "texto reconocido por stt"
    assert final["text"] == "texto reconocido por stt"
    synthesize_mock.assert_awaited_once_with(
        backend._container.context,
        session,
        "turn-1",
        "texto reconocido por stt",
    )
    stream_loopback_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_turn_handles_cancelled_generation(monkeypatch: pytest.MonkeyPatch) -> None:
    websocket = FakeWebSocket()
    session = make_session(websocket)
    session.turn_id = "turn-1"
    session.text_fragments = ["hola"]

    async def interrupting_tts(_ctx: Any, _session: Any, _turn: str, _text: str) -> bool:
        session.interrupted.set()
        return False

    monkeypatch.setattr(backend, "AUDIO_REPLY_MODE", "echo")
    monkeypatch.setattr(backend.turn_processing_service, "synthesize_text_to_audio", interrupting_tts)

    await backend.process_turn(session)
    final = [msg for msg in websocket.sent if msg["type"] == "assistant.text.final"][-1]
    assert final["interrupted"] is True


@pytest.mark.asyncio
async def test_send_session_ready_emits_protocol_and_capabilities(monkeypatch: pytest.MonkeyPatch) -> None:
    websocket = FakeWebSocket()
    session = make_session(websocket)
    fake_pipeline = Mock()
    fake_pipeline.capabilities.return_value = {"stt_available": True}
    monkeypatch.setattr(backend, "speech_pipeline", fake_pipeline)
    monkeypatch.setattr(backend, "AUDIO_REPLY_MODE", "assistant")
    await backend.send_session_ready(session)
    msg = websocket.sent[-1]
    assert msg["type"] == "session.ready"
    assert msg["audio_reply_mode"] == "assistant"
    assert msg["speech"] == {"stt_available": True}
    assert msg["agents_version"] == backend._container.settings.agent_catalog_version
    assert msg["agents_cache_seed"] is True


@pytest.mark.asyncio
async def test_handle_message_device_hello_authenticates_and_sends_ready() -> None:
    websocket = FakeWebSocket()
    session = make_session(websocket)
    await backend.handle_message(session, {"type": "device.hello", "device_id": "dev-1"})
    assert session.authenticated is True
    assert session.device_id == "dev-1"
    assert any(msg["type"] == "session.ready" for msg in websocket.sent)


@pytest.mark.asyncio
async def test_handle_message_device_hello_sends_auth_error_on_validation_failure() -> None:
    websocket = FakeWebSocket()
    session = make_session(websocket)
    await backend.handle_message(session, {"type": "device.hello"})
    assert websocket.sent[0]["type"] == "error"
    assert websocket.sent[0]["code"] == "auth_error"


@pytest.mark.asyncio
async def test_handle_message_rejects_when_not_authenticated() -> None:
    websocket = FakeWebSocket()
    session = make_session(websocket)
    await backend.handle_message(session, {"type": "session.start"})
    assert websocket.sent[0]["type"] == "error"
    assert websocket.sent[0]["code"] == "unauthorized"


@pytest.mark.asyncio
async def test_handle_message_agent_select_valid_and_invalid() -> None:
    websocket = FakeWebSocket()
    session = make_session(websocket)
    session.authenticated = True
    await backend.handle_message(session, {"type": "agent.select", "agent_id": "invalid"})
    assert websocket.sent[-2]["type"] == "error"

    websocket.sent.clear()
    await backend.handle_message(session, {"type": "agent.select", "agent_id": backend.AVAILABLE_AGENTS[0]})
    assert websocket.sent[0]["type"] == "agent.selected"


@pytest.mark.asyncio
async def test_handle_message_agents_version_request_returns_lightweight_version() -> None:
    websocket = FakeWebSocket()
    session = make_session(websocket)
    session.authenticated = True

    await backend.handle_message(session, {"type": "agents.version.request"})

    assert websocket.sent[0]["type"] == "agents.version.response"
    assert websocket.sent[0]["version"] == backend._container.settings.agent_catalog_version
    assert "agents" not in websocket.sent[0]


@pytest.mark.asyncio
async def test_handle_message_agents_list_request_returns_catalog_and_active_agent() -> None:
    websocket = FakeWebSocket()
    session = make_session(websocket)
    session.authenticated = True

    await backend.handle_message(session, {"type": "agents.list.request"})

    assert websocket.sent[0]["type"] == "agents.list.response"
    assert websocket.sent[0]["agents"] == backend.AVAILABLE_AGENTS
    assert websocket.sent[0]["active_agent"] == session.active_agent


def test_validate_device_message_accepts_new_agent_catalog_requests() -> None:
    assert backend.validate_device_message({"type": "agents.version.request"})["type"] == "agents.version.request"
    assert backend.validate_device_message({"type": "agents.list.request"})["type"] == "agents.list.request"


def test_backend_ui_state_contract_stays_remote_only() -> None:
    remote_states = {state.value for state in UiState}
    assert remote_states == {"idle", "listening", "processing", "speaking", "error"}


@pytest.mark.asyncio
async def test_handle_message_recording_start_and_audio_chunk_and_debug_text() -> None:
    websocket = FakeWebSocket()
    session = make_session(websocket)
    session.authenticated = True

    await backend.handle_message(session, {"type": "recording.start", "turn_id": "turn-1"})
    assert session.recording is True

    payload = base64.b64encode(b"abcd").decode("ascii")
    await backend.handle_message(
        session,
        {"type": "audio.chunk", "seq": 0, "duration_ms": 30, "payload": payload, "text_hint": "hola"},
    )
    assert session.audio_chunks_received == 1
    assert session.audio_bytes_received == 4
    assert session.text_fragments[-1] == "hola"

    await backend.handle_message(session, {"type": "debug.user_text", "text": "mundo"})
    assert session.text_fragments[-1] == "mundo"


@pytest.mark.asyncio
async def test_handle_message_audio_chunk_uses_size_bytes_when_payload_invalid() -> None:
    websocket = FakeWebSocket()
    session = make_session(websocket)
    session.authenticated = True
    await backend.handle_message(session, {"type": "recording.start", "turn_id": "turn-1"})
    await backend.handle_message(
        session,
        {"type": "audio.chunk", "payload": "not-base64", "size_bytes": 321},
    )
    assert session.audio_chunks_received == 1
    assert session.audio_bytes_received == 321


@pytest.mark.asyncio
async def test_handle_message_debug_user_text_empty_returns_error() -> None:
    websocket = FakeWebSocket()
    session = make_session(websocket)
    session.authenticated = True
    await backend.handle_message(session, {"type": "debug.user_text", "text": "   "})
    assert websocket.sent[0]["type"] == "error"


@pytest.mark.asyncio
async def test_handle_message_recording_stop_and_cancel(monkeypatch: pytest.MonkeyPatch) -> None:
    websocket = FakeWebSocket()
    session = make_session(websocket)
    session.authenticated = True

    await backend.handle_message(session, {"type": "recording.stop"})
    assert websocket.sent[0]["type"] == "error"

    websocket.sent.clear()
    session.recording = True
    session.audio_file_handle = io.BytesIO()
    monkeypatch.setattr(backend.message_router_service, "process_turn", AsyncMock(return_value=None))
    await backend.handle_message(session, {"type": "recording.stop"})
    assert session.response_task is not None
    await session.response_task

    await backend.handle_message(session, {"type": "recording.cancel"})
    assert session.recording is False


@pytest.mark.asyncio
async def test_handle_message_interrupt_ping_and_unknown() -> None:
    websocket = FakeWebSocket()
    session = make_session(websocket)
    session.authenticated = True

    async def long_task() -> None:
        await asyncio.sleep(60)

    session.response_task = asyncio.create_task(long_task())
    await backend.handle_message(session, {"type": "assistant.interrupt"})
    assert session.interrupted.is_set()

    await backend.handle_message(session, {"type": "ping"})
    assert websocket.sent[-1]["type"] == "pong"

    await backend.handle_message(session, {"type": "unknown"})
    assert websocket.sent[-2]["type"] == "error"


@pytest.mark.asyncio
async def test_websocket_endpoint_accepts_and_dispatches_messages(monkeypatch: pytest.MonkeyPatch) -> None:
    websocket = FakeWebSocket(incoming=[{"type": "ping"}])
    handle_mock = AsyncMock()
    monkeypatch.setattr(backend, "validate_device_message", Mock(side_effect=lambda raw: raw))
    monkeypatch.setattr(backend, "handle_message", handle_mock)
    await backend.websocket_endpoint(websocket)  # type: ignore[arg-type]
    assert websocket.accepted is True
    assert handle_mock.await_count == 1


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
