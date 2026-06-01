"""Note-taker runtime entrypoint for Raspberry Pi."""

from __future__ import annotations

import asyncio
import copy
import logging
from dataclasses import dataclass
from typing import Any

from device_runtime.application.services.note_taker_config import NoteTakerConfig
from device_runtime.application.services.note_taker_state_machine import (
    NoteTakerStateMachine,
    NoteTakerTransition,
)
from device_runtime.domain.note_taker_state import (
    CaptureMode,
    NoteTakerEvent,
    NoteTakerMode,
    NoteTakerState,
)
from device_runtime.infrastructure.audio.alsa_capture import AlsaCapture
from device_runtime.infrastructure.audio.null_audio import NullAudioCapture
from device_runtime.infrastructure.audio.sounddevice_capture import (
    SoundDeviceCapture,
    sounddevice_is_available,
)
from device_runtime.infrastructure.capture.audio_buffer import AudioBuffer
from device_runtime.infrastructure.capture.http_note_gateway import HttpNoteCaptureGateway
from device_runtime.infrastructure.capture.null_note_gateway import NullNoteCaptureGateway
from device_runtime.infrastructure.capture.offline_queue import OfflineQueue
from device_runtime.infrastructure.config.note_taker_env_loader import load_note_taker_config
from device_runtime.infrastructure.display.null_display import NullDisplay
from device_runtime.infrastructure.display.whisplay_display import WhisplayDisplay
from device_runtime.infrastructure.input.gpio_button import GpioButton
from device_runtime.infrastructure.input.keyboard_button import KeyboardButton
from device_runtime.infrastructure.input.null_button import NullButton
from device_runtime.infrastructure.input.whisplay_button import WhisplayButton
from device_runtime.infrastructure.power.pisugar_status import NullPowerStatus, PiSugarStatus
from device_runtime.infrastructure.rgb.hardware_rgb import HardwareRgb
from device_runtime.infrastructure.rgb.null_rgb import NullRgb
from device_runtime.infrastructure.wake_word.null_wake_word import NullWakeWord

logger = logging.getLogger(__name__)


@dataclass
class NoteTakerBootstrap:
    config: NoteTakerConfig
    display: Any
    button: Any
    audio_capture: Any
    power: Any
    rgb: Any
    wake_word: Any
    note_gateway: Any
    offline_queue: OfflineQueue


