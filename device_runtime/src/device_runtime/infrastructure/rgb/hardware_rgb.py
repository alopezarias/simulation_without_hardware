"""Hardware RGB adapter with safe fallbacks around optional drivers."""

from __future__ import annotations

from typing import Any, Callable

from device_runtime.application.ports import RgbSignal
from device_runtime.infrastructure.whisplay_vendor import load_whisplay_vendor


class HardwareRgb:
    def __init__(
        self,
        *,
        controller: Any | None = None,
        controller_factory: Callable[[], Any] | None = None,
        driver_path: str = "",
    ) -> None:
        self._controller = controller
        self._controller_factory = controller_factory
        self._driver_path = driver_path
        self.last_signal: RgbSignal | None = None

    @property
    def available(self) -> bool:
        if self._controller is not None or self._controller_factory is not None:
            return True
        return load_whisplay_vendor(self._driver_path).available

    def apply(self, signal: RgbSignal) -> None:
        if self.last_signal == signal:
            return
        controller = self._ensure_controller()
        red, green, blue = signal.color
        if signal.style == "off":
            self._set_rgb(controller, 0, 0, 0)
        elif signal.style == "pulse" and hasattr(controller, "set_rgb_fade"):
            controller.set_rgb_fade(red, green, blue, duration_ms=250)
        else:
            self._set_rgb(controller, red, green, blue)
        self.last_signal = signal

    def clear(self) -> None:
        self.apply(RgbSignal("off", (0, 0, 0), style="off"))

    def _ensure_controller(self) -> Any:
        if self._controller is not None:
            return self._controller
        if self._controller_factory is not None:
            self._controller = self._controller_factory()
            return self._controller
        self._controller = load_whisplay_vendor(self._driver_path).create_board()
        return self._controller

    def _set_rgb(self, controller: Any, red: int, green: int, blue: int) -> None:
        if hasattr(controller, "set_rgb"):
            controller.set_rgb(red, green, blue)
            return
        raise RuntimeError("RGB controller does not expose set_rgb")
