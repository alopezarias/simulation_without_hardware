"""Power adapter implementations for Raspberry runtime parity."""

from device_runtime.infrastructure.power.pisugar_status import NullPowerStatus, PiSugarStatus

__all__ = ["NullPowerStatus", "PiSugarStatus"]
