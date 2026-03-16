"""Development button adapter that normalizes keyboard shortcuts to button events."""

from __future__ import annotations

from typing import Any, Callable


class KeyboardButton:
    def __init__(self, root: Any | None = None) -> None:
        self._root = root
        self._handler: Callable[[str], None] | None = None
        self.started = False

    def start(self, on_event: Callable[[str], None]) -> None:
        self._handler = on_event
        self.started = True

    def stop(self) -> None:
        self.started = False
        self._handler = None

    def dispatch(self, event_name: str) -> str | None:
        if self.started and self._handler is not None:
            self._handler(event_name)
        return "break"

    def bind_default_keys(self, root: Any | None = None) -> None:
        target = root or self._root
        if target is None:
            raise ValueError("KeyboardButton requires a root widget to bind keys")
        target.bind("<space>", lambda _event: self.dispatch("press"))
        target.bind("<Escape>", lambda _event: self.dispatch("long_press"))
