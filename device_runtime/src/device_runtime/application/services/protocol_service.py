"""Inbound protocol mapping that preserves local device-state ownership."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field

from device_runtime.domain.events import DeviceState
from device_runtime.domain.state import DeviceSnapshot
from device_runtime.protocol import UiState, coerce_ui_state, normalize_message_type


@dataclass(slots=True)
class ProtocolUpdate:
    snapshot: DeviceSnapshot
    effects: list[object] = field(default_factory=list)
    note: str = ""


class ProtocolService:
    """Applies backend messages onto the shared runtime snapshot."""

    def apply_message(self, snapshot: DeviceSnapshot, message: dict[str, Any], *, now: float) -> ProtocolUpdate:
        current = deepcopy(snapshot)
        message_type = normalize_message_type(message.get("type"))

        if message_type == "session.ready":
            return self._apply_session_ready(current, message, now=now)
        if message_type == "ui.state":
            return self._apply_ui_state(current, message)
        if message_type == "agent.selected":
            selected = str(message.get("agent_id", "")).strip()
            if selected:
                current.active_agent = selected
            return ProtocolUpdate(snapshot=current)
        if message_type == "incoming_call":
            return self._apply_incoming_call(current)
        if message_type == "transcript.partial":
            piece = str(message.get("text", "")).strip()
            if piece:
                current.transcript = (current.transcript + " " + piece).strip()
            return ProtocolUpdate(snapshot=current)
        if message_type == "transcript.final":
            current.transcript = str(message.get("text", current.transcript))
            return ProtocolUpdate(snapshot=current)
        if message_type == "assistant.text.partial":
            current.assistant_text += str(message.get("text", ""))
            return ProtocolUpdate(snapshot=current)
        if message_type == "assistant.text.final":
            current.assistant_text = str(message.get("text", current.assistant_text))
            if bool(message.get("interrupted")):
                current.assistant_text += " [interrupted]"
            latency = message.get("latency_ms")
            if isinstance(latency, int):
                current.last_latency_ms = latency
            return ProtocolUpdate(snapshot=current)
        if message_type == "assistant.audio.chunk" and not message.get("payload"):
            warning = "assistant.audio.chunk invalid"
            if warning not in current.warnings:
                current.warnings = [*current.warnings, warning]
            return ProtocolUpdate(snapshot=current, note=warning)
        if message_type == "assistant.audio.start":
            current.playback_active = True
            return ProtocolUpdate(snapshot=current, note="assistant audio started")
        if message_type == "assistant.audio.file":
            current.playback_active = True
            return ProtocolUpdate(snapshot=current, note="assistant audio file queued")
        if message_type == "assistant.audio.end":
            current.playback_active = False
            if current.device_state == DeviceState.CALLING:
                current.device_state = DeviceState.STANDBY
                current.remote_ui_state = UiState.STANDBY
                return ProtocolUpdate(snapshot=current, note="call playback ended")
            return ProtocolUpdate(snapshot=current, note="assistant audio ended")
        if message_type == "error":
            current.remote_ui_state = current.device_state.value
            detail = str(message.get("detail", "")).strip()
            current.diagnostics.last_error = detail or "backend error"
            return ProtocolUpdate(snapshot=current, note=detail or "backend error")
        return ProtocolUpdate(snapshot=current)

    def _apply_session_ready(
        self,
        snapshot: DeviceSnapshot,
        message: dict[str, Any],
        *,
        now: float,
    ) -> ProtocolUpdate:
        snapshot.connected = True
        snapshot.diagnostics.transport_status = "connected"
        snapshot.session_id = str(message.get("session_id", ""))
        remote_agent = str(message.get("active_agent", "")).strip()
        if remote_agent:
            snapshot.active_agent = remote_agent
        snapshot.remote_ui_state = UiState.STANDBY

        return ProtocolUpdate(snapshot=snapshot, note="session ready")

    def _apply_ui_state(self, snapshot: DeviceSnapshot, message: dict[str, Any]) -> ProtocolUpdate:
        snapshot.remote_ui_state, warning = coerce_ui_state(message.get("state"), default=UiState.STANDBY)
        if snapshot.remote_ui_state == UiState.INCOMING_CALL and snapshot.device_state != DeviceState.LISTENING:
            snapshot.device_state = DeviceState.INCOMING_CALL
        elif snapshot.remote_ui_state == UiState.STANDBY and snapshot.device_state == DeviceState.CONFIG:
            pass
        elif snapshot.device_state not in {DeviceState.LISTENING, DeviceState.CALLING, DeviceState.CONFIG}:
            snapshot.device_state = DeviceState(snapshot.remote_ui_state.value)
        if warning:
            if warning not in snapshot.warnings:
                snapshot.warnings = [*snapshot.warnings, warning]
        return ProtocolUpdate(snapshot=snapshot)

    def _apply_incoming_call(self, snapshot: DeviceSnapshot) -> ProtocolUpdate:
        if snapshot.device_state != DeviceState.LISTENING:
            snapshot.device_state = DeviceState.INCOMING_CALL
        snapshot.remote_ui_state = UiState.INCOMING_CALL
        return ProtocolUpdate(snapshot=snapshot, note="incoming call")
