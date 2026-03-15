"""Transforms runtime state into a neutral screen view model."""

from __future__ import annotations

from dataclasses import dataclass, field

from device_runtime.domain.events import DeviceState
from device_runtime.domain.state import DeviceSnapshot


@dataclass(slots=True)
class ScreenViewModel:
    local_state: str
    remote_state: str
    active_agent: str
    focus_label: str
    transcript_preview: str
    assistant_preview: str
    mic_live: bool
    connected: bool
    warnings: list[str] = field(default_factory=list)


class DisplayModelService:
    """Creates a shared visual model for simulator and hardware displays."""

    def build(self, snapshot: DeviceSnapshot) -> ScreenViewModel:
        return ScreenViewModel(
            local_state=snapshot.device_state.value,
            remote_state=snapshot.remote_ui_state.value,
            active_agent=snapshot.active_agent,
            focus_label=self._focus_label(snapshot),
            transcript_preview=snapshot.transcript,
            assistant_preview=snapshot.assistant_text,
            mic_live=snapshot.listening_active,
            connected=snapshot.connected,
            warnings=list(snapshot.warnings),
        )

    def _focus_label(self, snapshot: DeviceSnapshot) -> str:
        if snapshot.device_state == DeviceState.MENU:
            options = snapshot.navigation.menu_options or ["-"]
            return options[snapshot.navigation.menu_index % len(options)]
        if snapshot.device_state == DeviceState.MODE:
            modes = snapshot.navigation.available_modes or [snapshot.navigation.active_mode]
            return modes[snapshot.navigation.mode_index % len(modes)]
        if snapshot.device_state == DeviceState.AGENTS:
            return snapshot.focused_agent or "-"
        return "-"
