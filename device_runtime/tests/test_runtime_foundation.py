"""Unit tests for the shared device runtime foundation."""

from __future__ import annotations

import asyncio
import base64
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from device_runtime.application.ports import PowerStatus
from device_runtime.application.services import BatteryDisplayService, DiagnosticsService, DisplayModelService, ExperienceService, RgbPolicyService
from device_runtime.domain.capabilities import CapabilityState, CapabilityStatus, DeviceCapabilities
from device_runtime.domain.events import DeviceState
from device_runtime.domain.state import DeviceSnapshot
from device_runtime.entrypoints.raspi_main import CachedPowerStatus, RuntimeBootstrap, RuntimeObserver, RuntimeRunner, build_hello_payload, build_runner, build_runtime
from device_runtime.infrastructure.audio.null_audio import NullAudioCapture
from device_runtime.infrastructure.display.null_display import NullDisplay
from device_runtime.infrastructure.input.null_button import NullButton
from device_runtime.infrastructure.diagnostics.null_diagnostics import NullDiagnostics
from device_runtime.infrastructure.config.env_loader import load_runtime_config
from device_runtime.infrastructure.power.pisugar_status import NullPowerStatus
from device_runtime.infrastructure.rgb.null_rgb import NullRgb
from device_runtime.protocol import UiState, build_message


def test_runtime_config_loads_minimal_environment() -> None:
    config = load_runtime_config({"DEVICE_ID": "raspi-1", "DEVICE_WS_URL": "ws://localhost/ws"})

    assert config.device_id == "raspi-1"
    assert config.ws_url == "ws://localhost/ws"
    assert config.transport_adapter == "websocket"
    assert config.button_long_press_ms == 400


def test_runtime_config_accepts_vendor_driver_path_and_backlight() -> None:
    config = load_runtime_config(
        {
            "DEVICE_ID": "raspi-1",
            "DEVICE_WS_URL": "ws://localhost/ws",
            "DEVICE_WHISPLAY_DRIVER_PATH": "~/Whisplay/Driver",
            "DEVICE_WHISPLAY_BACKLIGHT": "60",
        }
    )

    assert config.whisplay_driver_path == "~/Whisplay/Driver"
    assert config.whisplay_backlight == 60


def test_runtime_config_accepts_pisugar_socket_and_sysfs_overrides() -> None:
    config = load_runtime_config(
        {
            "DEVICE_ID": "raspi-1",
            "DEVICE_WS_URL": "ws://localhost/ws",
            "DEVICE_PISUGAR_MODE": "uds",
            "DEVICE_PISUGAR_SOCKET_PATH": "/run/pisugar.sock",
            "DEVICE_PISUGAR_SYSFS_ROOT": "/tmp/power_supply",
        }
    )

    assert config.pisugar_mode == "uds"
    assert config.pisugar_socket_path == "/run/pisugar.sock"
    assert config.pisugar_sysfs_root == "/tmp/power_supply"


def test_runtime_config_accepts_battery_display_calibration_overrides() -> None:
    config = load_runtime_config(
        {
            "DEVICE_ID": "raspi-1",
            "DEVICE_WS_URL": "ws://localhost/ws",
            "DEVICE_BATTERY_DISPLAY_CALIBRATION": "0:0,5:40,25:75,70:100",
            "DEVICE_BATTERY_DISPLAY_BAR_COUNT": "5",
        }
    )

    assert config.battery_display_calibration == "0:0,5:40,25:75,70:100"
    assert config.battery_display_bar_count == 5


def test_runtime_config_rejects_invalid_battery_display_bar_count() -> None:
    with pytest.raises(ValueError, match="DEVICE_BATTERY_DISPLAY_BAR_COUNT"):
        load_runtime_config(
            {
                "DEVICE_ID": "raspi-1",
                "DEVICE_WS_URL": "ws://localhost/ws",
                "DEVICE_BATTERY_DISPLAY_BAR_COUNT": "0",
            }
        )


def test_runtime_config_rejects_invalid_battery_display_calibration() -> None:
    with pytest.raises(ValueError, match="DEVICE_BATTERY_DISPLAY_CALIBRATION"):
        load_runtime_config(
            {
                "DEVICE_ID": "raspi-1",
                "DEVICE_WS_URL": "ws://localhost/ws",
                "DEVICE_BATTERY_DISPLAY_CALIBRATION": "0:0,broken",
            }
        )


