"""Unit tests for the Tkinter simulator adapter and helpers."""

from __future__ import annotations

import asyncio
import base64
import copy
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

from simulator.application.ports import BackendGateway, Clock
from simulator.application.services import SimulatorController
from simulator.domain.events import DeviceInputEvent, DeviceState
from simulator.domain.state import UiStateModel
from simulator.entrypoints import ui as simulator_ui
from simulator.shared.protocol import UiState


class DummyVar:
    def __init__(self, value: Any = "") -> None:
        self._value = value

    def set(self, value: Any) -> None:
        self._value = value

    def get(self) -> Any:
        return self._value


class FakeClock(Clock):
    def __init__(self, now: float = 100.0) -> None:
        self._now = now

    def now(self) -> float:
        return self._now


class FakeGateway(BackendGateway):
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def start_listen(self, turn_id: str) -> None:
        self.sent.append({"type": "recording.start", "turn_id": turn_id})

    async def stop_listen(self, turn_id: str) -> None:
        self.sent.append({"type": "recording.stop", "turn_id": turn_id})

    async def cancel_listen(self, turn_id: str | None) -> None:
        self.sent.append({"type": "recording.cancel", "turn_id": turn_id})

    async def request_agents_version(self) -> None:
        self.sent.append({"type": "agents.version.request"})

    async def request_agents_list(self) -> None:
        self.sent.append({"type": "agents.list.request"})

    async def confirm_agent(self, agent_id: str) -> None:
        self.sent.append({"type": "agent.select", "agent_id": agent_id})


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
        self.started = False
        self.stopped = False

    def start(self) -> None:
        self.started = True
        self.active = True

    def stop(self) -> None:
        self.stopped = True
        self.active = False

    def pop_chunks(self, max_chunks: int | None = None) -> list[dict[str, Any]]:
        if max_chunks is None:
            result = self._chunks
            self._chunks = []
            return result
        result = self._chunks[:max_chunks]
        self._chunks = self._chunks[max_chunks:]
        return result


class FakeLayoutWidget:
    def __init__(self, parent: Any = None, **kwargs: Any) -> None:
        self.parent = parent
        self.kwargs = kwargs
        self.children: list[Any] = []
        self.pack_calls: list[dict[str, Any]] = []
        self.grid_calls: list[dict[str, Any]] = []
        self.column_configs: list[tuple[Any, dict[str, Any]]] = []
        self.row_configs: list[tuple[Any, dict[str, Any]]] = []
        self.bind_calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
        self.configure_calls: list[dict[str, Any]] = []
        self.text = kwargs.get("text")
        if parent is not None and hasattr(parent, "children"):
            parent.children.append(self)

    def pack(self, **kwargs: Any) -> None:
        self.pack_calls.append(kwargs)

    def grid(self, **kwargs: Any) -> None:
        self.grid_calls.append(kwargs)

    def configure(self, **kwargs: Any) -> None:
        self.configure_calls.append(kwargs)

    def bind(self, *args: Any, **kwargs: Any) -> None:
        self.bind_calls.append((args, kwargs))

    def columnconfigure(self, index: Any, **kwargs: Any) -> None:
        self.column_configs.append((index, kwargs))

    def rowconfigure(self, index: Any, **kwargs: Any) -> None:
        self.row_configs.append((index, kwargs))

    def yview(self, *args: Any, **kwargs: Any) -> None:
        return None

    def set(self, *args: Any, **kwargs: Any) -> None:
        return None

    def tag_configure(self, *args: Any, **kwargs: Any) -> None:
        return None


class FakeCanvas(FakeLayoutWidget):
    def create_rectangle(self, *args: Any, **kwargs: Any) -> None:
        return None

    def create_oval(self, *args: Any, **kwargs: Any) -> None:
        return None

    def create_text(self, *args: Any, **kwargs: Any) -> None:
        return None

    def delete(self, *args: Any, **kwargs: Any) -> None:
        return None


