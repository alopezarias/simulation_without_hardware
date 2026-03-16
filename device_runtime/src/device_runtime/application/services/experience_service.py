"""Composes runtime experience outputs for screen and RGB adapters."""

from __future__ import annotations

from dataclasses import dataclass

from device_runtime.application.ports import PowerStatus, RgbSignal
from device_runtime.application.services.display_model_service import DisplayModelService, ScreenViewModel
from device_runtime.application.services.rgb_policy_service import RgbPolicyService
from device_runtime.domain.state import DeviceSnapshot


@dataclass(slots=True)
class RuntimeExperience:
    screen: ScreenViewModel
    rgb_signal: RgbSignal
    power: PowerStatus


class ExperienceService:
    def __init__(
        self,
        *,
        display_model_service: DisplayModelService | None = None,
        rgb_policy_service: RgbPolicyService | None = None,
    ) -> None:
        self._display_model_service = display_model_service or DisplayModelService()
        self._rgb_policy_service = rgb_policy_service or RgbPolicyService()

    def build(self, snapshot: DeviceSnapshot, power: PowerStatus) -> RuntimeExperience:
        screen = self._display_model_service.build(snapshot, power)
        rgb_signal = self._rgb_policy_service.select(snapshot, power)
        return RuntimeExperience(screen=screen, rgb_signal=rgb_signal, power=power)