def test_battery_display_service_calibrates_low_raw_pisugar_reading() -> None:
    battery = BatteryDisplayService().build(PowerStatus(2.0, False, "pisugar", True, "ok"))

    assert battery.raw_percent == 2.0
    assert battery.display_percent == 50
    assert battery.icon == "[##--]"
    assert battery.label == "[##--] 50%"


def test_runtime_config_implicitly_enables_whisplay_bundle_when_display_uses_vendor_adapter() -> None:
    config = load_runtime_config(
        {
            "DEVICE_ID": "raspi-1",
            "DEVICE_WS_URL": "ws://localhost/ws",
            "DEVICE_DISPLAY_ADAPTER": "whisplay",
        }
    )

    assert config.resolved_hardware_profile == "whisplay"
    assert config.button_adapter == "whisplay"
    assert config.rgb_adapter == "hardware"
    assert config.whisplay_bundle_active is True


def test_runtime_config_whisplay_defaults_wm8960_plughw_when_alsa_audio_enabled() -> None:
    config = load_runtime_config(
        {
            "DEVICE_ID": "raspi-1",
            "DEVICE_WS_URL": "ws://localhost/ws",
            "DEVICE_HARDWARE_PROFILE": "whisplay",
            "DEVICE_AUDIO_IN_ADAPTER": "alsa",
            "DEVICE_AUDIO_OUT_ADAPTER": "alsa",
        }
    )

    assert config.audio_in_alsa_device == "plughw:wm8960soundcard,0"
    assert config.audio_out_alsa_device == "plughw:wm8960soundcard,0"
    assert config.audio_out_chunk_ms == 200
    assert config.audio_out_start_buffer_ms == 1000
    assert any("WM8960 codec" in warning for warning in config.config_warnings)


def test_runtime_config_whisplay_profile_disables_conflicting_gpio_button() -> None:
    config = load_runtime_config(
        {
            "DEVICE_ID": "raspi-1",
            "DEVICE_WS_URL": "ws://localhost/ws",
            "DEVICE_HARDWARE_PROFILE": "whisplay",
            "DEVICE_BUTTON_ADAPTER": "gpio",
        }
    )

    assert config.display_adapter == "whisplay"
    assert config.button_adapter == "whisplay"
    assert any("GPIO17" in warning for warning in config.config_warnings)


def test_runtime_config_generic_profile_preserves_manual_adapter_selection() -> None:
    config = load_runtime_config(
        {
            "DEVICE_ID": "raspi-1",
            "DEVICE_WS_URL": "ws://localhost/ws",
            "DEVICE_HARDWARE_PROFILE": "generic",
            "DEVICE_DISPLAY_ADAPTER": "whisplay",
            "DEVICE_BUTTON_ADAPTER": "gpio",
            "DEVICE_RGB_ADAPTER": "none",
        }
    )

    assert config.resolved_hardware_profile == "generic"
    assert config.display_adapter == "whisplay"
    assert config.button_adapter == "gpio"
    assert config.rgb_adapter == "none"
    assert config.config_warnings == ()


def test_runtime_config_rejects_invalid_whisplay_backlight() -> None:
    with pytest.raises(ValueError, match="DEVICE_WHISPLAY_BACKLIGHT"):
        load_runtime_config(
            {
                "DEVICE_ID": "raspi-1",
                "DEVICE_WS_URL": "ws://localhost/ws",
                "DEVICE_WHISPLAY_BACKLIGHT": "120",
            }
        )


def test_runtime_config_rejects_invalid_integer_values() -> None:
    with pytest.raises(ValueError, match="DEVICE_AUDIO_CHUNK_MS"):
        load_runtime_config(
            {
                "DEVICE_ID": "raspi-1",
                "DEVICE_WS_URL": "ws://localhost/ws",
                "DEVICE_AUDIO_CHUNK_MS": "abc",
            }
        )


def test_runtime_config_accepts_legacy_playback_buffer_alias() -> None:
    config = load_runtime_config(
        {
            "DEVICE_ID": "raspi-1",
            "DEVICE_WS_URL": "ws://localhost/ws",
            "DEVICE_AUDIO_OUT_BUFFER_MS": "750",
        }
    )

    assert config.audio_out_start_buffer_ms == 750


def test_runtime_config_rejects_negative_alsa_buffer_values() -> None:
    with pytest.raises(ValueError, match="DEVICE_AUDIO_OUT_START_BUFFER_MS"):
        load_runtime_config(
            {
                "DEVICE_ID": "raspi-1",
                "DEVICE_WS_URL": "ws://localhost/ws",
                "DEVICE_AUDIO_OUT_START_BUFFER_MS": "-1",
            }
        )


