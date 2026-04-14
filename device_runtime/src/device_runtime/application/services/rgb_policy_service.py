"""Deterministic RGB policy for the standalone Raspberry runtime."""

from __future__ import annotations

from device_runtime.application.ports import PowerStatus, RgbSignal
from device_runtime.domain.events import DeviceState
from device_runtime.domain.state import DeviceSnapshot


class RgbPolicyService:
    """Maps runtime state to a stable RGB signal."""

    def select(self, snapshot: DeviceSnapshot, power: PowerStatus | None = None) -> RgbSignal:
        if not snapshot.connected:
            return RgbSignal("disconnected", (64, 196, 255), style="pulse", detail="backend offline")
        if snapshot.diagnostics.last_error:
            return RgbSignal("error", (255, 48, 48), style="pulse", detail="runtime error")
        if snapshot.device_state == DeviceState.LISTENING or snapshot.listening_active:
            return RgbSignal("listening", (255, 214, 10), style="solid", detail="microphone live")
        if snapshot.device_state == DeviceState.CALLING:
            return RgbSignal("calling", (92, 182, 255), style="pulse", detail="outgoing call")
        if snapshot.device_state == DeviceState.INCOMING_CALL:
            return RgbSignal("incoming_call", (255, 120, 32), style="pulse", detail="incoming call")
        if snapshot.device_state == DeviceState.CONFIG:
            return RgbSignal("config", (180, 120, 255), style="solid", detail="config mode")
        if power is not None and power.available and power.charging:
            return RgbSignal("charging", (56, 231, 109), style="pulse", detail="external power")
        return RgbSignal("standby", (56, 231, 109), style="solid", detail="standby")
