"""Domain state models shared by runtime entrypoints."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from device_runtime.domain.capabilities import DeviceCapabilities
from device_runtime.domain.events import DeviceState
from device_runtime.protocol import UiState


DEFAULT_ACTIVE_AGENT = "assistant-general"


@dataclass(slots=True)
class RuntimeDiagnostics:
    """Operational state that stays outside the backend protocol boundary."""

    warnings: list[str] = field(default_factory=list)
    last_error: str = ""
    last_note: str = ""
    transport_status: str = "disconnected"
    adapter_statuses: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class DeviceSnapshot:
    """Shared device snapshot with compatibility helpers for simulator wrappers."""

    device_id: str
    device_state: DeviceState = DeviceState.STANDBY
    remote_ui_state: UiState = UiState.STANDBY
    listening_active: bool = False
    audio_outbound_active: bool = False
    playback_active: bool = False
    turn_id: str | None = None
    transcript: str = ""
    assistant_text: str = ""
    session_id: str = ""
    connected: bool = False
    active_agent: str = DEFAULT_ACTIVE_AGENT
    last_latency_ms: int | None = None
    battery_level: float = 82.0
    capabilities: DeviceCapabilities = field(default_factory=DeviceCapabilities)
    diagnostics: RuntimeDiagnostics = field(default_factory=RuntimeDiagnostics)

    @property
    def ui_state(self) -> UiState:
        return self.remote_ui_state

    @ui_state.setter
    def ui_state(self, value: UiState) -> None:
        self.remote_ui_state = value

    @property
    def warnings(self) -> list[str]:
        return self.diagnostics.warnings

    @warnings.setter
    def warnings(self, value: list[str]) -> None:
        self.diagnostics.warnings = list(value)

class SimulatorState(DeviceSnapshot):
    """Compatibility alias for CLI entrypoint state."""


class UiStateModel(DeviceSnapshot):
    """Compatibility alias for Tkinter entrypoint state."""