class NoteTakerRunner:
    """Orchestrates button events and wake-word detection → audio capture → HTTP upload."""

    def __init__(self, bootstrap: NoteTakerBootstrap) -> None:
        self._bt = bootstrap
        self._state = NoteTakerState()
        self._machine = NoteTakerStateMachine()
        self._buffer = AudioBuffer()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()
        self._timeout_task: asyncio.Task[None] | None = None
        self._drain_task: asyncio.Task[None] | None = None

    @property
    def state(self) -> NoteTakerState:
        return copy.deepcopy(self._state)

    async def run(self, *, stop_event: asyncio.Event | None = None) -> None:
        self._loop = asyncio.get_running_loop()
        if self._bt.wake_word.available:
            self._bt.wake_word.start(self._on_wake_word)
        self._bt.button.start(self._on_button_event)
        self._drain_task = asyncio.create_task(self._offline_drain_loop())
        try:
            while True:
                if stop_event is not None and stop_event.is_set():
                    break
                try:
                    event_type, payload = await asyncio.wait_for(
                        self._queue.get(), timeout=0.05
                    )
                except asyncio.TimeoutError:
                    await self._drain_audio()
                    continue
                if event_type == "button":
                    await self._handle_button(payload)
                elif event_type == "wake_word":
                    await self._handle_event(NoteTakerEvent.WAKE_WORD)
                elif event_type == "recording_done":
                    await self._handle_event(NoteTakerEvent.RECORDING_DONE)
        finally:
            await self.stop()

    async def stop(self) -> None:
        self._bt.wake_word.stop()
        self._bt.button.stop()
        self._bt.audio_capture.stop()
        await self._cancel_timeout()
        if self._drain_task is not None and not self._drain_task.done():
            self._drain_task.cancel()
            try:
                await self._drain_task
            except asyncio.CancelledError:
                pass

    # ── event ingestion ───────────────────────────────────────────────────────

    def _on_wake_word(self) -> None:
        self._enqueue("wake_word", None)

    def _on_button_event(self, event_name: str) -> None:
        self._enqueue("button", event_name)

    def _enqueue(self, event_type: str, payload: Any) -> None:
        loop = self._loop
        if loop is None or not loop.is_running():
            return
        loop.call_soon_threadsafe(self._queue.put_nowait, (event_type, payload))

    # ── button → note-taker event mapping ────────────────────────────────────

    _BUTTON_MAP: dict[str, NoteTakerEvent] = {
        "press": NoteTakerEvent.TAP,
        "long_press": NoteTakerEvent.LONG_PRESS,
        "release": NoteTakerEvent.RELEASE,
    }

    async def _handle_button(self, event_name: str) -> None:
        nt_event = self._BUTTON_MAP.get(event_name)
        if nt_event is not None:
            await self._handle_event(nt_event)

    # ── state machine application ─────────────────────────────────────────────

    async def _handle_event(self, event: NoteTakerEvent) -> None:
        transition = self._machine.handle(self._state, event)
        self._state = transition.state
        logger.debug("note-taker %s → %s [%s]", event.value, self._state.mode.value, transition.action)
        await self._apply_action(transition)

    async def _apply_action(self, transition: NoteTakerTransition) -> None:
        if transition.action == "start_recording":
            await self._start_recording(transition.capture_mode or CaptureMode.MANUAL)
        elif transition.action == "stop_and_upload":
            await self._stop_and_upload(transition.capture_mode or CaptureMode.MANUAL)

    # ── recording lifecycle ───────────────────────────────────────────────────

    async def _start_recording(self, capture_mode: CaptureMode) -> None:
        self._buffer.clear()
        if getattr(self._bt.audio_capture, "available", False):
            self._bt.audio_capture.start()
        await self._cancel_timeout()
        if capture_mode == CaptureMode.WAKE_WORD:
            self._timeout_task = asyncio.create_task(self._recording_timeout())

    async def _stop_and_upload(self, capture_mode: CaptureMode) -> None:
        await self._cancel_timeout()
        # Drain before stopping — real ALSA/sounddevice adapters discard buffered
        # frames once stop() is called, so we must collect first.
        self._buffer.collect(self._bt.audio_capture, max_chunks=1000)
        self._bt.audio_capture.stop()
        pcm = self._buffer.to_pcm_bytes()
        if not pcm:
            logger.warning("note-taker: recording produced no audio; skipping upload")
            return
        wav_bytes = self._buffer.to_wav_bytes(
            sample_rate=self._bt.config.audio_sample_rate,
            channels=self._bt.config.audio_channels,
        )
        try:
            note_id = await self._bt.note_gateway.upload(
                pcm,
                capture_mode.value,
                sample_rate=self._bt.config.audio_sample_rate,
                channels=self._bt.config.audio_channels,
            )
            logger.info("note-taker: uploaded note_id=%s mode=%s", note_id, capture_mode.value)
        except Exception as exc:
            logger.error("note-taker: upload failed, queuing for retry: %s", exc)
            self._bt.offline_queue.save(wav_bytes, capture_mode.value)

    async def _offline_drain_loop(self) -> None:
        interval = self._bt.config.offline_queue_drain_interval_s
        while True:
            await asyncio.sleep(interval)
            if self._bt.offline_queue.pending_count == 0:
                continue
            try:
                uploaded, failed = await self._bt.offline_queue.drain(
                    self._bt.note_gateway.upload_wav
                )
                if uploaded:
                    logger.info("offline_queue: drained %d note(s), %d still pending", uploaded, failed)
            except Exception:
                logger.exception("offline_queue: drain loop error")

    async def _drain_audio(self) -> None:
        if self._state.mode == NoteTakerMode.RECORDING:
            self._buffer.collect(self._bt.audio_capture)

    async def _recording_timeout(self) -> None:
        await asyncio.sleep(self._bt.config.silence_timeout_s)
        self._enqueue("recording_done", None)

    async def _cancel_timeout(self) -> None:
        task = self._timeout_task
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._timeout_task = None


