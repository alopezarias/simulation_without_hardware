"""Unit tests for UI simulator logic that does not require a real GUI/hardware."""

from __future__ import annotations

import asyncio
import base64
import queue
import sys
import time
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from simulator.entrypoints import ui as simulator_ui
from simulator.shared.protocol import UiState


class DummyVar:
    def __init__(self, value: Any = "") -> None:
        self._value = value

    def set(self, value: Any) -> None:
        self._value = value

    def get(self) -> Any:
        return self._value


class FakeAudioPlayer:
    def __init__(self) -> None:
        self.active = False
        self.buffered_bytes = 0
        self.started_with: tuple[int, int] | None = None
        self.pushed: list[bytes] = []
        self.stopped = False

    def start(self, sample_rate: int, channels: int) -> None:
        self.active = True
        self.started_with = (sample_rate, channels)

    def push(self, pcm_bytes: bytes) -> None:
        self.pushed.append(pcm_bytes)
        self.buffered_bytes += len(pcm_bytes)

    def stop(self, clear_buffer: bool = True) -> None:
        self.stopped = True
        self.active = False
        if clear_buffer:
            self.buffered_bytes = 0


class FakeIncomingWs:
    def __init__(self, messages: list[str]) -> None:
        self._messages = list(messages)

    def __aiter__(self) -> "FakeIncomingWs":
        return self

    async def __anext__(self) -> str:
        if not self._messages:
            raise StopAsyncIteration
        return self._messages.pop(0)


class FakeSendWs:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, payload: str) -> None:
        self.sent.append(payload)


class MicStub:
    def __init__(self, active: bool = False, chunks: list[dict[str, Any]] | None = None) -> None:
        self.active = active
        self._chunks = list(chunks or [])
        self.device_index: int | None = None
        self.bytes_sent = 0
        self.dropped_chunks = 0

    def pop_chunks(self, max_chunks: int | None = None) -> list[dict[str, Any]]:
        if max_chunks is None:
            result = self._chunks
            self._chunks = []
            return result
        result = self._chunks[:max_chunks]
        self._chunks = self._chunks[max_chunks:]
        return result


@pytest.fixture
def ui_stub() -> simulator_ui.SimulatorUi:
    ui = simulator_ui.SimulatorUi.__new__(simulator_ui.SimulatorUi)
    ui.state = simulator_ui.UiStateModel(device_id="sim-ui")
    ui.note_var = DummyVar("-")
    ui.preview_mode_var = DummyVar("cased")
    ui.battery_var = DummyVar(ui.state.battery_level)
    ui.battery_label_var = DummyVar(f"{int(ui.state.battery_level)}%")
    ui._audio_player = FakeAudioPlayer()
    ui._audio_end_pending = False
    ui._turn_audio_chunks_rx = 0
    ui._turn_audio_bytes_rx = 0
    ui._turn_audio_chunks_sent = 0
    ui._turn_audio_bytes_sent = 0
    ui._mic_streamer = MicStub(active=False)
    ui._append_log = Mock()
    ui._append_wire = Mock()
    ui._stop_mic_capture = Mock()
    ui._stop_audio_playback = Mock()
    ui._send_quiet = Mock()
    ui._draw_hat_preview = Mock()
    return ui


def test_ui_state_model_set_agent_updates_index_and_list() -> None:
    state = simulator_ui.UiStateModel(device_id="dev")
    state.set_agent("assistant-tech")
    assert state.active_agent == "assistant-tech"
    state.set_agent("assistant-custom")
    assert state.active_agent == "assistant-custom"
    assert "assistant-custom" in state.agents


def test_wsworker_send_and_stop_flags() -> None:
    inbox: queue.Queue[dict[str, Any]] = queue.Queue()
    worker = simulator_ui.WsWorker(
        ws_url="ws://localhost:8000/ws",
        device_id="dev",
        auth_token="",
        initial_agent="assistant-general",
        inbox=inbox,
    )
    worker.send({"type": "ping"})
    assert worker.outbox.get_nowait()["type"] == "ping"
    worker.stop()
    assert worker.stop_event.is_set()


