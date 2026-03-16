"""Guarded Raspberry Pi GPIO button adapter with timing normalization."""

from __future__ import annotations

import importlib
import threading
import time
from typing import Any, Callable

try:
    _GPIOZeroButton = importlib.import_module("gpiozero").Button

    GPIO_IMPORT_ERROR: Exception | None = None
except Exception as exc:  # pragma: no cover - depends on host environment
    _GPIOZeroButton = None
    GPIO_IMPORT_ERROR = exc


class GpioButton:
    def __init__(
        self,
        pin: int,
        *,
        button_factory: Callable[..., Any] | None = None,
        bounce_time: float = 0.05,
        long_press_ms: int = 900,
        double_press_ms: int = 350,
        timer_factory: Callable[[float, Callable[[], None]], Any] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.pin = pin
        self._button_factory = button_factory
        self._bounce_time = bounce_time
        self._long_press_s = max(0.001, long_press_ms / 1000)
        self._double_press_s = max(0.001, double_press_ms / 1000)
        self._timer_factory = timer_factory or _ThreadTimer
        self._clock = clock or time.monotonic
        self._handler: Callable[[str], None] | None = None
        self._button: Any | None = None
        self._single_press_timer: Any | None = None
        self._last_press_at: float | None = None
        self._long_press_emitted = False
        self.started = False

    @property
    def available(self) -> bool:
        return self._button_factory is not None or _GPIOZeroButton is not None

    def start(self, on_event: Callable[[str], None]) -> None:
        factory = self._button_factory or _GPIOZeroButton
        if factory is None:
            detail = ""
            if GPIO_IMPORT_ERROR is not None:
                detail = f": {GPIO_IMPORT_ERROR}"
            raise RuntimeError("GPIO button adapter requires gpiozero on Raspberry Pi" + detail)
        self._handler = on_event
        self._button = factory(self.pin, bounce_time=self._bounce_time)
        button: Any = self._button
        if hasattr(button, "when_pressed"):
            button.when_pressed = self._handle_press
        if hasattr(button, "hold_time"):
            button.hold_time = self._long_press_s
        if hasattr(button, "when_held"):
            button.when_held = self._handle_long_press
        self._cancel_single_press_timer()
        self._last_press_at = None
        self._long_press_emitted = False
        self.started = True

    def stop(self) -> None:
        self.started = False
        self._cancel_single_press_timer()
        self._last_press_at = None
        self._long_press_emitted = False
        if self._button is not None and hasattr(self._button, "close"):
            self._button.close()
        self._button = None
        self._handler = None

    def emit_for_test(self, event_name: str) -> None:
        self._emit(event_name)

    def _emit(self, event_name: str) -> None:
        if self.started and self._handler is not None:
            self._handler(event_name)

    def _handle_press(self) -> None:
        if not self.started:
            return
        now = self._clock()
        if self._single_press_timer is not None and self._last_press_at is not None:
            if now - self._last_press_at <= self._double_press_s:
                self._cancel_single_press_timer()
                self._last_press_at = None
                self._long_press_emitted = False
                self._emit("double_press")
                return
            self._cancel_single_press_timer()

        self._long_press_emitted = False
        self._last_press_at = now
        self._single_press_timer = self._timer_factory(self._double_press_s, self._emit_single_press)
        self._single_press_timer.start()

    def _handle_long_press(self) -> None:
        if not self.started or self._long_press_emitted:
            return
        self._long_press_emitted = True
        self._cancel_single_press_timer()
        self._last_press_at = None
        self._emit("long_press")

    def _emit_single_press(self) -> None:
        self._single_press_timer = None
        self._last_press_at = None
        if self._long_press_emitted:
            self._long_press_emitted = False
            return
        self._emit("press")

    def _cancel_single_press_timer(self) -> None:
        timer = self._single_press_timer
        self._single_press_timer = None
        if timer is not None and hasattr(timer, "cancel"):
            timer.cancel()


class _ThreadTimer:
    def __init__(self, interval_s: float, callback: Callable[[], None]) -> None:
        self._timer = threading.Timer(interval_s, callback)

    def start(self) -> None:
        self._timer.start()

    def cancel(self) -> None:
        self._timer.cancel()
