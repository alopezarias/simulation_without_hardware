"""Whisplay vendor button adapter with click normalization."""

from __future__ import annotations

import threading
import time
from typing import Any, Callable


class WhisplayButton:
    def __init__(
        self,
        *,
        board: Any | None = None,
        board_provider: Callable[[], Any] | None = None,
        long_press_ms: int = 900,
        double_press_ms: int = 350,
        timer_factory: Callable[[float, Callable[[], None]], Any] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._board = board
        self._board_provider = board_provider
        self._long_press_s = max(0.001, long_press_ms / 1000)
        self._double_press_s = max(0.001, double_press_ms / 1000)
        self._timer_factory = timer_factory or _ThreadTimer
        self._clock = clock or time.monotonic
        self._handler: Callable[[str], None] | None = None
        self._click_timer: Any | None = None
        self._long_press_timer: Any | None = None
        self._last_release_at: float | None = None
        self._pressed = False
        self._long_press_emitted = False
        self._lock = threading.Lock()
        self.started = False

    @property
    def available(self) -> bool:
        return self._board is not None or self._board_provider is not None

    def start(self, on_event: Callable[[str], None]) -> None:
        board = self._ensure_board()
        self._handler = on_event
        self._cancel_click_timer()
        self._cancel_long_press_timer()
        self._last_release_at = None
        self._pressed = False
        self._long_press_emitted = False
        register_press = getattr(board, "on_button_press", None)
        register_release = getattr(board, "on_button_release", None)
        if not callable(register_press) or not callable(register_release):
            raise RuntimeError("Whisplay board does not expose on_button_press/on_button_release")
        register_press(self._handle_press)
        register_release(self._handle_release)
        self.started = True

    def stop(self) -> None:
        board = self._board
        self.started = False
        self._cancel_click_timer()
        self._cancel_long_press_timer()
        self._last_release_at = None
        self._pressed = False
        self._long_press_emitted = False
        self._handler = None
        if board is not None:
            register_press = getattr(board, "on_button_press", None)
            register_release = getattr(board, "on_button_release", None)
            if callable(register_press):
                register_press(None)
            if callable(register_release):
                register_release(None)

    def _ensure_board(self) -> Any:
        if self._board is not None:
            return self._board
        if self._board_provider is None:
            raise RuntimeError("Whisplay button adapter requires a vendor board provider")
        self._board = self._board_provider()
        return self._board

    def _handle_press(self) -> None:
        with self._lock:
            if not self.started or self._pressed:
                return
            self._pressed = True
            self._long_press_emitted = False
            self._cancel_long_press_timer()
            self._long_press_timer = self._timer_factory(self._long_press_s, self._emit_long_press)
            self._long_press_timer.start()

    def _handle_release(self) -> None:
        with self._lock:
            if not self.started or not self._pressed:
                return
            self._pressed = False
            self._cancel_long_press_timer()
            if self._long_press_emitted:
                self._long_press_emitted = False
                self._last_release_at = None
                return
            now = self._clock()
            if self._click_timer is not None and self._last_release_at is not None:
                if now - self._last_release_at <= self._double_press_s:
                    self._cancel_click_timer()
                    self._last_release_at = None
                    self._emit("double_press")
                    return
                self._cancel_click_timer()
            self._last_release_at = now
            self._click_timer = self._timer_factory(self._double_press_s, self._emit_click)
            self._click_timer.start()

    def _emit_long_press(self) -> None:
        with self._lock:
            self._long_press_timer = None
            if not self.started or not self._pressed:
                return
            self._long_press_emitted = True
            self._cancel_click_timer()
            self._emit("long_press")

    def _emit_click(self) -> None:
        with self._lock:
            self._click_timer = None
            self._last_release_at = None
            if not self.started or self._long_press_emitted:
                return
            self._emit("press")

    def _emit(self, event_name: str) -> None:
        handler = self._handler
        if self.started and handler is not None:
            handler(event_name)

    def _cancel_click_timer(self) -> None:
        timer = self._click_timer
        self._click_timer = None
        if timer is not None and hasattr(timer, "cancel"):
            timer.cancel()

    def _cancel_long_press_timer(self) -> None:
        timer = self._long_press_timer
        self._long_press_timer = None
        if timer is not None and hasattr(timer, "cancel"):
            timer.cancel()


class _ThreadTimer:
    def __init__(self, interval_s: float, callback: Callable[[], None]) -> None:
        self._timer = threading.Timer(interval_s, callback)

    def start(self) -> None:
        self._timer.start()

    def cancel(self) -> None:
        self._timer.cancel()
