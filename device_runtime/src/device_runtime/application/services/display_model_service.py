"""Transforms runtime state into a Whisplay-oriented screen view model."""

from __future__ import annotations

from dataclasses import dataclass, field

from device_runtime.application.ports import PowerStatus
from device_runtime.domain.events import DeviceState
from device_runtime.domain.state import DeviceSnapshot
from device_runtime.protocol import UiState


@dataclass(slots=True)
class ScreenViewModel:
    scene: str
    status_text: str
    status_detail: str
    center_title: str
    center_body: str
    center_hint: str
    local_state: str
    remote_state: str
    active_agent: str
    focus_label: str
    transcript_preview: str
    assistant_preview: str
    transcript_label: str
    assistant_label: str
    mic_live: bool
    connected: bool
    network_label: str
    battery_label: str
    diagnostics_label: str
    footer: str
    header_badges: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class DisplayModelService:
    """Creates a compact on-device screen model for Raspberry displays."""

    def build(self, snapshot: DeviceSnapshot, power: PowerStatus | None = None) -> ScreenViewModel:
        power_status = power or PowerStatus(None, None, "none", False, "")
        scene = self._scene(snapshot)
        status_text, status_detail = self._status_copy(scene, snapshot)
        diagnostics_label = self._diagnostics_label(snapshot, power_status)
        header_badges = self._header_badges(snapshot, power_status)
        network_label = header_badges[0] if header_badges else self._network_label(snapshot)
        battery_label = header_badges[1] if len(header_badges) > 1 else self._battery_label(power_status)
        transcript_preview = self._transcript_preview(snapshot, scene)
        assistant_preview = self._assistant_preview(snapshot, scene)
        center_title, center_body, center_hint = self._center_content(
            snapshot,
            scene,
            status_detail,
            transcript_preview,
            assistant_preview,
            network_label,
            diagnostics_label,
        )

        return ScreenViewModel(
            scene=scene,
            status_text=status_text,
            status_detail=status_detail,
            center_title=center_title,
            center_body=center_body,
            center_hint=center_hint,
            local_state=snapshot.device_state.value,
            remote_state=snapshot.remote_ui_state.value,
            active_agent=snapshot.active_agent,
            focus_label=self._focus_label(snapshot),
            transcript_preview=transcript_preview,
            assistant_preview=assistant_preview,
            transcript_label=self._transcript_label(scene),
            assistant_label=self._assistant_label(scene),
            mic_live=snapshot.listening_active,
            connected=snapshot.connected,
            network_label=network_label,
            battery_label=battery_label,
            diagnostics_label=diagnostics_label,
            footer=self._footer(snapshot, scene, diagnostics_label),
            header_badges=header_badges,
            warnings=list(snapshot.warnings),
        )

    def _scene(self, snapshot: DeviceSnapshot) -> str:
        if not snapshot.connected:
            return "disconnected"
        if snapshot.diagnostics.last_error or snapshot.remote_ui_state == UiState.ERROR:
            return "error"
        if snapshot.device_state == DeviceState.LOCKED:
            return "locked"
        if snapshot.device_state == DeviceState.AGENTS:
            return "agent-selection"
        if snapshot.device_state == DeviceState.MODE:
            return "mode-selection"
        if snapshot.device_state == DeviceState.MENU:
            return "menu"
        if snapshot.device_state == DeviceState.LISTEN or snapshot.listening_active:
            return "listening"
        if snapshot.remote_ui_state == UiState.SPEAKING:
            return "speaking"
        if snapshot.remote_ui_state == UiState.PROCESSING:
            return "processing"
        return "ready"

    def _status_copy(self, scene: str, snapshot: DeviceSnapshot) -> tuple[str, str]:
        mapping = {
            "locked": ("Locked", "Hold to unlock"),
            "ready": ("Ready", "Press to talk"),
            "listening": ("Listening", "Release send / dbl cancel"),
            "processing": ("Thinking", "Working on your reply"),
            "speaking": ("Speaking", "Assistant reply live"),
            "menu": ("Menu", "Browse local actions"),
            "mode-selection": ("Mode", "Pick and hold"),
            "agent-selection": ("Agent", "Pick and hold"),
            "disconnected": ("Offline", "Reconnect to the PC backend"),
            "error": ("Attention", snapshot.diagnostics.last_error or "Backend reported an error"),
        }
        status_text, status_detail = mapping.get(scene, ("Ready", "Press to speak"))
        if scene == "agent-selection" and snapshot.focused_agent:
            status_detail = f"Focus {snapshot.focused_agent}"
        if scene == "mode-selection":
            status_detail = f"Mode {snapshot.navigation.available_modes[snapshot.navigation.mode_index % len(snapshot.navigation.available_modes or [snapshot.navigation.active_mode])] if snapshot.navigation.available_modes or snapshot.navigation.active_mode else 'conversation'}"
        return status_text, status_detail

    def _focus_label(self, snapshot: DeviceSnapshot) -> str:
        if snapshot.device_state == DeviceState.MENU:
            options = snapshot.navigation.menu_options or ["-"]
            return options[snapshot.navigation.menu_index % len(options)]
        if snapshot.device_state == DeviceState.MODE:
            modes = snapshot.navigation.available_modes or [snapshot.navigation.active_mode]
            return modes[snapshot.navigation.mode_index % len(modes)]
        if snapshot.device_state == DeviceState.AGENTS:
            return snapshot.focused_agent or "-"
        return snapshot.navigation.active_mode or "-"

    def _transcript_label(self, scene: str) -> str:
        if scene == "listening":
            return "LIVE MIC"
        return "YOU"

    def _assistant_label(self, scene: str) -> str:
        if scene == "processing":
            return "BACKEND"
        return "ASSISTANT"

    def _transcript_preview(self, snapshot: DeviceSnapshot, scene: str) -> str:
        if snapshot.transcript.strip():
            return self._compact(snapshot.transcript, limit=72)
        prompts = {
            "locked": "Hold the button to wake.",
            "ready": "Tap and start speaking.",
            "listening": "Mic is open.",
            "menu": "Pick the next local action.",
            "mode-selection": "Tap to move, hold to keep.",
            "agent-selection": "Tap to browse the agent list.",
            "disconnected": "Waiting for network and backend.",
        }
        return prompts.get(scene, "Waiting for transcript...")

    def _assistant_preview(self, snapshot: DeviceSnapshot, scene: str) -> str:
        if snapshot.assistant_text.strip():
            return self._compact(snapshot.assistant_text, limit=84)
        prompts = {
            "processing": "Backend is preparing the answer.",
            "speaking": "Reply audio is streaming.",
            "disconnected": "Responses resume after reconnect.",
            "error": snapshot.diagnostics.last_error or "Runtime needs attention.",
        }
        return prompts.get(scene, "Assistant reply will appear here.")

    def _network_label(self, snapshot: DeviceSnapshot) -> str:
        status = snapshot.diagnostics.transport_status or ("connected" if snapshot.connected else "disconnected")
        return f"NET {status.upper()}"

    def _battery_label(self, power: PowerStatus) -> str:
        if not power.available:
            return "BAT --"
        if power.battery_percent is None:
            return "BAT ?"
        suffix = " CHG" if power.charging else ""
        return f"BAT {int(round(power.battery_percent))}%{suffix}"

    def _header_badges(self, snapshot: DeviceSnapshot, power: PowerStatus) -> list[str]:
        badges = [self._network_label(snapshot), self._battery_label(power)]
        if snapshot.pending_agent_ack:
            badges.append("AGENT PENDING")
        elif snapshot.active_agent:
            badges.append(snapshot.active_agent.upper())
        return badges

    def _diagnostics_label(self, snapshot: DeviceSnapshot, power: PowerStatus) -> str:
        if snapshot.diagnostics.last_error:
            return snapshot.diagnostics.last_error
        if snapshot.diagnostics.last_note:
            return snapshot.diagnostics.last_note
        if not power.available:
            return power.detail or "PiSugar unavailable"
        if snapshot.warnings:
            return snapshot.warnings[0]
        return "Runtime healthy"

    def _center_content(
        self,
        snapshot: DeviceSnapshot,
        scene: str,
        status_detail: str,
        transcript_preview: str,
        assistant_preview: str,
        network_label: str,
        diagnostics_label: str,
    ) -> tuple[str, str, str]:
        active_agent = snapshot.active_agent.replace("assistant-", "").replace("-", " ").strip() or "assistant"
        if scene == "locked":
            return ("Hold to wake", active_agent.title(), network_label)
        if scene == "ready":
            return ("Press to talk", active_agent.title(), network_label)
        if scene == "listening":
            title = transcript_preview if snapshot.transcript.strip() else "Listening now"
            return (title, status_detail, active_agent.title())
        if scene == "processing":
            return (assistant_preview, active_agent.title(), "Reply on the way")
        if scene == "speaking":
            return (assistant_preview, active_agent.title(), "Audio playing")
        if scene == "menu":
            return (snapshot.focused_agent or self._focus_label(snapshot).title(), status_detail, "Tap browse / dbl exit")
        if scene == "mode-selection":
            return (self._focus_label(snapshot).title(), "Mode selection", status_detail)
        if scene == "agent-selection":
            count = len(snapshot.agents) or 1
            return (self._focus_label(snapshot), f"Agent {snapshot.agent_index + 1}/{count}", status_detail)
        if scene == "disconnected":
            return ("Backend offline", "Check Wi-Fi or DEVICE_WS_URL", diagnostics_label)
        if scene == "error":
            return ("Needs attention", diagnostics_label, network_label)
        return (status_detail, active_agent.title(), network_label)

    def _footer(self, snapshot: DeviceSnapshot, scene: str, diagnostics_label: str) -> str:
        parts = [scene.replace("-", " "), snapshot.remote_ui_state.value]
        if snapshot.last_latency_ms is not None:
            parts.append(f"{snapshot.last_latency_ms} ms")
        if diagnostics_label and diagnostics_label != "Runtime healthy":
            parts.append(diagnostics_label)
        return " | ".join(part for part in parts if part)

    def _compact(self, value: str, *, limit: int) -> str:
        text = " ".join(value.split()).strip()
        if not text:
            return "-"
        if len(text) <= limit:
            return text
        return text[: limit - 3].rstrip() + "..."
