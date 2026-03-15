"""Domain state models shared by runtime entrypoints."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from backend.shared.protocol import UiState
from device_runtime.domain.capabilities import DeviceCapabilities
from device_runtime.domain.events import DeviceState, MenuOption


DEFAULT_AGENTS = ["assistant-general", "assistant-tech", "assistant-ops"]


@dataclass(slots=True)
class AgentCatalogCache:
    """Cached agent catalog owned by the runtime."""

    agents: list[str] = field(default_factory=lambda: list(DEFAULT_AGENTS))
    version: str = ""
    expires_at: float | None = None
    loaded_at: float | None = None


@dataclass(slots=True)
class NavigationState:
    """Local navigation and selection context for the device."""

    menu_options: list[str] = field(default_factory=lambda: [MenuOption.MODE.value])
    menu_index: int = 0
    available_modes: list[str] = field(default_factory=lambda: ["conversation"])
    mode_index: int = 0
    active_mode: str = "conversation"
    active_agent_id: str = "assistant-general"
    focused_agent_index: int = 0


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
    device_state: DeviceState = DeviceState.LOCKED
    navigation: NavigationState = field(default_factory=NavigationState)
    agent_cache: AgentCatalogCache = field(default_factory=AgentCatalogCache)
    remote_ui_state: UiState = UiState.IDLE
    listening_active: bool = False
    turn_id: str | None = None
    transcript: str = ""
    assistant_text: str = ""
    session_id: str = ""
    connected: bool = False
    agents_version: str = ""
    pending_agent_ack: str | None = None
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

    @property
    def agents(self) -> list[str]:
        return self.agent_cache.agents

    @agents.setter
    def agents(self, value: list[str]) -> None:
        normalized = [agent for agent in value if agent]
        self.agent_cache.agents = normalized
        if not self.agent_cache.agents:
            self.navigation.focused_agent_index = 0
            return
        if self.navigation.focused_agent_index >= len(self.agent_cache.agents):
            self.navigation.focused_agent_index = 0
        if self.navigation.active_agent_id not in self.agent_cache.agents:
            self.navigation.active_agent_id = self.agent_cache.agents[0]

    @property
    def agent_index(self) -> int:
        return self.navigation.focused_agent_index

    @agent_index.setter
    def agent_index(self, value: int) -> None:
        if not self.agent_cache.agents:
            self.agent_cache.agents = ["assistant-general"]
        size = len(self.agent_cache.agents)
        self.navigation.focused_agent_index = max(0, value) % size
        self.navigation.active_agent_id = self.agent_cache.agents[self.navigation.focused_agent_index]

    @property
    def active_agent(self) -> str:
        if not self.agent_cache.agents:
            return self.navigation.active_agent_id or "assistant-general"
        if self.navigation.active_agent_id in self.agent_cache.agents:
            return self.navigation.active_agent_id
        return self.agent_cache.agents[0]

    @property
    def focused_agent(self) -> str:
        if not self.agent_cache.agents:
            return ""
        return self.agent_cache.agents[self.navigation.focused_agent_index]

    def set_agent(self, agent_id: str) -> None:
        if not agent_id:
            return
        if agent_id not in self.agent_cache.agents:
            self.agent_cache.agents.append(agent_id)
        self.navigation.active_agent_id = agent_id
        self.navigation.focused_agent_index = self.agent_cache.agents.index(agent_id)


class SimulatorState(DeviceSnapshot):
    """Compatibility alias for CLI entrypoint state."""


class UiStateModel(DeviceSnapshot):
    """Compatibility alias for Tkinter entrypoint state."""
