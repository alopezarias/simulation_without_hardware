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
from device_runtime.domain.events import DeviceInputEvent, DeviceState
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

    async def start_call(self) -> None:
        self.sent.append({"type": "call.start"})

    async def send_audio_chunk(self, turn_id: str, chunk: dict[str, Any]) -> None:
        self.sent.append({"type": "audio.chunk", "turn_id": turn_id, **chunk})


class FakeObserver:
    def __init__(self) -> None:
        self.states: list[DeviceSnapshot] = []

    def publish(self, snapshot: DeviceSnapshot) -> None:
        self.states.append(snapshot)


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


class FakeMeasureDraw:
    def textlength(self, text: str, font: Any = None) -> int:
        widths = {"W": 12, "i": 4, ".": 3, " ": 4}
        return sum(widths.get(char, 8) for char in text)


class FakeButtonDevice:
    def __init__(self, pin: int, bounce_time: float) -> None:
        self.pin = pin
        self.bounce_time = bounce_time
        self.when_pressed: Any = None
        self.when_held: Any = None
        self.when_released: Any = None
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
    root.bound["<KeyPress-space>"](None)
    root.bound["<KeyRelease-space>"](None)
    root.bound["<Return>"](None)
    root.bound["<Escape>"](None)

    assert events == ["long_press", "release", "press", "double_press"]


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
    assert created[0].when_released is not None
    created[0].when_released()
    timers[0].callback()

    assert events == ["long_press", "release"]


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

    assert events == ["long_press", "release"]


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
                "status_icon": "MIC",
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
                "battery_label": "82%",
                "battery_percent": 82,
                "battery_charging": False,
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
    assert any("Ready" in text and "82%" in text for _, text in driver.lines)
    assert any("adapter ok" in text for _, text in driver.lines)