@pytest.mark.asyncio
async def test_wsworker_recv_loop_ignores_invalid_json_and_pushes_valid() -> None:
    inbox: queue.Queue[dict[str, Any]] = queue.Queue()
    worker = simulator_ui.WsWorker(
        ws_url="ws://localhost:8000/ws",
        device_id="dev",
        auth_token="",
        initial_agent="assistant-general",
        inbox=inbox,
    )
    incoming = FakeIncomingWs(["not-json", '{"type":"ui.state","state":"idle"}'])
    await worker._recv_loop(incoming)  # type: ignore[arg-type]
    message = inbox.get_nowait()
    assert message["type"] == "ui.state"


@pytest.mark.asyncio
async def test_wsworker_send_loop_drains_outbox_once() -> None:
    inbox: queue.Queue[dict[str, Any]] = queue.Queue()
    worker = simulator_ui.WsWorker(
        ws_url="ws://localhost:8000/ws",
        device_id="dev",
        auth_token="",
        initial_agent="assistant-general",
        inbox=inbox,
    )
    ws = FakeSendWs()
    worker.send({"type": "pong"})

    async def stop_soon() -> None:
        await asyncio.sleep(0.06)
        worker.stop_event.set()

    stopper = asyncio.create_task(stop_soon())
    await worker._send_loop(ws)  # type: ignore[arg-type]
    await stopper
    assert ws.sent


def test_mic_audio_streamer_pop_chunks_builds_metadata() -> None:
    streamer = simulator_ui.MicAudioStreamer(sample_rate=16000, channels=1, chunk_ms=120)
    raw = b"\x00\x01" * 400
    streamer._queue.put_nowait(raw)
    streamer._started_monotonic = time.monotonic() - 0.12
    chunks = streamer.pop_chunks(max_chunks=1)
    assert chunks[0]["seq"] == 0
    assert chunks[0]["size_bytes"] == len(raw)
    assert chunks[0]["duration_ms"] > 0
    assert isinstance(chunks[0]["payload"], str)


def test_audio_output_player_push_trims_overflow() -> None:
    player = simulator_ui.AudioOutputPlayer()
    player._stream = object()  # mark as active without opening real audio
    player._max_buffer_bytes = 8
    player.push(b"1234567890")
    assert player.buffered_bytes == 8


def test_wire_safe_payload_masks_audio_and_trims_text(ui_stub: simulator_ui.SimulatorUi) -> None:
    payload = {
        "type": "assistant.audio.chunk",
        "payload": "QUJDREVGR0g=",
        "text": "x" * 300,
    }
    safe = ui_stub._wire_safe_payload(payload)
    assert str(safe["payload"]).startswith("<base64:")
    assert str(safe["text"]).endswith("...<trimmed>")


def test_battery_color_and_dot_thresholds(ui_stub: simulator_ui.SimulatorUi, monkeypatch: pytest.MonkeyPatch) -> None:
    assert ui_stub._battery_color(80) == "#22c55e"
    assert ui_stub._battery_color(55) == "#facc15"
    assert ui_stub._battery_color(20) == "#fb923c"
    assert ui_stub._battery_color(5) == "#ef4444"

    monkeypatch.setattr(simulator_ui.time, "monotonic", Mock(return_value=1.0))
    assert ui_stub._battery_dot_color(5) in {"#ef4444", "#3b0a0a"}


def test_wrap_for_display_and_scrolling_lines(ui_stub: simulator_ui.SimulatorUi) -> None:
    wrapped = ui_stub._wrap_for_display("hola " * 40, max_chars=10, max_lines=3)
    assert len(wrapped.splitlines()) <= 3

    ui_stub.state.transcript = "hola " * 20
    ui_stub.state.assistant_text = "respuesta " * 20
    lines = ui_stub._scrolling_message_lines(max_chars=12, max_lines=5)
    assert len(lines) <= 5
    assert all(origin in {"YOU", "AGENT"} for origin, _ in lines)


