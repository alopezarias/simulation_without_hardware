"""RGB adapter implementations for Raspberry runtime parity."""

from device_runtime.infrastructure.rgb.hardware_rgb import HardwareRgb
from device_runtime.infrastructure.rgb.null_rgb import NullRgb

__all__ = ["HardwareRgb", "NullRgb"]
