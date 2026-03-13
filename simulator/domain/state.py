"""Domain state models shared by simulator entrypoints."""

from __future__ import annotations

from dataclasses import dataclass, field

from simulator.shared.protocol import UiState


DEFAULT_AGENTS = ["assistant-general", "assistant-tech", "assistant-ops"]


@dataclass
class SimulatorState:
    """Conversation state used by the CLI simulator."""

    device_id: str
    agents: list[str] = field(default_factory=lambda: list(DEFAULT_AGENTS))
    agent_index: int = 0
    ui_state: UiState = UiState.IDLE
    turn_id: str | None = None
    transcript: str = ""
    assistant_text: str = ""
    session_id: str = ""
    connected: bool = False

    @property
    def active_agent(self) -> str:
        if not self.agents:
            return "assistant-general"
        return self.agents[self.agent_index]

    def set_agent(self, agent_id: str) -> None:
        if not self.agents:
            self.agents = [agent_id]
            self.agent_index = 0
            return

        if agent_id in self.agents:
            self.agent_index = self.agents.index(agent_id)
            return

        self.agents.append(agent_id)
        self.agent_index = len(self.agents) - 1


@dataclass
class UiStateModel:
    """Conversation and UI state used by the Tkinter simulator."""

    device_id: str
    agents: list[str] = field(default_factory=lambda: list(DEFAULT_AGENTS))
    agent_index: int = 0
    ui_state: UiState = UiState.IDLE
    turn_id: str | None = None
    transcript: str = ""
    assistant_text: str = ""
    session_id: str = ""
    connected: bool = False
    last_latency_ms: int | None = None
    battery_level: float = 82.0

    @property
    def active_agent(self) -> str:
        if not self.agents:
            return "assistant-general"
        return self.agents[self.agent_index]

    def set_agent(self, agent_id: str) -> None:
        if not self.agents:
            self.agents = [agent_id]
            self.agent_index = 0
            return

        if agent_id in self.agents:
            self.agent_index = self.agents.index(agent_id)
            return

        self.agents.append(agent_id)
        self.agent_index = len(self.agents) - 1
