"""Unit tests for the shared device runtime foundation."""

from __future__ import annotations

import asyncio
import base64
import sys
from pathlib import Path

import pytest

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.shared.protocol import build_message
from device_runtime.application.services import DiagnosticsService, DisplayModelService
from device_runtime.domain.capabilities import CapabilityState, CapabilityStatus, DeviceCapabilities
from device_runtime.domain.events import DeviceState
from device_runtime.domain.state import DeviceSnapshot
from device_runtime.entrypoints.raspi_main import RuntimeBootstrap, RuntimeRunner, build_hello_payload, build_runner, build_runtime
from device_runtime.infrastructure.audio.null_audio import NullAudioCapture
from device_runtime.infrastructure.display.null_display import NullDisplay
from device_runtime.infrastructure.input.null_button import NullButton
from device_runtime.infrastructure.diagnostics.null_diagnostics import NullDiagnostics
from device_runtime.infrastructure.config.env_loader import load_runtime_config


def test_runtime_config_loads_minimal_environment() -> None:
    config = load_runtime_config({"DEVICE_ID": "raspi-1", "DEVICE_WS_URL": "ws://localhost/ws"})

    assert config.device_id == "raspi-1"
    assert config.ws_url == "ws://localhost/ws"
    assert config.transport_adapter == "websocket"


def test_runtime_config_rejects_invalid_integer_values() -> None:
    with pytest.raises(ValueError, match="DEVICE_AUDIO_CHUNK_MS"):
        load_runtime_config(
            {
                "DEVICE_ID": "raspi-1",
                "DEVICE_WS_URL": "ws://localhost/ws",
                "DEVICE_AUDIO_CHUNK_MS": "abc",
            }
        )


def test_display_model_service_builds_shared_focus_labels() -> None:
    snapshot = DeviceSnapshot(device_id="raspi-1", device_state=DeviceState.AGENTS)
    snapshot.agents = ["assistant-general", "assistant-tech"]
    snapshot.navigation.focused_agent_index = 1
    snapshot.warnings = ["audio_in unavailable"]

    model = DisplayModelService().build(snapshot)

    assert model.local_state == "AGENTS"
    assert model.focus_label == "assistant-tech"
    assert model.warnings == ["audio_in unavailable"]


def test_diagnostics_service_derives_warnings_from_capabilities() -> None:
    snapshot = DeviceSnapshot(device_id="raspi-1")
    snapshot.capabilities = DeviceCapabilities(
        screen=CapabilityState("screen", CapabilityStatus.DEGRADED, "adapter=null"),
        button=CapabilityState("button", CapabilityStatus.ENABLED),
        audio_in=CapabilityState("audio_in", CapabilityStatus.UNAVAILABLE, "missing device"),
        audio_out=CapabilityState("audio_out", CapabilityStatus.ENABLED),
        transport=CapabilityState("transport", CapabilityStatus.ENABLED),
    )

    diagnostics = DiagnosticsService().build_snapshot(snapshot)

    assert "screen degraded: adapter=null" in diagnostics.warnings
    assert "audio_in unavailable: missing device" in diagnostics.warnings


def test_raspi_bootstrap_builds_degraded_null_runtime() -> None:
    runtime = build_runtime({"DEVICE_ID": "raspi-1", "DEVICE_WS_URL": "ws://localhost/ws"})

    assert runtime.snapshot.device_id == "raspi-1"
    assert runtime.snapshot.capabilities.transport.detail == "adapter=websocket"
    assert "screen degraded: adapter=null" in runtime.snapshot.warnings


def test_raspi_bootstrap_builds_non_simulated_hello_payload() -> None:
    runtime = build_runtime({"DEVICE_ID": "raspi-1", "DEVICE_WS_URL": "ws://localhost/ws"})

    hello = build_hello_payload(runtime)

    assert hello["type"] == "device.hello"
    assert hello["simulated"] is False
    assert hello["device_id"] == "raspi-1"
    assert "screen" in hello["capabilities"]


class FakeTransport:
    def __init__(self) -> None:
        self.message_handler = None
        self.connection_handler = None
        self.closed = False

    def set_message_handler(self, handler):
        self.message_handler = handler

    def set_connection_handler(self, handler):
        self.connection_handler = handler

    async def connect(self) -> None:
        assert self.connection_handler is not None
        assert self.message_handler is not None
        self.connection_handler("connected", None)
        self.message_handler(build_message("session.ready", session_id="session-1"))
        while not self.closed:
            await asyncio.sleep(0.01)

    async def send(self, message):
        return None

    def close(self) -> None:
        self.closed = True


class FakePlayback:
    def __init__(self) -> None:
        self.available = True
        self.started = False
        self.started_with: tuple[int, int] | None = None
        self.pushed: list[bytes] = []
        self.stop_calls: list[bool] = []

    def start(self, sample_rate: int, channels: int) -> None:
        self.started = True
        self.started_with = (sample_rate, channels)

    def push(self, pcm_bytes: bytes) -> None:
        self.pushed.append(pcm_bytes)

    def stop(self, clear_buffer: bool = True) -> None:
        self.started = False
        self.stop_calls.append(clear_buffer)


class AudioTransport(FakeTransport):
    async def connect(self) -> None:
        assert self.connection_handler is not None
        assert self.message_handler is not None
        self.connection_handler("connected", None)
        self.message_handler(build_message("session.ready", session_id="session-1"))
        self.message_handler(build_message("assistant.audio.start", sample_rate=22050, channels=1))
        self.message_handler(
            build_message("assistant.audio.chunk", payload=base64.b64encode(b"pcm").decode("ascii"))
        )
        self.message_handler(build_message("assistant.audio.end"))
        while not self.closed:
            await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_runtime_runner_keeps_entrypoint_alive_until_stopped() -> None:
    stop_event = asyncio.Event()
    runner = build_runner(
        {
            "DEVICE_ID": "raspi-1",
            "DEVICE_WS_URL": "ws://localhost/ws",
            "DEVICE_BUTTON_ADAPTER": "null",
        },
        transport=FakeTransport(),
    )

    task = asyncio.create_task(runner.run(stop_event=stop_event))
    await asyncio.sleep(0.05)
    stop_event.set()
    await asyncio.wait_for(task, timeout=1.0)

    assert runner.controller.snapshot.connected is True
    assert runner.controller.snapshot.session_id == "session-1"


@pytest.mark.asyncio
async def test_runtime_runner_routes_assistant_audio_chunks_to_playback() -> None:
    runtime = build_runtime({"DEVICE_ID": "raspi-1", "DEVICE_WS_URL": "ws://localhost/ws"})
    playback = FakePlayback()
    runtime.display = NullDisplay()
    runtime.button = NullButton()
    runtime.audio_capture = NullAudioCapture()
    runtime.audio_playback = playback
    runtime.diagnostics = NullDiagnostics()
    stop_event = asyncio.Event()
    runner = RuntimeRunner(
        RuntimeBootstrap(
            config=runtime.config,
            snapshot=runtime.snapshot,
            display=NullDisplay(),
            button=NullButton(),
            audio_capture=NullAudioCapture(),
            audio_playback=playback,
            diagnostics=NullDiagnostics(),
        ),
        transport=AudioTransport(),
    )

    task = asyncio.create_task(runner.run(stop_event=stop_event))
    await asyncio.sleep(0.05)
    stop_event.set()
    await asyncio.wait_for(task, timeout=1.0)

    assert playback.started_with == (22050, 1)
    assert playback.pushed == [b"pcm"]
