"""Deterministic RGB policy for the standalone Raspberry runtime."""

from __future__ import annotations

from device_runtime.application.ports import PowerStatus, RgbSignal
from device_runtime.domain.events import DeviceState
from device_runtime.domain.state import DeviceSnapshot
from device_runtime.protocol import UiState


class RgbPolicyService:
    """Maps runtime state to a stable RGB signal."""

    def select(self, snapshot: DeviceSnapshot, power: PowerStatus | None = None) -> RgbSignal:
        if not snapshot.connected:
            return RgbSignal("disconnected", (64, 196, 255), style="pulse", detail="backend offline")
        if snapshot.diagnostics.last_error or snapshot.remote_ui_state == UiState.ERROR:
            return RgbSignal("error", (255, 48, 48), style="pulse", detail="runtime error")
        if snapshot.device_state == DeviceState.LOCKED:
            return RgbSignal("locked", (0, 0, 0), style="off", detail="device locked")
        if snapshot.device_state == DeviceState.LISTEN or snapshot.listening_active:
            return RgbSignal("listening", (255, 214, 10), style="solid", detail="microphone live")
        if snapshot.remote_ui_state == UiState.SPEAKING:
            return RgbSignal("speaking", (64, 196, 255), style="pulse", detail="assistant audio")
        if snapshot.remote_ui_state == UiState.PROCESSING:
            return RgbSignal("processing", (92, 182, 255), style="pulse", detail="backend processing")
        if snapshot.device_state in {DeviceState.MENU, DeviceState.MODE, DeviceState.AGENTS}:
            return RgbSignal("navigation", (74, 222, 128), style="solid", detail="navigation mode")
        if power is not None and power.available and power.charging:
            return RgbSignal("charging", (56, 231, 109), style="pulse", detail="external power")
        return RgbSignal("ready", (56, 231, 109), style="solid", detail="ready")