def test_handle_connection_event_updates_connection_state(ui_stub: simulator_ui.SimulatorUi) -> None:
    ui_stub._handle_connection_event({"type": "_connection", "status": "connected"})
    assert ui_stub.state.connected is True

    ui_stub._handle_connection_event({"type": "_connection", "status": "disconnected", "detail": "x"})
    assert ui_stub.state.connected is False
    assert ui_stub._stop_mic_capture.called
    assert ui_stub._stop_audio_playback.called


def test_handle_backend_message_session_ready_and_ui_state(ui_stub: simulator_ui.SimulatorUi) -> None:
    ui_stub._handle_backend_message(
        {
            "type": "session.ready",
            "session_id": "session-1",
            "available_agents": ["assistant-general", "assistant-tech"],
            "active_agent": "assistant-tech",
        }
    )
    assert ui_stub.state.session_id == "session-1"
    assert ui_stub.state.active_agent == "assistant-tech"

    ui_stub._handle_backend_message({"type": "ui.state", "state": "invalid"})
    assert ui_stub.state.ui_state == UiState.ERROR


def test_handle_backend_message_transcript_and_assistant(ui_stub: simulator_ui.SimulatorUi) -> None:
    ui_stub._handle_backend_message({"type": "transcript.partial", "text": "hola"})
    ui_stub._handle_backend_message({"type": "transcript.final", "text": "hola final"})
    ui_stub._handle_backend_message({"type": "assistant.text.partial", "text": "res-"})
    ui_stub._handle_backend_message(
        {"type": "assistant.text.final", "text": "respuesta", "interrupted": True, "latency_ms": 210}
    )

    assert ui_stub.state.transcript == "hola final"
    assert ui_stub.state.assistant_text.endswith("[interrupted]")
    assert ui_stub.state.last_latency_ms == 210


def test_handle_backend_message_audio_chunk_updates_counters(ui_stub: simulator_ui.SimulatorUi, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(simulator_ui, "SOUNDDEVICE_AVAILABLE", True)
    payload = base64.b64encode(b"abcd").decode("ascii")
    ui_stub._handle_backend_message({"type": "assistant.audio.chunk", "payload": payload})
    assert ui_stub._turn_audio_chunks_rx == 1
    assert ui_stub._turn_audio_bytes_rx == 4
    assert ui_stub._audio_player.pushed[-1] == b"abcd"


def test_handle_backend_message_error_sets_error_state_and_note(ui_stub: simulator_ui.SimulatorUi) -> None:
    ui_stub._handle_backend_message({"type": "error", "detail": "fallo"})
    assert ui_stub.state.ui_state == UiState.ERROR
    assert ui_stub.note_var.get() == "fallo"


def test_flush_mic_chunks_sends_chunk_messages(ui_stub: simulator_ui.SimulatorUi) -> None:
    ui_stub.state.ui_state = UiState.LISTENING
    ui_stub.state.turn_id = "turn-1"
    ui_stub._mic_streamer = MicStub(
        active=True,
        chunks=[
            {
                "seq": 0,
                "timestamp_ms": 10,
                "duration_ms": 120,
                "payload": "AA==",
                "size_bytes": 1,
            }
        ],
    )

    ui_stub._flush_mic_chunks()

    assert ui_stub._turn_audio_chunks_sent == 1
    assert ui_stub._turn_audio_bytes_sent == 1
    assert ui_stub._send_quiet.called
    sent_message = ui_stub._send_quiet.call_args.args[0]
    assert sent_message["type"] == "audio.chunk"
    assert sent_message["turn_id"] == "turn-1"


def test_maybe_finish_audio_playback_stops_when_buffer_empty(ui_stub: simulator_ui.SimulatorUi) -> None:
    ui_stub._audio_end_pending = True
    ui_stub._audio_player.active = True
    ui_stub._audio_player.buffered_bytes = 0
    ui_stub._maybe_finish_audio_playback()
    assert ui_stub._audio_player.stopped is True
    assert ui_stub._audio_end_pending is False
