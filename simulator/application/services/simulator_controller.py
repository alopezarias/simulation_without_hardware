"""Compatibility wrapper for the shared runtime controller."""

from device_runtime.application.services.device_controller import DeviceController


class SimulatorController(DeviceController):
    """Compatibility alias that keeps simulator imports stable during migration."""