def test_runtime_config_rejects_invalid_playback_chunk_size() -> None:
    with pytest.raises(ValueError, match="DEVICE_AUDIO_OUT_CHUNK_MS"):
        load_runtime_config(
            {
                "DEVICE_ID": "raspi-1",
                "DEVICE_WS_URL": "ws://localhost/ws",
                "DEVICE_AUDIO_OUT_CHUNK_MS": "0",
            }
        )


def test_runtime_config_requires_explicit_websocket_url() -> None:
    with pytest.raises(ValueError, match="standalone Raspberry deployment"):
        load_runtime_config({"DEVICE_ID": "raspi-1"})


def test_runtime_config_rejects_non_websocket_urls() -> None:
    with pytest.raises(ValueError, match="valid ws:// or wss:// URL"):
        load_runtime_config({"DEVICE_ID": "raspi-1", "DEVICE_WS_URL": "http://localhost/ws"})


def test_display_model_service_builds_incoming_call_view() -> None:
    snapshot = DeviceSnapshot(device_id="raspi-1", device_state=DeviceState.INCOMING_CALL)
    snapshot.warnings = ["audio_in unavailable"]
    snapshot.connected = True
    snapshot.diagnostics.transport_status = "connected"

    model = DisplayModelService().build(snapshot, PowerStatus(78.0, True, "pisugar", True, "ok"))

    assert model.local_state == "incoming_call"
    assert model.focus_label == "incoming_call"
    assert model.warnings == ["audio_in unavailable"]
    assert model.scene == "incoming-call"
    assert model.status_icon == "IN"
    assert model.battery_label == "[####] 100%+"
    assert model.battery_icon == "[####]"
    assert model.battery_percent == 100
    assert model.battery_raw_percent == 78.0
    assert model.network_label == "NET CONNECTED"
    assert model.center_title == "Incoming call"
    assert model.center_body == "Hold to answer"
    assert model.center_hint == "Release to speak"


def test_experience_service_builds_screen_and_rgb_from_single_snapshot() -> None:
    snapshot = DeviceSnapshot(device_id="raspi-1", device_state=DeviceState.CALLING)
    snapshot.connected = True
    snapshot.remote_ui_state = UiState.CALLING

    experience = ExperienceService().build(snapshot, PowerStatus(51.0, False, "pisugar", True, "ok"))

    assert experience.screen.scene == "calling"
    assert experience.rgb_signal.state == "off"
    assert experience.power.battery_percent == 51.0


def test_rgb_policy_prefers_disconnected_over_ready_state() -> None:
    snapshot = DeviceSnapshot(device_id="raspi-1", device_state=DeviceState.STANDBY)
    signal = RgbPolicyService().select(snapshot, PowerStatus(None, None, "pisugar", False, "offline"))

    assert signal.state == "off"
    assert signal.style == "solid"
    assert signal.color == (0, 0, 0)


def test_rgb_policy_only_lights_for_audio_outbound_and_incoming_call() -> None:
    snapshot = DeviceSnapshot(device_id="raspi-1", device_state=DeviceState.STANDBY)
    snapshot.connected = True

    ready = RgbPolicyService().select(snapshot, PowerStatus(80.0, False, "pisugar", True, "ok"))
    snapshot.audio_outbound_active = True
    sending = RgbPolicyService().select(snapshot, PowerStatus(80.0, False, "pisugar", True, "ok"))
    snapshot.audio_outbound_active = False
    snapshot.device_state = DeviceState.INCOMING_CALL
    incoming = RgbPolicyService().select(snapshot, PowerStatus(80.0, False, "pisugar", True, "ok"))

    assert ready.color == (0, 0, 0)
    assert sending.state == "audio_outbound"
    assert sending.color == (255, 214, 10)
    assert incoming.state == "incoming_call"


def test_cached_power_status_returns_last_value_without_blocking_every_publish() -> None:
    calls = 0

    class FakePower:
        def read_status(self) -> PowerStatus:
            nonlocal calls
            calls += 1
            return PowerStatus(63.0, False, "pisugar", True, "ok")

    cache = CachedPowerStatus(FakePower(), refresh_interval_s=60.0)
    first = cache.read_status()
    for _ in range(40):
        if calls:
            break
        asyncio.get_event_loop().run_until_complete(asyncio.sleep(0.01))
    second = cache.read_status()

    assert first.available is False
    assert second.battery_percent == 63.0
    assert calls == 1


