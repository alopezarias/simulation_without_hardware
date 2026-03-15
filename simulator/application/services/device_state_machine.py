"""Compatibility re-exports for shared runtime state machine."""

from device_runtime.application.services.device_state_machine import DeviceStateMachine, TransitionResult

__all__ = ["DeviceStateMachine", "TransitionResult"]
