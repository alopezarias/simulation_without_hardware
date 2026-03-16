"""Runtime capability models for effective and degraded peripherals."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class CapabilityStatus(str, Enum):
    ENABLED = "enabled"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


@dataclass(slots=True)
class CapabilityState:
    name: str
    status: CapabilityStatus
    detail: str = ""


@dataclass(slots=True)
class DeviceCapabilities:
    screen: CapabilityState = field(
        default_factory=lambda: CapabilityState(name="screen", status=CapabilityStatus.ENABLED)
    )
    button: CapabilityState = field(
        default_factory=lambda: CapabilityState(name="button", status=CapabilityStatus.ENABLED)
    )
    audio_in: CapabilityState = field(
        default_factory=lambda: CapabilityState(name="audio_in", status=CapabilityStatus.ENABLED)
    )
    audio_out: CapabilityState = field(
        default_factory=lambda: CapabilityState(name="audio_out", status=CapabilityStatus.ENABLED)
    )
    transport: CapabilityState = field(
        default_factory=lambda: CapabilityState(name="transport", status=CapabilityStatus.ENABLED)
    )
    extras: dict[str, CapabilityState] = field(default_factory=dict)

    def all(self) -> dict[str, CapabilityState]:
        items = {
            "screen": self.screen,
            "button": self.button,
            "audio_in": self.audio_in,
            "audio_out": self.audio_out,
            "transport": self.transport,
        }
        items.update(self.extras)
        return items