def test_runtime_observer_repaints_when_background_battery_refresh_arrives() -> None:
    class DelayedPower:
        def __init__(self) -> None:
            self.ready = threading.Event()

        def read_status(self) -> PowerStatus:
            self.ready.wait(timeout=1.0)
            return PowerStatus(63.0, False, "pisugar-uds", True, "ok")

    runtime = build_runtime({"DEVICE_ID": "raspi-1", "DEVICE_WS_URL": "ws://localhost/ws"})
    display = FakeDisplay()
    runtime.display = display
    runtime.power = DelayedPower()
    runtime.audio_capture = NullAudioCapture()
    runtime.rgb = NullRgb()
    observer = RuntimeObserver(runtime)
    snapshot = runtime.snapshot
    snapshot.connected = True
    snapshot.diagnostics.transport_status = "connected"

    observer.publish(snapshot)
    assert len(display.rendered) == 1
    assert display.rendered[0].battery_percent is None

    runtime.power.ready.set()
    for _ in range(100):
        if len(display.rendered) >= 2:
            break
        time.sleep(0.01)

    assert len(display.rendered) >= 2
    assert display.rendered[-1].battery_percent == 100
    assert display.rendered[-1].battery_raw_percent == 63.0


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
    assert "power degraded: adapter=none" in runtime.snapshot.warnings
    assert "rgb degraded: adapter=none" in runtime.snapshot.warnings


def test_raspi_bootstrap_records_whisplay_profile_resolution_warnings() -> None:
    runtime = build_runtime(
        {
            "DEVICE_ID": "raspi-1",
            "DEVICE_WS_URL": "ws://localhost/ws",
            "DEVICE_DISPLAY_ADAPTER": "whisplay",
            "DEVICE_BUTTON_ADAPTER": "gpio",
        }
    )

    assert runtime.config.resolved_hardware_profile == "whisplay"
    assert runtime.snapshot.diagnostics.metadata["hardware_profile"] == "whisplay"
    assert any("vendor bundle already owns the button" in warning for warning in runtime.snapshot.warnings)


def test_raspi_bootstrap_applies_separate_capture_and_playback_audio_tuning() -> None:
    runtime = build_runtime(
        {
            "DEVICE_ID": "raspi-1",
            "DEVICE_WS_URL": "ws://localhost/ws",
            "DEVICE_AUDIO_IN_ADAPTER": "alsa",
            "DEVICE_AUDIO_OUT_ADAPTER": "alsa",
            "DEVICE_AUDIO_CHUNK_MS": "80",
            "DEVICE_AUDIO_OUT_CHUNK_MS": "200",
            "DEVICE_AUDIO_OUT_START_BUFFER_MS": "1000",
        }
    )

    assert runtime.config.audio_chunk_ms == 80
    assert runtime.config.audio_out_chunk_ms == 200
    assert runtime.config.audio_out_start_buffer_ms == 1000


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

    def end_session(self) -> None:
        self.stop_calls.append(False)


class FakeDisplay:
    def __init__(self) -> None:
        self.rendered: list[object] = []
        self.diagnostics: list[str] = []

    def render(self, model: object) -> None:
        self.rendered.append(model)

    def show_diagnostic(self, line: str) -> None:
        self.diagnostics.append(line)


class FakePower:
    def __init__(self) -> None:
        self.calls = 0

    def read_status(self) -> PowerStatus:
        self.calls += 1
        return PowerStatus(80.0, False, "pisugar", True, "ok")


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


class AudioFileTransport(FakeTransport):
    async def connect(self) -> None:
        assert self.connection_handler is not None
        assert self.message_handler is not None
        self.connection_handler("connected", None)
        self.message_handler(build_message("session.ready", session_id="session-1"))
        self.message_handler(
            build_message(
                "assistant.audio.file",
                audio_id="audio-1",
                url="/audio/audio-1",
                codec="pcm16",
                sample_rate=24000,
                channels=1,
                size_bytes=6,
                source="tts",
                loopback=False,
                expires_at=9999999999,
            )
        )
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
            power=NullPowerStatus(),
            rgb=NullRgb(),
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


