"""Bootstrap helpers and runtime loop for the Raspberry Pi entrypoint."""

from __future__ import annotations

import asyncio
import base64
import copy
from dataclasses import dataclass
import time
from typing import Any

from device_runtime.application.ports import BackendGateway, PowerStatus, StateObserver
from device_runtime.application.services.device_controller import DeviceController
from device_runtime.application.services.diagnostics_service import DiagnosticsService
from device_runtime.application.services.experience_service import ExperienceService
from device_runtime.application.services.runtime_config import RuntimeConfig
from device_runtime.domain.events import DeviceInputEvent, DeviceState
from device_runtime.domain.capabilities import CapabilityState, CapabilityStatus, DeviceCapabilities
from device_runtime.domain.state import DeviceSnapshot
from device_runtime.infrastructure.audio.alsa_capture import AlsaCapture
from device_runtime.infrastructure.audio.alsa_playback import AlsaPlayback
from device_runtime.infrastructure.audio.null_audio import NullAudioCapture, NullAudioPlayback
from device_runtime.infrastructure.audio.sounddevice_capture import SoundDeviceCapture, sounddevice_is_available
from device_runtime.infrastructure.audio.sounddevice_playback import SoundDevicePlayback
from device_runtime.infrastructure.config.env_loader import load_runtime_config
from device_runtime.infrastructure.diagnostics.null_diagnostics import NullDiagnostics
from device_runtime.infrastructure.display.null_display import NullDisplay
from device_runtime.infrastructure.display.whisplay_display import WhisplayDisplay
from device_runtime.infrastructure.input.gpio_button import GpioButton
from device_runtime.infrastructure.input.keyboard_button import KeyboardButton
from device_runtime.infrastructure.input.null_button import NullButton
from device_runtime.infrastructure.input.whisplay_button import WhisplayButton
from device_runtime.infrastructure.power.pisugar_status import NullPowerStatus, PiSugarStatus
from device_runtime.infrastructure.rgb.hardware_rgb import HardwareRgb
from device_runtime.infrastructure.rgb.null_rgb import NullRgb
from device_runtime.infrastructure.transport.websocket_client import WebSocketTransport
from device_runtime.protocol import MessageType, UiState, build_message


@dataclass(slots=True)
class RuntimeBootstrap:
    config: RuntimeConfig
    snapshot: DeviceSnapshot
    display: Any
    button: Any
    audio_capture: Any
    audio_playback: Any
    power: Any
    rgb: Any
    diagnostics: NullDiagnostics


class MonotonicClock:
    def now(self) -> float:
        return time.monotonic()


class RuntimeObserver(StateObserver):
    def __init__(self, runtime: RuntimeBootstrap) -> None:
        self._runtime = runtime
        self._experience_service = ExperienceService()
        self._was_listening = False
        self._playback_started = False

    def publish(self, snapshot: DeviceSnapshot) -> None:
        listening = snapshot.device_state == DeviceState.LISTEN and snapshot.listening_active
        if listening and not self._was_listening:
            if getattr(self._runtime.audio_capture, "available", False):
                self._runtime.audio_capture.start()
        if not listening and self._was_listening:
            self._runtime.audio_capture.stop()
        self._was_listening = listening

        power_status = self._safe_power_status()
        experience = self._experience_service.build(snapshot, power_status)
        self._runtime.display.render(experience.screen)
        self._apply_rgb(experience.rgb_signal.state, experience.rgb_signal)
        note = snapshot.diagnostics.last_error or snapshot.diagnostics.last_note
        if note:
            self._runtime.display.show_diagnostic(note)

        remote_state = str(snapshot.remote_ui_state.value)
        if remote_state == "speaking" and getattr(self._runtime.audio_playback, "available", False):
            if not self._playback_started:
                self._runtime.audio_playback.start(
                    sample_rate=self._runtime.config.audio_sample_rate,
                    channels=self._runtime.config.audio_channels,
                )
                self._playback_started = True
        elif self._playback_started:
            self._runtime.audio_playback.stop(clear_buffer=False)
            self._playback_started = False

    def _safe_power_status(self) -> PowerStatus:
        try:
            return self._runtime.power.read_status()
        except Exception as exc:
            return PowerStatus(None, None, "power", False, f"power degraded: {exc}")

    def _apply_rgb(self, _state: str, signal: Any) -> None:
        try:
            self._runtime.rgb.apply(signal)
        except Exception as exc:
            self._runtime.display.show_diagnostic(f"rgb degraded: {exc}")


