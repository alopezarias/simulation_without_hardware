"""Integration and degradation tests for shared runtime adapters."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
import socket
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from device_runtime.application.ports import PowerStatus, RgbSignal
from device_runtime.application.services.display_model_service import DisplayModelService
from device_runtime.application.services.device_controller import DeviceController
from device_runtime.application.services.experience_service import ExperienceService
from device_runtime.application.services.rgb_policy_service import RgbPolicyService
from device_runtime.domain.events import DeviceState
from device_runtime.domain.state import DeviceSnapshot
from device_runtime.entrypoints.raspi_main import build_runtime
from device_runtime.infrastructure.audio.alsa_capture import AlsaCapture
from device_runtime.infrastructure.audio.alsa_playback import AlsaPlayback
from device_runtime.infrastructure.audio.null_audio import NullAudioCapture, NullAudioPlayback
from device_runtime.infrastructure.power.pisugar_status import PiSugarStatus
from device_runtime.infrastructure.display.whisplay_display import WhisplayDisplay
from device_runtime.infrastructure.input.gpio_button import GpioButton
from device_runtime.infrastructure.input.null_button import NullButton
from device_runtime.infrastructure.input.whisplay_button import WhisplayButton
from device_runtime.infrastructure.input.keyboard_button import KeyboardButton
from device_runtime.infrastructure.rgb.hardware_rgb import HardwareRgb
from device_runtime.infrastructure.rgb.null_rgb import NullRgb
from device_runtime.protocol import UiState


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


class FakeRgbController:
    def __init__(self) -> None:
        self.colors: list[tuple[str, tuple[int, int, int]]] = []

    def set_rgb(self, red: int, green: int, blue: int) -> None:
        self.colors.append(("solid", (red, green, blue)))

    def set_rgb_fade(self, red: int, green: int, blue: int, duration_ms: int = 250) -> None:
        self.colors.append((f"fade:{duration_ms}", (red, green, blue)))


class FakeVendorBoard(FakeRgbController):
    LCD_WIDTH = 24
    LCD_HEIGHT = 32

    def __init__(self) -> None:
        super().__init__()
        self.images: list[tuple[int, int, int, int, list[int]]] = []
        self.backlight: list[int] = []

    def draw_image(self, x: int, y: int, width: int, height: int, pixel_data: list[int]) -> None:
        self.images.append((x, y, width, height, pixel_data))

    def set_backlight(self, value: int) -> None:
        self.backlight.append(value)

    def on_button_press(self, callback: Any) -> None:
        self.button_press_callback = callback

    def on_button_release(self, callback: Any) -> None:
        self.button_release_callback = callback

    def press(self) -> None:
        callback = getattr(self, "button_press_callback", None)
        if callable(callback):
            callback()

    def release(self) -> None:
        callback = getattr(self, "button_release_callback", None)
        if callable(callback):
            callback()


class FakeSocket:
    def __init__(self, responses: list[str]) -> None:
        self._responses = [response.encode("utf-8") for response in responses]
        self.sent: list[str] = []
        self.closed = False

    def settimeout(self, _timeout: float) -> None:
        return None

    def sendall(self, payload: bytes) -> None:
        self.sent.append(payload.decode("utf-8"))

    def recv(self, _size: int) -> bytes:
        if not self._responses:
            return b""
        return self._responses.pop(0)

    def close(self) -> None:
        self.closed = True


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


def test_whisplay_button_emits_single_press_after_release_timer() -> None:
    board = FakeVendorBoard()
    timers: list[FakeTimer] = []

    def timer_factory(interval_s: float, callback: Any) -> FakeTimer:
        timer = FakeTimer(interval_s, callback)
        timers.append(timer)
        return timer

    adapter = WhisplayButton(board=board, timer_factory=timer_factory, clock=lambda: 10.0)
    events: list[str] = []

    adapter.start(events.append)
    board.press()
    board.release()
    timers[-1].callback()

    assert events == ["press"]


def test_whisplay_button_collapses_double_press() -> None:
    board = FakeVendorBoard()
    times = iter([10.0, 10.2])
    adapter = WhisplayButton(board=board, timer_factory=FakeTimer, clock=lambda: next(times))
    events: list[str] = []

    adapter.start(events.append)
    board.press()
    board.release()
    board.press()
    board.release()

    assert events == ["double_press"]


def test_whisplay_button_emits_long_press_without_followup_click() -> None:
    board = FakeVendorBoard()
    timers: list[FakeTimer] = []

    def timer_factory(interval_s: float, callback: Any) -> FakeTimer:
        timer = FakeTimer(interval_s, callback)
        timers.append(timer)
        return timer

    adapter = WhisplayButton(board=board, timer_factory=timer_factory, clock=lambda: 10.0)
    events: list[str] = []

    adapter.start(events.append)
    board.press()
    timers[0].callback()
    board.release()

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
                "scene": "ready",
                "status_text": "Ready",
                "status_detail": "Press to talk",
                "center_title": "Press to talk",
                "center_body": "General",
                "center_hint": "NET CONNECTED",
                "remote_state": "idle",
                "active_agent": "assistant-general",
                "focus_label": "-",
                "mic_live": False,
                "connected": True,
                "network_label": "NET CONNECTED",
                "battery_label": "BAT 82%",
                "diagnostics_label": "Runtime healthy",
                "header_badges": ["NET CONNECTED", "BAT 82%"],
                "transcript_preview": "hola mundo",
                "assistant_preview": "respuesta larga",
                "warnings": [],
            },
        )()
    )
    display.show_diagnostic("adapter ok")

    assert driver.cleared >= 1
    assert driver.presented >= 1
    assert any("Ready" in text and "BAT 82%" in text for _, text in driver.lines)
    assert any("adapter ok" in text for _, text in driver.lines)


def test_whisplay_display_compacts_long_content_for_small_screen() -> None:
    display = WhisplayDisplay(driver=FakeDriver())
    model = type(
        "Model",
        (),
        {
            "local_state": "READY",
            "scene": "speaking",
            "status_text": "Speaking",
            "status_detail": "Assistant audio live",
            "center_title": "Respuesta en curso",
            "center_body": "assistant-general",
            "center_hint": "Audio playing",
            "remote_state": "speaking",
            "active_agent": "assistant-general",
            "focus_label": "conversation",
            "mic_live": False,
            "connected": True,
            "network_label": "NET CONNECTED",
            "battery_label": "BAT 82%",
            "diagnostics_label": "Runtime healthy",
            "header_badges": ["NET CONNECTED", "BAT 82%"],
            "transcript_label": "YOU",
            "assistant_label": "AI",
            "transcript_preview": "hola mundo desde la Raspberry con mucho texto de prueba para compactar bien",
            "assistant_preview": "respuesta larga del asistente que debe priorizarse y mostrarse en pocas lineas claras",
            "footer": "speaking | connected",
            "warnings": [],
        },
    )()

    display.render(model)

    assert display.last_frame is not None
    assert len(display.last_frame["lines"]) <= 6
    assert display.last_frame["lines"][0].startswith("Speaking")
    assert display.last_frame["top_row"].endswith("BAT 82%")
    assert display.last_frame["center_title"] == "Respuesta en curso"


def test_whisplay_display_loads_vendor_whisplay_board_from_driver_path(tmp_path: Path) -> None:
    sys.modules.pop("WhisPlay", None)
    driver_dir = tmp_path / "Driver"
    driver_dir.mkdir()
    module_path = driver_dir / "WhisPlay.py"
    module_path.write_text(
        "class WhisPlayBoard:\n"
        "    LCD_WIDTH = 24\n"
        "    LCD_HEIGHT = 32\n"
        "    def __init__(self):\n"
        "        self.images = []\n"
        "        self.backlight = []\n"
        "    def set_backlight(self, value):\n"
        "        self.backlight.append(value)\n"
        "    def set_rgb(self, red, green, blue):\n"
        "        self.last_rgb = (red, green, blue)\n"
        "    def draw_image(self, x, y, width, height, pixel_data):\n"
        "        self.images.append((x, y, width, height, pixel_data))\n",
        encoding="utf-8",
    )

    display = WhisplayDisplay(driver_path=str(driver_dir), backlight=65)
    display.render(
        type(
            "Model",
            (),
            {
                "local_state": "READY",
                "scene": "ready",
                "status_text": "Ready",
                "status_detail": "Press to talk",
                "center_title": "Press to talk",
                "center_body": "General",
                "center_hint": "NET CONNECTED",
                "remote_state": "idle",
                "active_agent": "assistant-general",
                "focus_label": "-",
                "mic_live": False,
                "connected": True,
                "network_label": "NET CONNECTED",
                "battery_label": "BAT 82%",
                "diagnostics_label": "Runtime healthy",
                "header_badges": ["NET CONNECTED", "BAT 82%"],
                "transcript_preview": "hola mundo",
                "assistant_preview": "respuesta larga",
                "warnings": [],
            },
        )()
    )

    board = display.get_rgb_controller()
    assert board is not None
    assert board.backlight == [65]
    assert len(board.images) == 1
    assert board.images[0][2:4] == (24, 32)
    assert len(board.images[0][4]) == 24 * 32 * 2
    sys.modules.pop("WhisPlay", None)


def test_display_model_service_surfaces_disconnected_battery_unavailable_copy() -> None:
    snapshot = DeviceSnapshot(device_id="raspi-1", device_state=DeviceState.READY)
    snapshot.connected = False
    snapshot.diagnostics.transport_status = "disconnected"

    model = DisplayModelService().build(snapshot, PowerStatus(None, None, "pisugar", False, "PiSugar unavailable"))

    assert model.scene == "disconnected"
    assert model.status_text == "Offline"
    assert model.battery_label == "BAT --"
    assert model.diagnostics_label == "PiSugar unavailable"


def test_experience_service_and_rgb_policy_align_on_speaking_state() -> None:
    snapshot = DeviceSnapshot(device_id="raspi-1", device_state=DeviceState.READY)
    snapshot.connected = True
    snapshot.remote_ui_state = UiState.SPEAKING

    experience = ExperienceService().build(snapshot, PowerStatus(88.0, False, "pisugar", True, "ok"))

    assert experience.screen.scene == "speaking"
    assert experience.rgb_signal == RgbPolicyService().select(snapshot, experience.power)


def test_pisugar_status_reads_tcp_battery_and_charge_state() -> None:
    sockets = iter([
        FakeSocket(["battery: 83.6\n"]),
        FakeSocket(["battery_charging: true\n"]),
    ])

    def socket_factory(_address: tuple[str, int], _timeout: float) -> FakeSocket:
        return next(sockets)

    status = PiSugarStatus(socket_factory=socket_factory, mode="tcp").read_status()

    assert status.available is True
    assert status.source == "pisugar-tcp"
    assert status.battery_percent == 83.6
    assert status.charging is True


def test_pisugar_status_degrades_cleanly_when_tcp_unavailable() -> None:
    def socket_factory(_address: tuple[str, int], _timeout: float) -> socket.socket:
        raise OSError("connection refused")

    status = PiSugarStatus(socket_factory=socket_factory, mode="tcp").read_status()

    assert status.available is False
    assert "connection refused" in status.detail


def test_hardware_rgb_uses_fade_for_pulse_and_null_rgb_tracks_last_signal() -> None:
    controller = FakeRgbController()
    hardware = HardwareRgb(controller=controller)
    null_rgb = NullRgb()

    signal = RgbSignal("speaking", (64, 180, 255), style="pulse")
    hardware.apply(signal)
    null_rgb.apply(signal)
    null_rgb.clear()

    assert controller.colors == [("fade:250", (64, 180, 255))]
    assert null_rgb.last_signal is not None
    assert null_rgb.last_signal.state == "off"


def test_hardware_rgb_loads_vendor_whisplay_board_from_driver_path(tmp_path: Path) -> None:
    sys.modules.pop("WhisPlay", None)
    driver_dir = tmp_path / "Driver"
    driver_dir.mkdir()
    module_path = driver_dir / "WhisPlay.py"
    module_path.write_text(
        "class WhisPlayBoard:\n"
        "    def __init__(self):\n"
        "        self.colors = []\n"
        "    def set_rgb(self, red, green, blue):\n"
        "        self.colors.append((red, green, blue))\n",
        encoding="utf-8",
    )

    hardware = HardwareRgb(driver_path=str(driver_dir))
    hardware.apply(RgbSignal("ready", (10, 20, 30)))

    controller = hardware._ensure_controller()
    assert controller.colors == [(10, 20, 30)]
    sys.modules.pop("WhisPlay", None)


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
            "DEVICE_POWER_ADAPTER": "pisugar",
            "DEVICE_RGB_ADAPTER": "hardware",
        }
    )

    assert isinstance(runtime.display, object)
    assert isinstance(runtime.button, NullButton)
    assert isinstance(runtime.audio_capture, NullAudioCapture)
    assert isinstance(runtime.audio_playback, NullAudioPlayback)
    assert isinstance(runtime.power, PiSugarStatus)
    assert isinstance(runtime.rgb, object)
    assert any("missing dependency" in warning for warning in runtime.snapshot.warnings)
    assert any("vendor bundle already owns the button" in warning for warning in runtime.snapshot.warnings)


async def test_null_audio_capture_keeps_runtime_smoke_safe() -> None:
    snapshot = DeviceSnapshot(device_id="raspi-1", device_state=DeviceState.LISTEN)
    snapshot.connected = True
    snapshot.turn_id = "turn-1"
    gateway = FakeGateway()
    controller = DeviceController(snapshot, gateway=gateway, clock=FakeClock())

    sent = await controller.flush_audio_capture(NullAudioCapture(), max_chunks=4)

    assert sent == 0
    assert gateway.sent == []
