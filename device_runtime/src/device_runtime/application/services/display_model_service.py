"""Transforms runtime state into a Whisplay-oriented screen view model."""

from __future__ import annotations

from dataclasses import dataclass, field

from device_runtime.application.ports import PowerStatus
from device_runtime.domain.events import DeviceState
from device_runtime.domain.state import DeviceSnapshot


@dataclass(slots=True)
class ScreenViewModel:
    scene: str
    status_icon: str
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
    battery_percent: int | None
    battery_charging: bool
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
        status_icon = self._status_icon(scene)
        diagnostics_label = self._diagnostics_label(snapshot, power_status)
        header_badges = self._header_badges(snapshot, power_status)
        network_label = header_badges[0] if header_badges else self._network_label(snapshot)
        battery_percent = self._battery_percent(power_status)
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
            status_icon=status_icon,
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
            battery_percent=battery_percent,
            battery_charging=bool(power_status.charging),
            diagnostics_label=diagnostics_label,
            footer=self._footer(snapshot, scene, diagnostics_label),
            header_badges=header_badges,
            warnings=self._display_warnings(snapshot, power_status),
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
            "standby": ("Standby", "Hold to talk · Press to call"),
            "listening": ("Listening", "Release to send"),
            "calling": ("Calling", "Connecting audio"),
            "incoming-call": ("Incoming", "Hold to answer"),
            "config": ("Settings", "Double press to exit"),
            "disconnected": ("Offline", "Reconnect to the backend"),
            "error": ("Attention", snapshot.diagnostics.last_error or "Device needs attention"),
        }
        return mapping.get(scene, ("Standby", "Hold to talk · Press to call"))

    def _status_icon(self, scene: str) -> str:
        mapping = {
            "standby": "Zz",
            "listening": "MIC",
            "calling": "OUT",
            "incoming-call": "IN",
            "config": "CFG",
            "disconnected": "OFF",
            "error": "!",
        }
        return mapping.get(scene, "•")

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
            "standby": "Ready when you are.",
            "listening": "Speak now.",
            "calling": "Starting the call.",
            "incoming-call": "Someone is calling you.",
            "config": "Device settings.",
            "disconnected": "Waiting for network.",
        }
        return prompts.get(scene, "Waiting for transcript...")

    def _assistant_preview(self, snapshot: DeviceSnapshot, scene: str) -> str:
        if snapshot.assistant_text.strip():
            return self._compact(snapshot.assistant_text, limit=84)
        prompts = {
            "calling": "Assistant audio will start soon.",
            "incoming-call": "Answer to begin talking.",
            "config": "Keep setup simple on device.",
            "disconnected": "Responses resume after reconnect.",
            "error": snapshot.diagnostics.last_error or "Runtime needs attention.",
        }
        return prompts.get(scene, "Assistant reply will appear here.")

    def _network_label(self, snapshot: DeviceSnapshot) -> str:
        status = snapshot.diagnostics.transport_status or ("connected" if snapshot.connected else "disconnected")
        return f"NET {status.upper()}"

    def _battery_label(self, power: PowerStatus) -> str:
        percent = self._battery_percent(power)
        if percent is None:
            return "--"
        suffix = "+" if power.charging else ""
        return f"{percent}%{suffix}"

    def _battery_percent(self, power: PowerStatus) -> int | None:
        if not power.available or power.battery_percent is None:
            return None
        return int(round(power.battery_percent))

    def _header_badges(self, snapshot: DeviceSnapshot, power: PowerStatus) -> list[str]:
        badges = [self._network_label(snapshot), self._battery_label(power)]
        return badges

    def _diagnostics_label(self, snapshot: DeviceSnapshot, power: PowerStatus) -> str:
        warnings = self._display_warnings(snapshot, power)
        if snapshot.diagnostics.last_error:
            return snapshot.diagnostics.last_error
        if warnings:
            return warnings[0]
        if snapshot.diagnostics.last_note:
            return snapshot.diagnostics.last_note
        return ""

    def _display_warnings(self, snapshot: DeviceSnapshot, power: PowerStatus) -> list[str]:
        if power.available:
            return list(snapshot.warnings)
        return [warning for warning in snapshot.warnings if not warning.lower().startswith("power ")]

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
            return ("Ready", active_agent.title(), status_detail)
        if scene == "listening":
            title = transcript_preview if snapshot.transcript.strip() else "Listening"
            body = "Speak now" if not snapshot.transcript.strip() else "Release to send"
            return (title, body, status_detail)
        if scene == "calling":
            return ("Calling", assistant_preview, "Press once to cancel later")
        if scene == "incoming-call":
            return ("Incoming call", "Hold to answer", "Release to speak")
        if scene == "config":
            return ("Settings", "Press once for call", status_detail)
        if scene == "disconnected":
            return ("Backend offline", "Check Wi-Fi or backend URL", "Trying again automatically")
        if scene == "error":
            return ("Needs attention", diagnostics_label, network_label)
        return (status_detail, active_agent.title(), network_label)

    def _footer(self, snapshot: DeviceSnapshot, scene: str, diagnostics_label: str) -> str:
        if scene in {"error", "disconnected"}:
            return diagnostics_label
        return ""

    def _compact(self, value: str, *, limit: int) -> str:
        text = " ".join(value.split()).strip()
        if not text:
            return "-"
        if len(text) <= limit:
            return text
        return text[: limit - 3].rstrip() + "..."