class RuntimeRunner:
    def __init__(self, runtime: RuntimeBootstrap, transport: Any | None = None) -> None:
        self.runtime = runtime
        self.transport = transport or build_transport(runtime)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()
        self._transport_task: asyncio.Task[None] | None = None
        self._observer = RuntimeObserver(runtime)
        self._controller = DeviceController(
            runtime.snapshot,
            gateway=build_gateway(runtime, self.transport),
            clock=MonotonicClock(),
            observer=self._observer,
            diagnostics=runtime.diagnostics,
        )
        self.transport.set_message_handler(lambda message: self._enqueue("backend", message))
        self.transport.set_connection_handler(lambda status, detail: self._enqueue("connection", (status, detail)))

    @property
    def controller(self) -> DeviceController:
        return self._controller

    async def run(self, *, stop_event: asyncio.Event | None = None) -> None:
        self._loop = asyncio.get_running_loop()
        self._observer.publish(self._controller.snapshot)
        self.runtime.button.start(self._on_button_event)
        self._transport_task = asyncio.create_task(self.transport.connect())
        try:
            while True:
                if stop_event is not None and stop_event.is_set():
                    break
                try:
                    event_type, payload = await asyncio.wait_for(self._queue.get(), timeout=0.1)
                except asyncio.TimeoutError:
                    await self._controller.flush_audio_capture(self.runtime.audio_capture)
                    continue
                if event_type == "button":
                    await self._controller.handle_input(payload)
                elif event_type == "backend":
                    await self._controller.handle_backend_message(payload)
                    self._handle_backend_audio(payload)
                elif event_type == "connection":
                    self._handle_connection_event(*payload)
                await self._controller.flush_audio_capture(self.runtime.audio_capture)
        finally:
            await self.stop()

    async def stop(self) -> None:
        self.runtime.button.stop()
        self.runtime.audio_capture.stop()
        self.runtime.audio_playback.stop(clear_buffer=False)
        self.transport.close()
        task = self._transport_task
        if task is not None:
            try:
                await asyncio.wait_for(task, timeout=3.0)
            except Exception:
                task.cancel()
        self._transport_task = None

    def _on_button_event(self, event_name: str) -> None:
        try:
            event = DeviceInputEvent(event_name)
        except ValueError:
            self.runtime.diagnostics.record("button.ignored", event_name=event_name)
            return
        self._enqueue("button", event)

    def _enqueue(self, event_type: str, payload: Any) -> None:
        loop = self._loop
        if loop is None:
            return
        loop.call_soon_threadsafe(self._queue.put_nowait, (event_type, payload))

    def _handle_connection_event(self, status: str, detail: str | None) -> None:
        snapshot = copy.deepcopy(self._controller.snapshot)
        status = status.strip()
        snapshot.diagnostics.transport_status = status or snapshot.diagnostics.transport_status
        snapshot.connected = status == "connected"
        if status == "disconnected":
            snapshot.session_id = ""
            snapshot.remote_ui_state = UiState.IDLE
            snapshot.listening_active = False
            snapshot.turn_id = None
            if snapshot.device_state == DeviceState.LISTEN:
                snapshot.device_state = DeviceState.READY
            if detail:
                snapshot.diagnostics.last_error = detail
        self._controller.replace_snapshot(snapshot)

    def _handle_backend_audio(self, message: dict[str, Any]) -> None:
        message_type = str(message.get("type", "")).strip()
        playback = self.runtime.audio_playback
        if not getattr(playback, "available", False):
            return
        if message_type == "assistant.audio.start":
            sample_rate = _safe_int(message.get("sample_rate"), self.runtime.config.audio_sample_rate)
            channels = _safe_int(message.get("channels"), self.runtime.config.audio_channels)
            playback.start(sample_rate=sample_rate, channels=channels)
            return
        if message_type == "assistant.audio.chunk":
            payload = message.get("payload")
            if not isinstance(payload, str) or not payload:
                return
            try:
                pcm_bytes = base64.b64decode(payload, validate=True)
            except Exception:
                self._record_warning("assistant.audio.chunk invalid")
                return
            if not pcm_bytes:
                self._record_warning("assistant.audio.chunk invalid")
                return
            if not getattr(playback, "started", False):
                playback.start(
                    sample_rate=_safe_int(message.get("sample_rate"), self.runtime.config.audio_sample_rate),
                    channels=_safe_int(message.get("channels"), self.runtime.config.audio_channels),
                )
            playback.push(pcm_bytes)
            return
        if message_type == "assistant.audio.end":
            end_session = getattr(playback, "end_session", None)
            if callable(end_session):
                end_session()

    def _record_warning(self, warning: str) -> None:
        snapshot = copy.deepcopy(self._controller.snapshot)
        if warning not in snapshot.warnings:
            snapshot.warnings = [*snapshot.warnings, warning]
        snapshot.diagnostics.last_note = warning
        self._controller.replace_snapshot(snapshot)