def test_whisplay_display_compacts_long_content_for_small_screen() -> None:
    display = WhisplayDisplay(driver=FakeDriver())
    model = type(
        "Model",
        (),
        {
            "local_state": "READY",
            "scene": "speaking",
            "status_icon": "OUT",
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
            "battery_label": "82%",
            "battery_percent": 82,
            "battery_charging": False,
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
    assert display.last_frame["top_row"].endswith("82%")
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
                "status_icon": "Zz",
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
                "battery_label": "82%",
                "battery_percent": 82,
                "battery_charging": False,
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
    snapshot = DeviceSnapshot(device_id="raspi-1", device_state=DeviceState.STANDBY)
    snapshot.connected = False
    snapshot.diagnostics.transport_status = "disconnected"
    snapshot.warnings = ["power unavailable: Battery unavailable"]

    model = DisplayModelService().build(snapshot, PowerStatus(None, None, "pisugar", False, "PiSugar unavailable"))

    assert model.scene == "disconnected"
    assert model.status_text == "Offline"
    assert model.battery_label == "--"
    assert model.diagnostics_label == ""
    assert model.warnings == []


def test_whisplay_display_skips_render_when_frame_does_not_change() -> None:
    driver = FakeLineDriver()
    display = WhisplayDisplay(driver=driver)
    model = type(
        "Model",
        (),
        {
            "local_state": "READY",
            "scene": "standby",
            "status_icon": "Zz",
            "status_text": "Standby",
            "status_detail": "Hold to talk",
            "center_title": "Ready",
            "center_body": "General",
            "center_hint": "Hold to talk · Press to call",
            "remote_state": "idle",
            "active_agent": "assistant-general",
            "focus_label": "standby",
            "mic_live": False,
            "connected": True,
            "network_label": "NET CONNECTED",
            "battery_label": "82%",
            "battery_percent": 82,
            "battery_charging": False,
            "diagnostics_label": "",
            "header_badges": ["NET CONNECTED", "82%"],
            "transcript_preview": "",
            "assistant_preview": "",
            "warnings": [],
        },
    )()

    display.render(model)
    first_presented = driver.presented
    display.render(model)

    assert driver.presented == first_presented


def test_experience_service_and_rgb_policy_align_on_incoming_call_state() -> None:
    snapshot = DeviceSnapshot(device_id="raspi-1", device_state=DeviceState.INCOMING_CALL)
    snapshot.connected = True
    snapshot.remote_ui_state = UiState.INCOMING_CALL

    experience = ExperienceService().build(snapshot, PowerStatus(88.0, False, "pisugar", True, "ok"))

    assert experience.screen.scene == "incoming-call"
    assert experience.rgb_signal == RgbPolicyService().select(snapshot, experience.power)


def test_whisplay_display_wraps_body_text_by_pixel_width_not_character_count() -> None:
    display = WhisplayDisplay(driver=FakeDriver())

    lines = display._wrap_text_pixels(  # noqa: SLF001
        FakeMeasureDraw(),
        "iiiiiiii WWW",
        object(),
        max_width=50,
        max_lines=2,
    )

    assert lines == ["iiiiiiii", "WWW"]


def test_pisugar_status_prefers_uds_before_tcp_in_auto_mode() -> None:
    sockets = iter([
        FakeSocket(["battery: 84.0\n"]),
        FakeSocket(["battery_charging: false\n"]),
    ])

    def unix_socket_factory(_path: str, _timeout: float) -> FakeSocket:
        return next(sockets)

    def socket_factory(_address: tuple[str, int], _timeout: float) -> socket.socket:
        raise AssertionError("TCP should not be used when UDS succeeds")

    status = PiSugarStatus(
        mode="auto",
        unix_socket_factory=unix_socket_factory,
        socket_factory=socket_factory,
    ).read_status()

    assert status.available is True
    assert status.source == "pisugar-uds"
    assert status.battery_percent == 84.0


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
    assert status.detail == "Battery unavailable"


def test_pisugar_status_falls_back_to_sysfs_after_socket_failures() -> None:
    def socket_factory(_address: tuple[str, int], _timeout: float) -> socket.socket:
        raise OSError("connection refused")

    def unix_socket_factory(_path: str, _timeout: float) -> socket.socket:
        raise FileNotFoundError("missing socket")

    def file_reader(path: str) -> str:
        if path.endswith("BAT0/capacity"):
            return "79\n"
        if path.endswith("BAT0/status"):
            return "Charging\n"
        raise FileNotFoundError(path)

    status = PiSugarStatus(
        socket_factory=socket_factory,
        unix_socket_factory=unix_socket_factory,
        file_reader=file_reader,
        mode="auto",
        sysfs_root="/fake/power_supply",
    ).read_status()

    assert status.available is True
    assert status.source == "pisugar-sysfs"
    assert status.battery_percent == 79.0
    assert status.charging is True


def test_pisugar_status_reads_sysfs_when_mode_is_explicit() -> None:
    def socket_factory(_address: tuple[str, int], _timeout: float) -> socket.socket:
        raise AssertionError("TCP should not be used in sysfs mode")

    def file_reader(path: str) -> str:
        if path.endswith("pisugar-battery/capacity"):
            return "64\n"
        if path.endswith("pisugar-battery/status"):
            return "Discharging\n"
        raise FileNotFoundError(path)

    status = PiSugarStatus(
        socket_factory=socket_factory,
        file_reader=file_reader,
        mode="sysfs",
        sysfs_root="/fake/power_supply",
    ).read_status()

    assert status.available is True
    assert status.source == "pisugar-sysfs"
    assert status.battery_percent == 64.0
    assert status.charging is False


def test_pisugar_status_keeps_battery_when_tcp_charging_probe_fails() -> None:
    sockets = iter([
        FakeSocket(["battery: 83.6\n"]),
    ])

    def socket_factory(_address: tuple[str, int], _timeout: float) -> FakeSocket:
        try:
            return next(sockets)
        except StopIteration as exc:
            raise OSError("charging unsupported") from exc

    status = PiSugarStatus(socket_factory=socket_factory, mode="tcp").read_status()

    assert status.available is True
    assert status.battery_percent == 83.6
    assert status.charging is None


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

        def setperiodsize(self, value: int) -> None:
            self.configured.append(("period", value))

        def write(self, payload: bytes) -> None:
            written.append(payload)

    capture = AlsaCapture(pcm_factory=CapturePcm, chunk_ms=80, period_size=512)
    capture.start()
    capture_pcm = capture._pcm
    chunks = capture.read_chunks(2)
    capture.stop()

    playback = AlsaPlayback(pcm_factory=PlaybackPcm, period_size=256, start_buffer_ms=0)
    playback.start(sample_rate=16000, channels=1)
    playback_pcm = playback._pcm
    playback.push(b"pcm")
    playback.end_session()
    playback.stop(clear_buffer=False)

    assert chunks[0]["payload"] == "YWJj"
    assert chunks[0]["size_bytes"] == 3
    assert written == [b"pcm"]
    assert capture.available is True
    assert playback.available is True
    assert capture_pcm is not None and ("period", 512) in capture_pcm.configured
    assert playback_pcm is not None and ("period", 256) in playback_pcm.configured


def test_alsa_playback_waits_for_prebuffer_before_writing() -> None:
    written: list[bytes] = []

    class PlaybackPcm:
        def setchannels(self, _value: int) -> None:
            return None

        def setrate(self, _value: int) -> None:
            return None

        def setperiodsize(self, _value: int) -> None:
            return None

        def write(self, payload: bytes) -> None:
            written.append(payload)

    playback = AlsaPlayback(pcm_factory=PlaybackPcm, period_size=4, chunk_ms=20, start_buffer_ms=40)
    playback.start(sample_rate=100, channels=1)
    playback.push(b"12")
    assert written == []

    playback.push(b"345678")
    assert written == [b"12345678"]


def test_alsa_playback_uses_larger_runtime_chunks_after_one_second_prebuffer() -> None:
    written: list[bytes] = []

    class PlaybackPcm:
        def setchannels(self, _value: int) -> None:
            return None

        def setrate(self, _value: int) -> None:
            return None

        def setperiodsize(self, _value: int) -> None:
            return None

        def write(self, payload: bytes) -> None:
            written.append(payload)

    playback = AlsaPlayback(pcm_factory=PlaybackPcm, chunk_ms=200, start_buffer_ms=1000)
    playback.start(sample_rate=10, channels=1)
    for payload in [b"aa", b"bb", b"cc", b"dd", b"ee", b"ff", b"gg", b"hh", b"ii"]:
        playback.push(payload)

    assert written == []

    playback.push(b"jj")

    assert written == [b"aabb", b"ccdd", b"eeff", b"gghh", b"iijj"]


def test_alsa_playback_fades_and_drains_final_chunk() -> None:
    written: list[bytes] = []
    drain_calls = 0

    class PlaybackPcm:
        def setchannels(self, _value: int) -> None:
            return None

        def setrate(self, _value: int) -> None:
            return None

        def setperiodsize(self, _value: int) -> None:
            return None

        def write(self, payload: bytes) -> None:
            written.append(payload)

        def drain(self) -> None:
            nonlocal drain_calls
            drain_calls += 1

    playback = AlsaPlayback(pcm_factory=PlaybackPcm, period_size=4, chunk_ms=2, start_buffer_ms=0)
    playback.start(sample_rate=1000, channels=1)
    playback.push(b"\x10\x00\x20\x00")
    playback.push(b"\x30\x00\x40\x00")
    playback.push(b"\x50\x00\x60\x00")

    playback.end_session()

    assert written[0] == b"\x10\x00\x20\x00\x30\x00\x40\x00"
    assert len(written[1]) == 16
    assert written[1][-8:] == b"\x00" * 8
    assert drain_calls == 1


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
    snapshot = DeviceSnapshot(device_id="raspi-1", device_state=DeviceState.LISTENING)
    snapshot.connected = True
    snapshot.turn_id = "turn-1"
    gateway = FakeGateway()
    controller = DeviceController(snapshot, gateway=gateway, clock=FakeClock())

    sent = await controller.flush_audio_capture(NullAudioCapture(), max_chunks=4)

    assert sent == 0
    assert gateway.sent == []


async def test_device_controller_marks_audio_outbound_only_while_chunks_are_sent() -> None:
    class Capture:
        available = True

        def __init__(self) -> None:
            self.calls = 0

        def read_chunks(self, max_chunks: int) -> list[dict[str, Any]]:
            self.calls += 1
            if self.calls == 1:
                return [{"payload": "YWJj", "size_bytes": 3}]
            return []

    snapshot = DeviceSnapshot(device_id="raspi-1", device_state=DeviceState.LISTENING)
    snapshot.connected = True
    snapshot.turn_id = "turn-1"
    gateway = FakeGateway()
    observer = FakeObserver()
    controller = DeviceController(snapshot, gateway=gateway, clock=FakeClock(), observer=observer)
    capture = Capture()

    sent = await controller.flush_audio_capture(capture, max_chunks=2)
    assert sent == 1
    assert controller.snapshot.audio_outbound_active is True

    sent = await controller.flush_audio_capture(capture, max_chunks=2)
    assert sent == 0
    assert controller.snapshot.audio_outbound_active is False
    assert len(observer.states) >= 2


async def test_device_controller_hold_to_talk_starts_on_hold_and_stops_on_release() -> None:
    snapshot = DeviceSnapshot(device_id="raspi-1", device_state=DeviceState.STANDBY)
    snapshot.connected = True
    snapshot.session_id = "session-1"
    gateway = FakeGateway()
    controller = DeviceController(snapshot, gateway=gateway, clock=FakeClock())

    started = await controller.handle_input(DeviceInputEvent.LONG_PRESS)
    finished = await controller.handle_input(DeviceInputEvent.RELEASE)

    assert started.snapshot.device_state == DeviceState.LISTENING
    assert started.snapshot.listening_active is True
    assert gateway.sent[0]["type"] == "recording.start"
    assert finished.snapshot.device_state == DeviceState.STANDBY
    assert finished.snapshot.listening_active is False
    assert gateway.sent[1]["type"] == "recording.stop"


async def test_device_controller_single_press_starts_call_and_audio_end_returns_to_standby() -> None:
    snapshot = DeviceSnapshot(device_id="raspi-1", device_state=DeviceState.STANDBY)
    snapshot.connected = True
    snapshot.session_id = "session-1"
    gateway = FakeGateway()
    controller = DeviceController(snapshot, gateway=gateway, clock=FakeClock())

    started = await controller.handle_input(DeviceInputEvent.PRESS)
    await controller.handle_backend_message({"type": "assistant.audio.start"})
    ended = await controller.handle_backend_message({"type": "assistant.audio.end"})

    assert started.snapshot.device_state == DeviceState.CALLING
    assert gateway.sent == [{"type": "call.start"}]
    assert ended.snapshot.device_state == DeviceState.STANDBY
    assert ended.snapshot.playback_active is False


async def test_incoming_call_preempts_config_and_hold_to_talk_still_works() -> None:
    snapshot = DeviceSnapshot(device_id="raspi-1", device_state=DeviceState.STANDBY)
    snapshot.connected = True
    snapshot.session_id = "session-1"
    gateway = FakeGateway()
    controller = DeviceController(snapshot, gateway=gateway, clock=FakeClock())

    config = await controller.handle_input(DeviceInputEvent.DOUBLE_PRESS)
    incoming = await controller.handle_backend_message({"type": "incoming_call"})
    listening = await controller.handle_input(DeviceInputEvent.LONG_PRESS)
    released = await controller.handle_input(DeviceInputEvent.RELEASE)

    assert config.snapshot.device_state == DeviceState.CONFIG
    assert incoming.snapshot.device_state == DeviceState.INCOMING_CALL
    assert listening.snapshot.device_state == DeviceState.LISTENING
    assert released.snapshot.device_state == DeviceState.STANDBY
    assert [message["type"] for message in gateway.sent] == ["recording.start", "recording.stop"]