@pytest.mark.asyncio
async def test_runtime_runner_downloads_audio_file_and_routes_pcm_to_playback() -> None:
    runtime = build_runtime({"DEVICE_ID": "raspi-1", "DEVICE_WS_URL": "ws://localhost/ws"})
    playback = FakePlayback()
    stop_event = asyncio.Event()
    runner = RuntimeRunner(
        RuntimeBootstrap(
            config=runtime.config,
            snapshot=runtime.snapshot,
            display=NullDisplay(),
            button=NullButton(),
            audio_capture=NullAudioCapture(),
            audio_playback=playback,
            power=NullPowerStatus(),
            rgb=NullRgb(),
            diagnostics=NullDiagnostics(),
        ),
        transport=AudioFileTransport(),
    )

    async def fake_play_audio_file(message):
        playback.start(sample_rate=message["sample_rate"], channels=message["channels"])
        playback.push(b"pcmraw")
        playback.end_session()
        runner._mark_playback_finished("assistant audio file played")

    runner._play_audio_file = fake_play_audio_file  # type: ignore[method-assign]

    task = asyncio.create_task(runner.run(stop_event=stop_event))
    await asyncio.sleep(0.05)
    stop_event.set()
    await asyncio.wait_for(task, timeout=1.0)

    assert playback.started_with == (24000, 1)
    assert playback.pushed == [b"pcmraw"]


def test_runtime_observer_avoids_duplicate_diagnostic_render_path() -> None:
    runtime = build_runtime({"DEVICE_ID": "raspi-1", "DEVICE_WS_URL": "ws://localhost/ws"})
    display = FakeDisplay()
    power = FakePower()
    runtime.display = display
    runtime.power = power
    runtime.audio_capture = NullAudioCapture()
    runtime.rgb = NullRgb()
    runtime.diagnostics = NullDiagnostics()
    observer = RuntimeObserver(runtime)
    snapshot = runtime.snapshot
    snapshot.connected = True
    snapshot.diagnostics.last_note = "transport ready"

    observer.publish(snapshot)

    assert len(display.rendered) >= 1
    assert display.diagnostics == []


def test_runtime_runner_clears_last_error_on_reconnect() -> None:
    # Without this, a stale disconnect detail keeps DisplayModelService stuck
    # in the "error" scene (red screen) forever after a successful reconnect.
    runtime = build_runtime({"DEVICE_ID": "raspi-1", "DEVICE_WS_URL": "ws://localhost/ws"})
    runtime.display = NullDisplay()
    runtime.button = NullButton()
    runtime.audio_capture = NullAudioCapture()
    runtime.audio_playback = FakePlayback()
    runtime.rgb = NullRgb()
    runtime.diagnostics = NullDiagnostics()
    runner = RuntimeRunner(
        RuntimeBootstrap(
            config=runtime.config,
            snapshot=runtime.snapshot,
            display=NullDisplay(),
            button=NullButton(),
            audio_capture=NullAudioCapture(),
            audio_playback=FakePlayback(),
            power=NullPowerStatus(),
            rgb=NullRgb(),
            diagnostics=NullDiagnostics(),
        ),
        transport=FakeTransport(),
    )

    runner._handle_connection_event("disconnected", "received 1012 (service restart)")
    assert runner.controller.snapshot.diagnostics.last_error == "received 1012 (service restart)"

    runner._handle_connection_event("connected", None)
    assert runner.controller.snapshot.diagnostics.last_error == ""
    assert runner.controller.snapshot.connected is True


def test_whisplay_rgb565_fast_and_slow_paths_match() -> None:
    pil_image = pytest.importorskip("PIL.Image")
    from device_runtime.infrastructure.display import whisplay_display as wd
    image = pil_image.new("RGB", (8, 4))
    image.putdata([
        (0, 0, 0), (255, 255, 255), (255, 0, 0), (0, 255, 0),
        (0, 0, 255), (12, 34, 56), (200, 100, 50), (8, 8, 8),
        (1, 2, 3), (255, 254, 253), (128, 128, 128), (64, 32, 16),
        (199, 88, 77), (0, 128, 255), (255, 128, 0), (33, 66, 99),
        (10, 20, 30), (40, 50, 60), (70, 80, 90), (100, 110, 120),
        (130, 140, 150), (160, 170, 180), (190, 200, 210), (220, 230, 240),
        (5, 5, 5), (95, 95, 95), (185, 185, 185), (245, 245, 245),
        (15, 200, 75), (60, 60, 240), (210, 30, 90), (170, 10, 250),
    ])
    display = wd.WhisplayDisplay(driver=object())

    original_np = wd._np
    try:
        wd._np = None
        pure_pixels = display._rgb565_pixels(image)
        wd._np = original_np
        fast_pixels = display._rgb565_pixels(image)
    finally:
        wd._np = original_np

    assert fast_pixels == pure_pixels
    assert len(fast_pixels) == 8 * 4 * 2
