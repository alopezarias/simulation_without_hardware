"""No-op RGB adapter for degraded runtime setups."""

from __future__ import annotations

from device_runtime.application.ports import RgbSignal


class NullRgb:
    def __init__(self) -> None:
        self.last_signal: RgbSignal | None = None
        self.clear_calls = 0

    def apply(self, signal: RgbSignal) -> None:
        self.last_signal = signal

    def clear(self) -> None:
        self.clear_calls += 1
        self.last_signal = RgbSignal("off", (0, 0, 0), style="off")
