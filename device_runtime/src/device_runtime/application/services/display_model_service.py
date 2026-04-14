"""Transforms runtime state into a Whisplay-oriented screen view model."""

from __future__ import annotations

from dataclasses import dataclass, field

from device_runtime.application.ports import PowerStatus
from device_runtime.domain.events import DeviceState
from device_runtime.domain.state import DeviceSnapshot


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
        if snapshot.diagnostics.last_error:
            return "error"
        if snapshot.device_state == DeviceState.LISTENING or snapshot.listening_active:
            return "listening"
        if snapshot.device_state == DeviceState.CALLING:
            return "calling"
        if snapshot.device_state == DeviceState.INCOMING_CALL:
            return "incoming-call"
        if snapshot.device_state == DeviceState.CONFIG:
            return "config"
        return "standby"

    def _status_copy(self, scene: str, snapshot: DeviceSnapshot) -> tuple[str, str]:
        mapping = {
            "standby": ("Standby", "Hold to talk / press to call"),
            "listening": ("Listening", "Release to send"),
            "calling": ("Llamando", "Waiting for backend greeting"),
            "incoming-call": ("Incoming call", "Hold to answer"),
            "config": ("Config", "Local settings"),
            "disconnected": ("Offline", "Reconnect to the PC backend"),
            "error": ("Attention", snapshot.diagnostics.last_error or "Backend reported an error"),
        }
        return mapping.get(scene, ("Standby", "Hold to talk / press to call"))

    def _focus_label(self, snapshot: DeviceSnapshot) -> str:
        return snapshot.device_state.value

    def _transcript_label(self, scene: str) -> str:
        if scene == "listening":
            return "LIVE MIC"
        return "YOU"

    def _assistant_label(self, scene: str) -> str:
        if scene in {"calling", "incoming-call"}:
            return "CALL"
        return "ASSISTANT"

    def _transcript_preview(self, snapshot: DeviceSnapshot, scene: str) -> str:
        if snapshot.transcript.strip():
            return self._compact(snapshot.transcript, limit=72)
        prompts = {
            "standby": "Hold to talk or press to call.",
            "listening": "Mic is open.",
            "calling": "Calling the backend.",
            "incoming-call": "Backend is calling you.",
            "config": "Configuration mode.",
            "disconnected": "Waiting for network and backend.",
        }
        return prompts.get(scene, "Waiting for transcript...")

    def _assistant_preview(self, snapshot: DeviceSnapshot, scene: str) -> str:
        if snapshot.assistant_text.strip():
            return self._compact(snapshot.assistant_text, limit=84)
        prompts = {
            "calling": "Greeting audio will play here.",
            "incoming-call": "Distinct ringing/call audio is active.",
            "config": "Config flow remains intentionally minimal.",
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
        if snapshot.active_agent:
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
        if scene == "standby":
            return ("Standby", active_agent.title(), "Hold to talk")
        if scene == "listening":
            title = transcript_preview if snapshot.transcript.strip() else "Listening now"
            return (title, status_detail, active_agent.title())
        if scene == "calling":
            return ("Llamando", assistant_preview, "Return to standby after playback")
        if scene == "incoming-call":
            return ("Incoming call", status_detail, "Hold to talk")
        if scene == "config":
            return ("Config", status_detail, "Incoming call preempts")
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
