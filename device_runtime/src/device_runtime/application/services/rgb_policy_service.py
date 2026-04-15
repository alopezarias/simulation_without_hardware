"""Deterministic RGB policy for the standalone Raspberry runtime."""

from __future__ import annotations

from device_runtime.application.ports import PowerStatus, RgbSignal
from device_runtime.domain.events import DeviceState
from device_runtime.domain.state import DeviceSnapshot


class RgbPolicyService:
    """Maps runtime state to a stable RGB signal."""

    def select(self, snapshot: DeviceSnapshot, power: PowerStatus | None = None) -> RgbSignal:
        if snapshot.device_state == DeviceState.INCOMING_CALL:
            return RgbSignal("incoming_call", (255, 120, 32), style="pulse", detail="incoming call")
        if snapshot.audio_outbound_active:
            return RgbSignal("audio_outbound", (255, 214, 10), style="solid", detail="sending audio")
        return RgbSignal("off", (0, 0, 0), style="solid", detail="idle")