# ── bootstrap ─────────────────────────────────────────────────────────────────

def build_note_taker(env: dict[str, str] | None = None) -> NoteTakerBootstrap:
    config = load_note_taker_config(env)
    display = _resolve_display(config)
    button = _resolve_button(config, display=display)
    audio_capture = _resolve_audio_capture(config)
    power = _resolve_power(config)
    rgb = _resolve_rgb(config, display=display)
    wake_word = _resolve_wake_word(config)
    note_gateway = _resolve_note_gateway(config)
    offline_queue = OfflineQueue(config.offline_queue_dir)
    return NoteTakerBootstrap(
        config=config,
        display=display,
        button=button,
        audio_capture=audio_capture,
        power=power,
        rgb=rgb,
        wake_word=wake_word,
        note_gateway=note_gateway,
        offline_queue=offline_queue,
    )


def build_runner(env: dict[str, str] | None = None, **overrides: Any) -> NoteTakerRunner:
    bootstrap = build_note_taker(env)
    for key, value in overrides.items():
        if hasattr(bootstrap, key):
            object.__setattr__(bootstrap, key, value)
    return NoteTakerRunner(bootstrap)


def _resolve_display(config: NoteTakerConfig) -> Any:
    if config.display_adapter == "whisplay":
        display = WhisplayDisplay(driver_path=config.whisplay_driver_path, backlight=config.whisplay_backlight)
        if display.available:
            return display
    return NullDisplay()


def _resolve_button(config: NoteTakerConfig, *, display: Any) -> Any:
    adapter = config.button_adapter
    if adapter == "whisplay" or config.whisplay_bundle_active:
        board_provider = getattr(display, "get_board", None)
        board = None
        if callable(board_provider):
            try:
                board = board_provider()
            except Exception:
                board = None
        btn = WhisplayButton(
            board=board,
            board_provider=board_provider if callable(board_provider) else None,
            long_press_ms=config.button_long_press_ms,
            double_press_ms=config.button_double_press_ms,
        )
        if board is not None:
            return btn
        return NullButton()
    if adapter == "keyboard":
        return KeyboardButton()
    if adapter == "gpio":
        btn = GpioButton(pin=17, long_press_ms=config.button_long_press_ms, double_press_ms=config.button_double_press_ms)
        if btn.available:
            return btn
    return NullButton()


def _resolve_audio_capture(config: NoteTakerConfig) -> Any:
    adapter = config.audio_in_adapter
    if adapter == "sounddevice" and sounddevice_is_available():
        return SoundDeviceCapture(
            sample_rate=config.audio_sample_rate,
            channels=config.audio_channels,
            chunk_ms=config.audio_chunk_ms,
        )
    if adapter == "alsa":
        cap = AlsaCapture(
            sample_rate=config.audio_sample_rate,
            channels=config.audio_channels,
            chunk_ms=config.audio_chunk_ms,
            device=config.audio_in_alsa_device,
            period_size=config.audio_in_alsa_period_size,
            nonblock=config.audio_in_alsa_nonblock,
        )
        if cap.available:
            return cap
    return NullAudioCapture()


def _resolve_power(config: NoteTakerConfig) -> Any:
    if config.power_adapter == "pisugar":
        return PiSugarStatus()
    return NullPowerStatus()


def _resolve_rgb(config: NoteTakerConfig, *, display: Any) -> Any:
    if config.rgb_adapter == "hardware":
        controller = getattr(display, "get_rgb_controller", lambda: None)()
        rgb = HardwareRgb(controller=controller, driver_path=config.whisplay_driver_path)
        if rgb.available:
            return rgb
    return NullRgb()


def _resolve_wake_word(config: NoteTakerConfig) -> Any:
    return NullWakeWord()


def _resolve_note_gateway(config: NoteTakerConfig) -> Any:
    return HttpNoteCaptureGateway(
        config.note_api_url,
        device_id=config.device_id,
        api_token=config.note_api_token,
    )


async def async_main() -> None:
    logging.basicConfig(level=logging.INFO)
    runner = build_runner()
    await runner.run()


def main() -> None:
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