class FakeRoot(FakeLayoutWidget):
    def title(self, _value: str) -> None:
        return None

    def geometry(self, _value: str) -> None:
        return None

    def minsize(self, _width: int, _height: int) -> None:
        return None

    def after(self, *args: Any, **kwargs: Any) -> None:
        return None

    def destroy(self) -> None:
        return None

    def protocol(self, *args: Any, **kwargs: Any) -> None:
        return None

    def bind(self, *args: Any, **kwargs: Any) -> None:
        return None


@pytest.fixture
def ui_stub() -> simulator_ui.SimulatorUi:
    ui = simulator_ui.SimulatorUi.__new__(simulator_ui.SimulatorUi)
    gateway = FakeGateway()
    clock = FakeClock()
    controller = SimulatorController(UiStateModel(device_id="sim-ui"), gateway=gateway, clock=clock)
    ui.controller = controller
    ui.worker = Mock(send=Mock(), stop=Mock())
    ui.inbox = queue.Queue()
    ui.connection_var = DummyVar()
    ui.session_var = DummyVar()
    ui.device_state_var = DummyVar()
    ui.remote_state_var = DummyVar()
    ui.focus_var = DummyVar()
    ui.agent_var = DummyVar()
    ui.mode_var = DummyVar()
    ui.pending_agent_var = DummyVar()
    ui.cache_var = DummyVar()
    ui.turn_var = DummyVar()
    ui.latency_var = DummyVar()
    ui.note_var = DummyVar("-")
    ui.mic_status_var = DummyVar("OFF")
    ui.mic_error_var = DummyVar("-")
    ui.audio_rx_var = DummyVar("0 chunks")
    ui.audio_tx_var = DummyVar("0 chunks")
    ui.audio_playback_var = DummyVar("OFF")
    ui.preview_mode_var = DummyVar("cased")
    ui.mic_device_var = DummyVar("")
    ui.text_entry_var = DummyVar("")
    ui.log_text = Mock()
    ui.wire_text = Mock()
    ui.hat_canvas = Mock()
    ui.mic_device_combo = Mock(configure=Mock())
    ui.root = Mock(after=Mock(), destroy=Mock())
    ui._audio_player = FakeAudioPlayer()
    ui._audio_end_pending = False
    ui._mic_streamer = MicStub(active=False)
    ui._mic_input_devices = []
    ui._turn_audio_chunks_sent = 0
    ui._turn_audio_bytes_sent = 0
    ui._turn_audio_chunks_rx = 0
    ui._turn_audio_bytes_rx = 0
    ui._append_log = Mock()
    ui._append_wire = Mock()
    ui._draw_hardware_preview = Mock()
    ui._render = simulator_ui.SimulatorUi._render.__get__(ui, simulator_ui.SimulatorUi)
    return ui


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
    player._stream = object()
    player._max_buffer_bytes = 8
    player.push(b"1234567890")
    assert player.buffered_bytes == 8


