"""Compatibility wrappers for shared runtime ports."""

from device_runtime.application.ports import (
    AudioCapturePort,
    AudioPlaybackPort,
    BackendGateway,
    ButtonInputPort,
    CapabilityProvider,
    Clock,
    DiagnosticsPort,
    DisplayPort,
    StateObserver,
    TransportPort,
)

__all__ = [
    "AudioCapturePort",
    "AudioPlaybackPort",
    "BackendGateway",
    "ButtonInputPort",
    "CapabilityProvider",
    "Clock",
    "DiagnosticsPort",
    "DisplayPort",
    "StateObserver",
    "TransportPort",
]