class RuntimeTransportGateway(BackendGateway):
    def __init__(self, transport: Any, *, sample_rate: int, channels: int) -> None:
        self._transport = transport
        self._sample_rate = sample_rate
        self._channels = channels

    async def start_listen(self, turn_id: str) -> None:
        await self._transport.send(
            build_message(
                MessageType.RECORDING_START,
                turn_id=turn_id,
                codec="pcm16",
                sample_rate=self._sample_rate,
                channels=self._channels,
            )
        )

    async def stop_listen(self, turn_id: str) -> None:
        await self._transport.send(build_message(MessageType.RECORDING_STOP, turn_id=turn_id))

    async def cancel_listen(self, turn_id: str | None) -> None:
        payload: dict[str, Any] = {}
        if turn_id:
            payload["turn_id"] = turn_id
        await self._transport.send(build_message(MessageType.RECORDING_CANCEL, **payload))

    async def send_audio_chunk(self, turn_id: str, chunk: dict[str, Any]) -> None:
        await self._transport.send(build_message(MessageType.AUDIO_CHUNK, turn_id=turn_id, **chunk))

    async def request_agents_version(self) -> None:
        await self._transport.send(build_message(MessageType.AGENTS_VERSION_REQUEST))

    async def request_agents_list(self) -> None:
        await self._transport.send(build_message(MessageType.AGENTS_LIST_REQUEST))

    async def confirm_agent(self, agent_id: str) -> None:
        await self._transport.send(build_message(MessageType.AGENT_SELECT, agent_id=agent_id))


def build_runtime(env: dict[str, str] | None = None) -> RuntimeBootstrap:
    config = load_runtime_config(env)
    display, screen_capability = _resolve_display(config)
    button, button_capability = _resolve_button(config, display=display)
    audio_capture, audio_in_capability = _resolve_audio_capture(config)
    audio_playback, audio_out_capability = _resolve_audio_playback(config)
    power, power_capability = _resolve_power(config)
    rgb, rgb_capability = _resolve_rgb(config, display=display)
    capabilities = DeviceCapabilities(
        screen=screen_capability,
        button=button_capability,
        audio_in=audio_in_capability,
        audio_out=audio_out_capability,
        transport=CapabilityState("transport", CapabilityStatus.ENABLED, f"adapter={config.transport_adapter}"),
        extras={"power": power_capability, "rgb": rgb_capability},
    )
    snapshot = DeviceSnapshot(device_id=config.device_id)
    snapshot.capabilities = capabilities
    snapshot.diagnostics.metadata["hardware_profile"] = config.resolved_hardware_profile
    snapshot.diagnostics.metadata["hardware_profile_requested"] = config.hardware_profile
    DiagnosticsService().refresh_snapshot(snapshot, capabilities=capabilities, transport_status="configured")
    if config.config_warnings:
        snapshot.diagnostics.warnings = list(
            dict.fromkeys([*snapshot.diagnostics.warnings, *config.config_warnings])
        )
        snapshot.diagnostics.last_note = config.config_warnings[0]
    return RuntimeBootstrap(
        config=config,
        snapshot=snapshot,
        display=display,
        button=button,
        audio_capture=audio_capture,
        audio_playback=audio_playback,
        power=power,
        rgb=rgb,
        diagnostics=NullDiagnostics(),
    )


def build_hello_payload(runtime: RuntimeBootstrap) -> dict[str, Any]:
    snapshot = runtime.snapshot
    payload = build_message(
        MessageType.DEVICE_HELLO,
        device_id=runtime.config.device_id,
        firmware_version=runtime.config.firmware_version,
        simulated=False,
        capabilities=_declared_capabilities(snapshot.capabilities),
        active_agent=snapshot.active_agent,
    )
    if runtime.config.auth_token:
        payload["auth_token"] = runtime.config.auth_token
    return payload


