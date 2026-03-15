"""Compatibility re-exports for shared runtime event types."""

from device_runtime.domain.events import DeviceInputEvent, DeviceState, DomainEffect, EffectPayload, MenuOption

__all__ = ["DeviceInputEvent", "DeviceState", "DomainEffect", "EffectPayload", "MenuOption"]
