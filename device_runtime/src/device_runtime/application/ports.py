"""Shared runtime ports for transport, peripherals and state observation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol

from device_runtime.domain.capabilities import DeviceCapabilities
from device_runtime.domain.state import DeviceSnapshot


class TransportPort(Protocol):
    async def connect(self) -> None: ...

    async def send(self, message: dict[str, Any]) -> None: ...

    def set_message_handler(self, handler: Callable[[dict[str, Any]], None]) -> None: ...

    def set_connection_handler(self, handler: Callable[[str, str | None], None]) -> None: ...

    async def close(self) -> None: ...


class DisplayPort(Protocol):
    def render(self, model: Any) -> None: ...

    def show_diagnostic(self, line: str) -> None: ...


class ButtonInputPort(Protocol):
    def start(self, on_event: Callable[[str], None]) -> None: ...

    def stop(self) -> None: ...


class AudioCapturePort(Protocol):
    def start(self) -> None: ...

    def stop(self) -> None: ...

    def read_chunks(self, max_chunks: int) -> list[dict[str, Any]]: ...

    @property
    def available(self) -> bool: ...


class AudioPlaybackPort(Protocol):
    def start(self, sample_rate: int, channels: int) -> None: ...

    def push(self, pcm_bytes: bytes) -> None: ...

    def stop(self, clear_buffer: bool = True) -> None: ...

    @property
    def available(self) -> bool: ...


class DiagnosticsPort(Protocol):
    def record(self, event: str, **data: Any) -> None: ...


@dataclass(slots=True)
class PowerStatus:
    battery_percent: float | None
    charging: bool | None
    source: str
    available: bool
    detail: str = ""


@dataclass(slots=True)
class RgbSignal:
    state: str
    color: tuple[int, int, int]
    style: str = "solid"
    detail: str = ""


class PowerStatusPort(Protocol):
    def read_status(self) -> PowerStatus: ...


class RgbPort(Protocol):
    def apply(self, signal: RgbSignal) -> None: ...

    def clear(self) -> None: ...


class CapabilityProvider(Protocol):
    def detect(self) -> DeviceCapabilities: ...


class BackendGateway(Protocol):
    async def start_listen(self, turn_id: str) -> None: ...

    async def stop_listen(self, turn_id: str) -> None: ...

    async def cancel_listen(self, turn_id: str | None) -> None: ...

    async def start_call(self) -> None: ...

    async def send_audio_chunk(self, turn_id: str, chunk: dict[str, Any]) -> None: ...


class Clock(Protocol):
    def now(self) -> float: ...


class StateObserver(Protocol):
    def publish(self, snapshot: DeviceSnapshot) -> None: ...


class WakeWordPort(Protocol):
    """Passive wake-word detection; fires callback on detection, then stops."""

    def start(self, on_wake: Callable[[], None]) -> None: ...

    def stop(self) -> None: ...

    @property
    def available(self) -> bool: ...


class NoteCaptureGateway(Protocol):
    """Posts audio to the note-taker backend."""

    async def upload(
        self,
        audio_bytes: bytes,
        capture_mode: str,
        *,
        sample_rate: int = 16000,
        channels: int = 1,
    ) -> str:
        """Upload WAV audio. Returns note_id string (may be empty on failure)."""
        ...