def build_transport(
    runtime: RuntimeBootstrap,
    *,
    connect_factory: Any | None = None,
    keepalive_interval_s: float = 15.0,
) -> WebSocketTransport:
    if runtime.config.transport_adapter.strip().lower() != "websocket":
        raise RuntimeError(f"Unsupported transport adapter: {runtime.config.transport_adapter}")
    return WebSocketTransport(
        runtime.config.ws_url,
        hello_payload=build_hello_payload(runtime),
        reconnect_initial_ms=runtime.config.reconnect_initial_ms,
        reconnect_max_ms=runtime.config.reconnect_max_ms,
        keepalive_interval_s=keepalive_interval_s,
        connect_factory=connect_factory,
    )


def build_gateway(runtime: RuntimeBootstrap, transport: Any) -> RuntimeTransportGateway:
    return RuntimeTransportGateway(
        transport,
        sample_rate=runtime.config.audio_sample_rate,
        channels=runtime.config.audio_channels,
    )


def build_runner(
    env: dict[str, str] | None = None,
    *,
    transport: Any | None = None,
) -> RuntimeRunner:
    runtime = build_runtime(env)
    return RuntimeRunner(runtime, transport=transport)


def _declared_capabilities(capabilities: DeviceCapabilities) -> list[str]:
    declared: list[str] = []
    mapping = {
        "screen": capabilities.screen,
        "button": capabilities.button,
        "audio_in": capabilities.audio_in,
        "audio_out": capabilities.audio_out,
    }
    for name, capability in mapping.items():
        if capability.status != CapabilityStatus.UNAVAILABLE:
            declared.append(name)
    return declared


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _resolve_display(config: RuntimeConfig) -> tuple[Any, CapabilityState]:
    adapter = config.display_adapter.strip().lower()
    if adapter == "null":
        return NullDisplay(), CapabilityState("screen", CapabilityStatus.DEGRADED, "adapter=null")
    if adapter == "whisplay":
        display = WhisplayDisplay(
            driver_path=config.whisplay_driver_path,
            backlight=config.whisplay_backlight,
        )
        if not display.available:
            return NullDisplay(), CapabilityState("screen", CapabilityStatus.UNAVAILABLE, "adapter=whisplay missing dependency")
        return display, CapabilityState("screen", CapabilityStatus.ENABLED, "adapter=whisplay")
    return NullDisplay(), CapabilityState("screen", CapabilityStatus.DEGRADED, f"adapter={adapter} unsupported")


def _resolve_button(config: RuntimeConfig, *, display: Any | None = None) -> tuple[Any, CapabilityState]:
    adapter = config.button_adapter.strip().lower()
    if adapter == "whisplay" or config.whisplay_bundle_active:
        board_provider = getattr(display, "get_board", None)
        board = None
        if callable(board_provider):
            try:
                board = board_provider()
            except Exception:
                board = None
        button = WhisplayButton(
            board=board,
            board_provider=board_provider if callable(board_provider) else None,
            long_press_ms=config.button_long_press_ms,
            double_press_ms=config.button_double_press_ms,
        )
        if board is not None and hasattr(board, "on_button_press") and hasattr(board, "on_button_release"):
            return button, CapabilityState("button", CapabilityStatus.ENABLED, "adapter=whisplay")
        if config.fail_fast_on_missing_button:
            raise RuntimeError("Whisplay button adapter requires the vendor Whisplay board bindings")
        return NullButton(), CapabilityState("button", CapabilityStatus.UNAVAILABLE, "adapter=whisplay missing dependency")
    if adapter == "null":
        return NullButton(), CapabilityState("button", CapabilityStatus.DEGRADED, "adapter=null")
    if adapter == "keyboard":
        return KeyboardButton(), CapabilityState("button", CapabilityStatus.ENABLED, "adapter=keyboard")
    if adapter == "gpio":
        button = GpioButton(
            pin=17,
            long_press_ms=config.button_long_press_ms,
            double_press_ms=config.button_double_press_ms,
        )
        if button.available:
            return button, CapabilityState("button", CapabilityStatus.ENABLED, "adapter=gpio")
        if config.fail_fast_on_missing_button:
            raise RuntimeError("GPIO button adapter requires gpiozero on Raspberry Pi")
        return NullButton(), CapabilityState("button", CapabilityStatus.UNAVAILABLE, "adapter=gpio missing dependency")
    return NullButton(), CapabilityState("button", CapabilityStatus.DEGRADED, f"adapter={adapter} unsupported")


