"""Integration and degradation tests for shared runtime adapters."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from device_runtime.application.services.device_controller import DeviceController
from device_runtime.domain.events import DeviceState
from device_runtime.domain.state import DeviceSnapshot
from device_runtime.entrypoints.raspi_main import build_runtime
from device_runtime.infrastructure.audio.alsa_capture import AlsaCapture
from device_runtime.infrastructure.audio.alsa_playback import AlsaPlayback
from device_runtime.infrastructure.audio.null_audio import NullAudioCapture, NullAudioPlayback
from device_runtime.infrastructure.display.whisplay_display import WhisplayDisplay
from device_runtime.infrastructure.input.gpio_button import GpioButton
from device_runtime.infrastructure.input.keyboard_button import KeyboardButton


class FakeClock:
    def now(self) -> float:
        return 100.0


class FakeGateway:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def start_listen(self, turn_id: str) -> None:
        self.sent.append({"type": "recording.start", "turn_id": turn_id})

    async def stop_listen(self, turn_id: str) -> None:
        self.sent.append({"type": "recording.stop", "turn_id": turn_id})

    async def cancel_listen(self, turn_id: str | None) -> None:
        self.sent.append({"type": "recording.cancel", "turn_id": turn_id})

    async def send_audio_chunk(self, turn_id: str, chunk: dict[str, Any]) -> None:
        self.sent.append({"type": "audio.chunk", "turn_id": turn_id, **chunk})

    async def request_agents_version(self) -> None:
        self.sent.append({"type": "agents.version.request"})

    async def request_agents_list(self) -> None:
        self.sent.append({"type": "agents.list.request"})

    async def confirm_agent(self, agent_id: str) -> None:
        self.sent.append({"type": "agent.select", "agent_id": agent_id})


class FakeRoot:
    def __init__(self) -> None:
        self.bound: dict[str, Any] = {}

    def bind(self, key: str, callback: Any) -> None:
        self.bound[key] = callback


class FakeDriver:
    def __init__(self) -> None:
        self.rendered: list[Any] = []
        self.diagnostics: list[str] = []

    def render(self, model: Any) -> None:
        self.rendered.append(model)

    def show_diagnostic(self, line: str) -> None:
        self.diagnostics.append(line)


class FakeLineDriver:
    def __init__(self) -> None:
        self.lines: list[tuple[int, str]] = []
        self.cleared = 0
        self.presented = 0

    def clear(self) -> None:
        self.cleared += 1

    def draw_text(self, row: int, text: str) -> None:
        self.lines.append((row, text))

    def present(self) -> None:
        self.presented += 1


class FakeButtonDevice:
    def __init__(self, pin: int, bounce_time: float) -> None:
        self.pin = pin
        self.bounce_time = bounce_time
        self.when_pressed: Any = None
        self.when_held: Any = None
        self.hold_time: float | None = None
        self.closed = False

    def close(self) -> None:
        self.closed = True


class FakeTimer:
    def __init__(self, _interval_s: float, callback: Any) -> None:
        self.callback = callback
        self.cancelled = False

    def start(self) -> None:
        return None

    def cancel(self) -> None:
        self.cancelled = True


def test_keyboard_button_binds_default_keys_and_dispatches() -> None:
    root = FakeRoot()
    adapter = KeyboardButton(root)
    events: list[str] = []

    adapter.start(events.append)
    adapter.bind_default_keys()
    root.bound["<space>"](None)
    root.bound["<Escape>"](None)

    assert events == ["press", "long_press"]


def test_gpio_button_emits_single_press_after_timer() -> None:
    created: list[FakeButtonDevice] = []
    timers: list[FakeTimer] = []

    def factory(pin: int, bounce_time: float) -> FakeButtonDevice:
        device = FakeButtonDevice(pin=pin, bounce_time=bounce_time)
        created.append(device)
        return device

    def timer_factory(interval_s: float, callback: Any) -> FakeTimer:
        timer = FakeTimer(interval_s, callback)
        timers.append(timer)
        return timer

    adapter = GpioButton(pin=17, button_factory=factory, timer_factory=timer_factory, clock=lambda: 10.0)
    events: list[str] = []
    adapter.start(events.append)
    created[0].when_pressed()
    timers[0].callback()
    adapter.stop()

    assert events == ["press"]
    assert created[0].hold_time is not None
    assert created[0].closed is True


def test_gpio_button_collapses_double_press_before_timer_fires() -> None:
    created: list[FakeButtonDevice] = []

    def factory(pin: int, bounce_time: float) -> FakeButtonDevice:
        device = FakeButtonDevice(pin=pin, bounce_time=bounce_time)
        created.append(device)
        return device

    times = iter([10.0, 10.2])
    adapter = GpioButton(pin=17, button_factory=factory, timer_factory=FakeTimer, clock=lambda: next(times))
    events: list[str] = []
    adapter.start(events.append)
    created[0].when_pressed()
    created[0].when_pressed()

    assert events == ["double_press"]


def test_gpio_button_cancels_pending_press_when_long_press_arrives() -> None:
    created: list[FakeButtonDevice] = []
    timers: list[FakeTimer] = []

    def factory(pin: int, bounce_time: float) -> FakeButtonDevice:
        device = FakeButtonDevice(pin=pin, bounce_time=bounce_time)
        created.append(device)
        return device

    def timer_factory(interval_s: float, callback: Any) -> FakeTimer:
        timer = FakeTimer(interval_s, callback)
        timers.append(timer)
        return timer

    adapter = GpioButton(pin=17, button_factory=factory, timer_factory=timer_factory, clock=lambda: 10.0)
    events: list[str] = []
    adapter.start(events.append)
    created[0].when_pressed()
    assert created[0].when_held is not None
    created[0].when_held()
    timers[0].callback()

    assert events == ["long_press"]


def test_whisplay_display_uses_injected_driver_double() -> None:
    driver = FakeDriver()
    display = WhisplayDisplay(driver=driver)

    display.render({"state": "READY"})
    display.show_diagnostic("screen ok")

    assert driver.rendered == [{"state": "READY"}]
    assert driver.diagnostics == ["screen ok"]


def test_whisplay_display_can_render_line_oriented_driver() -> None:
    driver = FakeLineDriver()
    display = WhisplayDisplay(driver=driver)

    display.render(
        type(
            "Model",
            (),
            {
                "local_state": "READY",
                "remote_state": "idle",
                "active_agent": "assistant-general",
                "focus_label": "-",
                "mic_live": False,
                "connected": True,
                "transcript_preview": "hola mundo",
                "assistant_preview": "respuesta larga",
                "warnings": [],
            },
        )()
    )
    display.show_diagnostic("adapter ok")

    assert driver.cleared >= 1
    assert driver.presented >= 1
    assert any("READY" in text for _, text in driver.lines)
    assert any("adapter ok" in text for _, text in driver.lines)


def test_alsa_adapters_accept_injected_pcm_doubles() -> None:
    written: list[bytes] = []
    capture_reads = iter([(3, b"abc"), (0, b""), (0, b"")])

    class CapturePcm:
        def __init__(self) -> None:
            self.configured: list[tuple[str, object]] = []

        def setchannels(self, value: int) -> None:
            self.configured.append(("channels", value))

        def setrate(self, value: int) -> None:
            self.configured.append(("rate", value))

        def setperiodsize(self, value: int) -> None:
            self.configured.append(("period", value))

        def read(self) -> tuple[int, bytes]:
            return next(capture_reads)

    class PlaybackPcm:
        def __init__(self) -> None:
            self.configured: list[tuple[str, object]] = []

        def setchannels(self, value: int) -> None:
            self.configured.append(("channels", value))

        def setrate(self, value: int) -> None:
            self.configured.append(("rate", value))

        def write(self, payload: bytes) -> None:
            written.append(payload)

    capture = AlsaCapture(pcm_factory=CapturePcm)
    capture.start()
    chunks = capture.read_chunks(2)
    capture.stop()

    playback = AlsaPlayback(pcm_factory=PlaybackPcm)
    playback.start(sample_rate=16000, channels=1)
    playback.push(b"pcm")
    playback.stop(clear_buffer=False)

    assert chunks[0]["payload"] == "YWJj"
    assert chunks[0]["size_bytes"] == 3
    assert written == [b"pcm"]
    assert capture.available is True
    assert playback.available is True


def test_raspi_bootstrap_degrades_missing_real_adapters_without_import_failures() -> None:
    runtime = build_runtime(
        {
            "DEVICE_ID": "raspi-1",
            "DEVICE_WS_URL": "ws://localhost/ws",
            "DEVICE_DISPLAY_ADAPTER": "whisplay",
            "DEVICE_BUTTON_ADAPTER": "gpio",
            "DEVICE_AUDIO_IN_ADAPTER": "alsa",
            "DEVICE_AUDIO_OUT_ADAPTER": "alsa",
        }
    )

    assert isinstance(runtime.display, object)
    assert isinstance(runtime.button, object)
    assert isinstance(runtime.audio_capture, NullAudioCapture)
    assert isinstance(runtime.audio_playback, NullAudioPlayback)
    assert any("missing dependency" in warning for warning in runtime.snapshot.warnings)


async def test_null_audio_capture_keeps_runtime_smoke_safe() -> None:
    snapshot = DeviceSnapshot(device_id="raspi-1", device_state=DeviceState.LISTEN)
    snapshot.connected = True
    snapshot.turn_id = "turn-1"
    gateway = FakeGateway()
    controller = DeviceController(snapshot, gateway=gateway, clock=FakeClock())

    sent = await controller.flush_audio_capture(NullAudioCapture(), max_chunks=4)

    assert sent == 0
    assert gateway.sent == []