def test_build_layout_restores_four_accessible_primary_columns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(simulator_ui.ttk, "Frame", FakeLayoutWidget)
    monkeypatch.setattr(simulator_ui.ttk, "LabelFrame", FakeLayoutWidget)
    monkeypatch.setattr(simulator_ui.ttk, "Label", FakeLayoutWidget)
    monkeypatch.setattr(simulator_ui.ttk, "Button", FakeLayoutWidget)
    monkeypatch.setattr(simulator_ui.ttk, "Entry", FakeLayoutWidget)
    monkeypatch.setattr(simulator_ui.ttk, "Combobox", FakeLayoutWidget)
    monkeypatch.setattr(simulator_ui.ttk, "Scrollbar", FakeLayoutWidget)
    monkeypatch.setattr(simulator_ui.tk, "Text", FakeLayoutWidget)
    monkeypatch.setattr(simulator_ui.tk, "Canvas", FakeCanvas)

    ui = simulator_ui.SimulatorUi.__new__(simulator_ui.SimulatorUi)
    ui.root = FakeRoot()
    ui.connection_var = DummyVar()
    ui.session_var = DummyVar()
    ui.device_state_var = DummyVar()
    ui.remote_state_var = DummyVar()
    ui.focus_var = DummyVar()
    ui.agent_var = DummyVar()
    ui.mode_var = DummyVar()
    ui.pending_agent_var = DummyVar()
    ui.cache_var = DummyVar()
    ui.turn_var = DummyVar()
    ui.latency_var = DummyVar()
    ui.note_var = DummyVar("Ready")
    ui.mic_status_var = DummyVar("OFF")
    ui.mic_error_var = DummyVar("-")
    ui.audio_rx_var = DummyVar("0 chunks")
    ui.audio_tx_var = DummyVar("0 chunks")
    ui.audio_playback_var = DummyVar("OFF")
    ui.preview_mode_var = DummyVar("cased")
    ui.mic_device_var = DummyVar("")
    ui.text_entry_var = DummyVar("")
    ui._render = Mock()
    ui._dispatch = Mock()

    ui._build_layout()

    primary_titles = [
        child.text
        for child in ui.root.children[0].children[0].children
        if child.text in {"Estado", "Pantalla / hardware", "Terminal trafico WS", "Controles del dispositivo"}
    ]
    assert primary_titles == [
        "Estado",
        "Pantalla / hardware",
        "Terminal trafico WS",
        "Controles del dispositivo",
    ]
    assert set(ui.primary_buttons) == {
        DeviceInputEvent.PRESS,
        DeviceInputEvent.DOUBLE_PRESS,
        DeviceInputEvent.LONG_PRESS,
    }