def _resolve_audio_capture(config: RuntimeConfig) -> tuple[Any, CapabilityState]:
    adapter = config.audio_in_adapter.strip().lower()
    if adapter == "null":
        return NullAudioCapture(), CapabilityState("audio_in", CapabilityStatus.DEGRADED, "adapter=null")
    if adapter == "sounddevice":
        if not sounddevice_is_available():
            return NullAudioCapture(), CapabilityState("audio_in", CapabilityStatus.UNAVAILABLE, "adapter=sounddevice missing dependency")
        return (
            SoundDeviceCapture(
                sample_rate=config.audio_sample_rate,
                channels=config.audio_channels,
                chunk_ms=config.audio_chunk_ms,
            ),
            CapabilityState("audio_in", CapabilityStatus.ENABLED, "adapter=sounddevice"),
        )
    if adapter == "alsa":
        capture = AlsaCapture(sample_rate=config.audio_sample_rate, channels=config.audio_channels)
        if not capture.available:
            return NullAudioCapture(), CapabilityState("audio_in", CapabilityStatus.UNAVAILABLE, "adapter=alsa missing dependency")
        return capture, CapabilityState("audio_in", CapabilityStatus.ENABLED, "adapter=alsa")
    return NullAudioCapture(), CapabilityState("audio_in", CapabilityStatus.DEGRADED, f"adapter={adapter} unsupported")


def _resolve_audio_playback(config: RuntimeConfig) -> tuple[Any, CapabilityState]:
    adapter = config.audio_out_adapter.strip().lower()
    if adapter == "null":
        return NullAudioPlayback(), CapabilityState("audio_out", CapabilityStatus.DEGRADED, "adapter=null")
    if adapter == "sounddevice":
        if not sounddevice_is_available():
            return NullAudioPlayback(), CapabilityState("audio_out", CapabilityStatus.UNAVAILABLE, "adapter=sounddevice missing dependency")
        return SoundDevicePlayback(), CapabilityState("audio_out", CapabilityStatus.ENABLED, "adapter=sounddevice")
    if adapter == "alsa":
        playback = AlsaPlayback()
        if not playback.available:
            return NullAudioPlayback(), CapabilityState("audio_out", CapabilityStatus.UNAVAILABLE, "adapter=alsa missing dependency")
        return playback, CapabilityState("audio_out", CapabilityStatus.ENABLED, "adapter=alsa")
    return NullAudioPlayback(), CapabilityState("audio_out", CapabilityStatus.DEGRADED, f"adapter={adapter} unsupported")


def _resolve_power(config: RuntimeConfig) -> tuple[Any, CapabilityState]:
    adapter = config.power_adapter.strip().lower()
    if adapter in {"none", "null", "disabled"}:
        return NullPowerStatus(), CapabilityState("power", CapabilityStatus.DEGRADED, f"adapter={adapter}")
    if adapter == "pisugar":
        power = PiSugarStatus(
            mode=config.pisugar_mode,
            host=config.pisugar_host,
            port=config.pisugar_port,
            command=config.pisugar_command,
        )
        status = power.read_status()
        if status.available:
            return power, CapabilityState("power", CapabilityStatus.ENABLED, f"adapter=pisugar source={status.source}")
        return power, CapabilityState("power", CapabilityStatus.UNAVAILABLE, status.detail or "adapter=pisugar unavailable")
    return NullPowerStatus(), CapabilityState("power", CapabilityStatus.DEGRADED, f"adapter={adapter} unsupported")


def _resolve_rgb(config: RuntimeConfig, *, display: Any) -> tuple[Any, CapabilityState]:
    adapter = config.rgb_adapter.strip().lower()
    if adapter in {"none", "null", "disabled"}:
        return NullRgb(), CapabilityState("rgb", CapabilityStatus.DEGRADED, f"adapter={adapter}")
    if adapter == "hardware":
        controller = getattr(display, "get_rgb_controller", lambda: None)()
        rgb = HardwareRgb(controller=controller, driver_path=config.whisplay_driver_path)
        if rgb.available:
            return rgb, CapabilityState("rgb", CapabilityStatus.ENABLED, f"adapter=hardware profile={config.rgb_profile}")
        return NullRgb(), CapabilityState("rgb", CapabilityStatus.UNAVAILABLE, "adapter=hardware missing dependency")
    return NullRgb(), CapabilityState("rgb", CapabilityStatus.DEGRADED, f"adapter={adapter} unsupported")


async def async_main() -> None:
    runner = build_runner()
    await runner.run()


def main() -> None:
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