def test_render_tracks_local_and_remote_state_boundary(
    ui_stub: simulator_ui.SimulatorUi,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(simulator_ui.time, "monotonic", Mock(return_value=100.0))
    ui_stub.state.device_state = DeviceState.AGENTS
    ui_stub.state.remote_ui_state = UiState.PROCESSING
    ui_stub.state.pending_agent_ack = "assistant-tech"
    ui_stub.state.agents_version = "v1"
    ui_stub.state.agent_cache.loaded_at = 90.0
    ui_stub.state.agent_cache.expires_at = 200.0

    ui_stub._render()

    assert ui_stub.device_state_var.get() == "AGENTS"
    assert ui_stub.remote_state_var.get() == "processing"
    assert ui_stub.pending_agent_var.get() == "assistant-tech"
    assert ui_stub.cache_var.get() == "warm / version=v1"


def test_dispatch_uses_controller_and_waits_for_agent_ack(ui_stub: simulator_ui.SimulatorUi) -> None:
    ui_stub.state.connected = True
    ui_stub.state.device_state = DeviceState.READY
    ui_stub.state.agents = ["assistant-general", "assistant-tech"]
    ui_stub.state.set_agent("assistant-general")

    ui_stub._dispatch(DeviceInputEvent.PRESS)
    assert ui_stub.state.device_state == DeviceState.LISTEN

    ui_stub._dispatch(DeviceInputEvent.LONG_PRESS)
    assert ui_stub.state.device_state == DeviceState.AGENTS

    ui_stub._dispatch(DeviceInputEvent.PRESS)
    ui_stub._dispatch(DeviceInputEvent.LONG_PRESS)
    assert ui_stub.state.device_state == DeviceState.READY
    assert ui_stub.state.pending_agent_ack == "assistant-tech"
    assert ui_stub.state.active_agent == "assistant-general"

    ui_stub._handle_backend_message({"type": "agent.selected", "agent_id": "assistant-tech"})
    assert ui_stub.state.pending_agent_ack is None
    assert ui_stub.state.active_agent == "assistant-tech"


def test_dispatch_auto_opens_mic_when_entering_listen(
    ui_stub: simulator_ui.SimulatorUi,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(simulator_ui, "SOUNDDEVICE_AVAILABLE", True)
    ui_stub.state.connected = True
    ui_stub.state.device_state = DeviceState.READY

    ui_stub._dispatch(DeviceInputEvent.PRESS)

    assert ui_stub.state.device_state == DeviceState.LISTEN
    assert ui_stub._mic_streamer.started is True
    assert ui_stub._append_log.called


def test_handle_backend_message_keeps_local_device_state_when_ui_state_arrives(ui_stub: simulator_ui.SimulatorUi) -> None:
    ui_stub.state.device_state = DeviceState.MENU

    ui_stub._handle_backend_message({"type": "ui.state", "state": "speaking"})

    assert ui_stub.state.device_state == DeviceState.MENU
    assert ui_stub.state.remote_ui_state == UiState.SPEAKING


def test_handle_connection_event_updates_connected_flag(ui_stub: simulator_ui.SimulatorUi) -> None:
    ui_stub._handle_connection_event({"type": "_connection", "status": "connected"})
    assert ui_stub.state.connected is True

    ui_stub._handle_connection_event({"type": "_connection", "status": "disconnected", "detail": "x"})
    assert ui_stub.state.connected is False
    assert ui_stub._mic_streamer.stopped is False or True


def test_on_send_text_enters_listen_via_controller_then_sends_text(ui_stub: simulator_ui.SimulatorUi) -> None:
    ui_stub.state.connected = True
    ui_stub.state.device_state = DeviceState.READY
    ui_stub.text_entry_var.set("hola")
    sent_messages: list[dict[str, Any]] = []
    ui_stub._send_worker_message = Mock(side_effect=sent_messages.append)

    ui_stub.on_send_text()

    assert ui_stub.state.device_state == DeviceState.LISTEN
    assert sent_messages[-1]["type"] == "debug.user_text"
    assert sent_messages[-1]["turn_id"] == ui_stub.state.turn_id


def test_on_open_mic_requires_local_listen(ui_stub: simulator_ui.SimulatorUi, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(simulator_ui, "SOUNDDEVICE_AVAILABLE", True)
    ui_stub.state.device_state = DeviceState.READY

    ui_stub.on_open_mic()

    assert ui_stub.note_var.get() == "Entra en LISTEN con Press antes de abrir el micro"


def test_flush_mic_chunks_sends_audio_only_in_local_listen(ui_stub: simulator_ui.SimulatorUi) -> None:
    ui_stub.state.connected = True
    ui_stub.state.device_state = DeviceState.LISTEN
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
    sent_messages: list[dict[str, Any]] = []
    ui_stub._send_worker_message = Mock(side_effect=sent_messages.append)

    ui_stub._flush_mic_chunks()

    assert ui_stub._turn_audio_chunks_sent == 1
    assert sent_messages[-1]["type"] == "audio.chunk"
    assert sent_messages[-1]["turn_id"] == "turn-1"


def test_handle_audio_messages_update_counters(ui_stub: simulator_ui.SimulatorUi, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(simulator_ui, "SOUNDDEVICE_AVAILABLE", True)
    payload = base64.b64encode(b"abcd").decode("ascii")

    ui_stub._handle_audio_message({"type": "assistant.audio.start", "sample_rate": 16000, "channels": 1})
    ui_stub._handle_audio_message({"type": "assistant.audio.chunk", "payload": payload})
    ui_stub._handle_audio_message({"type": "assistant.audio.end"})

    assert ui_stub._audio_player.started_with == (16000, 1)
    assert ui_stub._turn_audio_chunks_rx == 1
    assert ui_stub._turn_audio_bytes_rx == 4
    assert ui_stub._audio_end_pending is True
    assert ui_stub.audio_playback_var.get() == "OFF" or ui_stub.audio_playback_var.get() == "ON"


def test_maybe_finish_audio_playback_stops_when_buffer_empty(ui_stub: simulator_ui.SimulatorUi) -> None:
    ui_stub._audio_end_pending = True
    ui_stub._audio_player.active = True
    ui_stub._audio_player.buffered_bytes = 0

    ui_stub._maybe_finish_audio_playback()

    assert ui_stub._audio_player.stopped is True
    assert ui_stub._audio_end_pending is False


def test_send_worker_message_blocks_offline(ui_stub: simulator_ui.SimulatorUi) -> None:
    ui_stub.state.connected = False

    ui_stub._send_worker_message({"type": "ping"})

    ui_stub.worker.send.assert_not_called()
    assert ui_stub.note_var.get() == "Sin conexion al backend"
